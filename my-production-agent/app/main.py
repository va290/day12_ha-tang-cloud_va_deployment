"""
LLM Compare Agent — Sản phẩm production hóa từ Day01 Lab (LLM API Foundation)
theo format Day12 / 06-lab-complete.

Chức năng (từ Day01):
  - POST /ask      → chatbot có conversation history (Day01 Task 4)
  - POST /compare  → so sánh GPT-4o vs GPT-4o-mini: response, latency, cost (Task 3)

Production checklist (Day12):
  ✅ Config từ environment (12-factor)
  ✅ Structured JSON logging
  ✅ API Key authentication            (app/auth.py)
  ✅ Rate limiting — Redis sliding window (app/rate_limiter.py)
  ✅ Cost guard — bảng giá Day01, budget trong Redis (app/cost_guard.py)
  ✅ Conversation history — Redis      (app/session_store.py)
  ✅ Stateless design — scale nhiều instances
  ✅ Health check + Readiness probe (ping Redis thật)
  ✅ Graceful shutdown (SIGTERM)
  ✅ Retry with backoff khi gọi LLM    (app/llm.py — Day01 Bonus A)
"""
import time
import signal
import logging
import json
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from app.config import settings
from app.auth import verify_api_key
from app.rate_limiter import check_rate_limit
from app.cost_guard import check_budget, record_cost, get_spent
from app.redis_client import redis_ok
from app.session_store import load_session, delete_session, append_to_history
from app.llm import call_llm, compare_models, estimate_cost

# ─────────────────────────────────────────────────────────
# Logging — JSON structured
# ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)

START_TIME = time.time()
INSTANCE_ID = f"instance-{uuid.uuid4().hex[:6]}"
_is_ready = False
_request_count = 0
_error_count = 0

# Day01: chatbot giữ 3 turns gần nhất khi gọi LLM
HISTORY_WINDOW_MESSAGES = 6

# ─────────────────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _is_ready
    logger.info(json.dumps({
        "event": "startup",
        "instance": INSTANCE_ID,
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "llm": "openai" if settings.openai_api_key else "mock",
        "storage": "redis" if redis_ok() else "in-memory (not scalable!)",
    }))
    _is_ready = True
    logger.info(json.dumps({"event": "ready", "instance": INSTANCE_ID}))

    yield

    # Graceful shutdown: /ready trả 503 → LB ngừng route, request đang chạy được hoàn thành
    _is_ready = False
    logger.info(json.dumps({"event": "shutdown", "instance": INSTANCE_ID}))

# ─────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

@app.middleware("http")
async def request_middleware(request: Request, call_next):
    global _request_count, _error_count
    start = time.time()
    _request_count += 1
    try:
        response: Response = await call_next(request)
        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if "server" in response.headers:
            del response.headers["server"]
        duration = round((time.time() - start) * 1000, 1)
        logger.info(json.dumps({
            "event": "request",
            "instance": INSTANCE_ID,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ms": duration,
        }))
        return response
    except Exception:
        _error_count += 1
        raise

# ─────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = Field(
        None, description="Gửi lại session_id để tiếp tục hội thoại (multi-turn)")

class AskResponse(BaseModel):
    session_id: str
    question: str
    answer: str
    turn: int
    model: str
    latency_seconds: float
    cost_estimate_usd: float
    served_by: str
    timestamp: str

class CompareRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)

# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────

@app.get("/", tags=["Info"])
def root():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "origin": "Productionized from Day01 Lab — LLM API Foundation",
        "endpoints": {
            "ask": "POST /ask (requires X-API-Key)",
            "compare": "POST /compare — GPT-4o vs GPT-4o-mini (requires X-API-Key)",
            "history": "GET /chat/{session_id}/history (requires X-API-Key)",
            "health": "GET /health",
            "ready": "GET /ready",
        },
    }


@app.post("/ask", response_model=AskResponse, tags=["Agent"])
async def ask_agent(
    body: AskRequest,
    request: Request,
    _key: str = Depends(verify_api_key),
):
    """
    Chatbot có conversation history (Day01 Task 4, history trong Redis).
    Gửi lại `session_id` từ response trước để tiếp tục hội thoại.
    """
    user_id = _key[:8]

    check_rate_limit(user_id)
    check_budget(user_id)

    session_id = body.session_id or str(uuid.uuid4())
    history = append_to_history(session_id, "user", body.question)

    logger.info(json.dumps({
        "event": "agent_call",
        "instance": INSTANCE_ID,
        "session": session_id,
        "q_len": len(body.question),
        "client": str(request.client.host) if request.client else "unknown",
    }))

    # Day01: chỉ gửi 3 turns gần nhất cho LLM
    messages = [{"role": m["role"], "content": m["content"]}
                for m in history[-HISTORY_WINDOW_MESSAGES:]]
    answer, latency = call_llm(messages, model=settings.llm_model)

    history = append_to_history(session_id, "assistant", answer)

    cost = estimate_cost(answer, settings.llm_model)
    record_cost(user_id, cost)

    return AskResponse(
        session_id=session_id,
        question=body.question,
        answer=answer,
        turn=len([m for m in history if m["role"] == "user"]),
        model=settings.llm_model,
        latency_seconds=round(latency, 3),
        cost_estimate_usd=round(cost, 6),
        served_by=INSTANCE_ID,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.post("/compare", tags=["Agent"])
async def compare(
    body: CompareRequest,
    _key: str = Depends(verify_api_key),
):
    """
    So sánh GPT-4o vs GPT-4o-mini với cùng prompt (Day01 Task 3):
    response, latency, chi phí ước tính và tỉ lệ giá giữa 2 model.
    """
    user_id = _key[:8]

    check_rate_limit(user_id)
    check_budget(user_id)

    logger.info(json.dumps({
        "event": "compare_call",
        "instance": INSTANCE_ID,
        "q_len": len(body.prompt),
    }))

    result = compare_models(body.prompt)
    record_cost(user_id, result["total_cost_estimate"])

    return {
        "prompt": body.prompt,
        **result,
        "served_by": INSTANCE_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/chat/{session_id}/history", tags=["Agent"])
def get_history(session_id: str, _key: str = Depends(verify_api_key)):
    """Xem conversation history của một session."""
    session = load_session(session_id)
    if not session:
        raise HTTPException(404, f"Session {session_id} not found or expired")
    return {
        "session_id": session_id,
        "messages": session.get("history", []),
        "count": len(session.get("history", [])),
    }


@app.delete("/chat/{session_id}", tags=["Agent"])
def remove_session(session_id: str, _key: str = Depends(verify_api_key)):
    """Xóa session."""
    delete_session(session_id)
    return {"deleted": session_id}


@app.get("/health", tags=["Operations"])
def health():
    """Liveness probe. Platform restarts container if this fails."""
    storage_ok = redis_ok()
    return {
        "status": "ok" if (storage_ok or not settings.redis_url) else "degraded",
        "instance": INSTANCE_ID,
        "version": settings.app_version,
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "checks": {
            "llm": "mock" if not settings.openai_api_key else "openai",
            "redis": "connected" if storage_ok else "unavailable",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready", tags=["Operations"])
def ready():
    """Readiness probe. Load balancer stops routing here if not ready."""
    if not _is_ready:
        raise HTTPException(503, "Not ready")
    if settings.redis_url and not redis_ok():
        raise HTTPException(503, "Redis not available")
    return {"ready": True, "instance": INSTANCE_ID}


@app.get("/metrics", tags=["Operations"])
def metrics(_key: str = Depends(verify_api_key)):
    """Basic metrics (protected)."""
    user_id = _key[:8]
    spent = get_spent(user_id)
    return {
        "instance": INSTANCE_ID,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "error_count": _error_count,
        "daily_cost_usd": round(spent, 4),
        "daily_budget_usd": settings.daily_budget_usd,
        "budget_used_pct": round(spent / settings.daily_budget_usd * 100, 1),
    }


# ─────────────────────────────────────────────────────────
# Graceful Shutdown
# Khi chạy qua uvicorn CLI, uvicorn tự handle SIGTERM:
# ngừng nhận request mới → hoàn thành request đang chạy →
# chạy lifespan shutdown. Handler dưới đây log lại signal
# cho path chạy trực tiếp `python app/main.py`.
# ─────────────────────────────────────────────────────────
def _handle_signal(signum, _frame):
    logger.info(json.dumps({"event": "signal", "signum": signum,
                            "instance": INSTANCE_ID}))

signal.signal(signal.SIGTERM, _handle_signal)


if __name__ == "__main__":
    logger.info(f"Starting {settings.app_name} on {settings.host}:{settings.port}")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        timeout_graceful_shutdown=30,
    )
