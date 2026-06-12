"""
LLM Provider — port từ Day01 Lab (LLM API Foundation).

Giữ nguyên các concept của Day01:
  - call_openai / call_openai_mini → call_llm (nhận messages để có history)
  - compare_models: so sánh GPT-4o vs GPT-4o-mini (response, latency, cost)
  - retry_with_backoff: exponential backoff khi API lỗi
  - COST_PER_1K_OUTPUT_TOKENS: bảng giá ước tính chi phí

Khi không có OPENAI_API_KEY → dùng mock (chạy offline, không tốn tiền),
giống convention của các lab trong khoá.
"""
import time
import random
from typing import Any, Callable

from app.config import settings

# Bảng giá từ Day01 — USD / 1K output tokens
COST_PER_1K_OUTPUT_TOKENS = {
    "gpt-4o": 0.010,
    "gpt-4o-mini": 0.0006,
}

OPENAI_MODEL = "gpt-4o"
OPENAI_MINI_MODEL = "gpt-4o-mini"


# ─────────────────────────────────────────────────────────
# Retry with exponential backoff (Day01 — Bonus Task A)
# ─────────────────────────────────────────────────────────
def retry_with_backoff(
    fn: Callable,
    max_retries: int = 3,
    base_delay: float = 0.1,
) -> Any:
    """Gọi fn(), retry tối đa max_retries lần với delay tăng theo cấp số nhân."""
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                time.sleep(base_delay * (2 ** attempt))
    raise last_exception


# ─────────────────────────────────────────────────────────
# Mock LLM — chạy offline khi không có API key
# ─────────────────────────────────────────────────────────
_MOCK_STYLES = {
    OPENAI_MODEL: {
        "latency": (0.5, 0.9),   # GPT-4o chậm hơn nhưng trả lời kỹ hơn
        "template": (
            "[mock gpt-4o] Câu trả lời chi tiết cho: \"{q}\". "
            "Trong production với OPENAI_API_KEY, đây là response thật từ GPT-4o "
            "— model mạnh hơn, chi phí ~16x so với mini."
        ),
    },
    OPENAI_MINI_MODEL: {
        "latency": (0.15, 0.35),  # mini nhanh và rẻ
        "template": "[mock gpt-4o-mini] Trả lời ngắn gọn cho: \"{q}\".",
    },
}


def _mock_completion(messages: list[dict], model: str) -> tuple[str, float]:
    style = _MOCK_STYLES.get(model, _MOCK_STYLES[OPENAI_MINI_MODEL])
    time.sleep(random.uniform(*style["latency"]))  # giả lập latency thật
    question = messages[-1]["content"] if messages else ""
    start = time.time()
    text = style["template"].format(q=question[:120])
    return text, time.time() - start + random.uniform(*style["latency"])


# ─────────────────────────────────────────────────────────
# call_llm (Day01 call_openai, mở rộng nhận messages/history)
# ─────────────────────────────────────────────────────────
def call_llm(
    messages: list[dict],
    model: str = OPENAI_MINI_MODEL,
    temperature: float = 0.7,
    top_p: float = 0.9,
    max_tokens: int = 256,
) -> tuple[str, float]:
    """
    Gọi Chat Completions API với conversation history, trả về (text, latency).
    Tự fallback sang mock nếu không có OPENAI_API_KEY.
    """
    if not settings.openai_api_key:
        return _mock_completion(messages, model)

    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)

    def _do_call():
        start = time.time()
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content, time.time() - start

    return retry_with_backoff(_do_call)


# ─────────────────────────────────────────────────────────
# Cost estimate (Day01 — heuristic 0.75 words ≈ 1 token)
# ─────────────────────────────────────────────────────────
def estimate_cost(text: str, model: str) -> float:
    tokens = len(text.split()) / 0.75
    price = COST_PER_1K_OUTPUT_TOKENS.get(model, COST_PER_1K_OUTPUT_TOKENS[OPENAI_MINI_MODEL])
    return (tokens / 1000) * price


# ─────────────────────────────────────────────────────────
# compare_models (Day01 — Task 3, mở rộng: cost cho cả 2 model)
# ─────────────────────────────────────────────────────────
def compare_models(prompt: str) -> dict:
    """Gọi cả GPT-4o và GPT-4o-mini với cùng prompt, so sánh kết quả."""
    messages = [{"role": "user", "content": prompt}]
    gpt4o_response, gpt4o_latency = call_llm(messages, model=OPENAI_MODEL)
    mini_response, mini_latency = call_llm(messages, model=OPENAI_MINI_MODEL)

    gpt4o_cost = estimate_cost(gpt4o_response, OPENAI_MODEL)
    mini_cost = estimate_cost(mini_response, OPENAI_MINI_MODEL)

    return {
        "gpt4o_response": gpt4o_response,
        "mini_response": mini_response,
        "gpt4o_latency": round(gpt4o_latency, 3),
        "mini_latency": round(mini_latency, 3),
        "gpt4o_cost_estimate": round(gpt4o_cost, 6),
        "mini_cost_estimate": round(mini_cost, 6),
        "cost_ratio": round(gpt4o_cost / mini_cost, 1) if mini_cost else None,
        "total_cost_estimate": round(gpt4o_cost + mini_cost, 6),
    }
