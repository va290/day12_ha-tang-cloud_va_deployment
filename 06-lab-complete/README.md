# Lab 12 — Complete Production Agent

Kết hợp TẤT CẢ những gì đã học trong 1 project hoàn chỉnh.

## Checklist Deliverable

- [x] Dockerfile (multi-stage, < 500 MB)
- [x] docker-compose.yml (nginx LB + 3 agent instances + redis)
- [x] .dockerignore
- [x] Health check endpoint (`GET /health`)
- [x] Readiness endpoint (`GET /ready` — ping Redis thật)
- [x] API Key authentication
- [x] Rate limiting (Redis sliding window — đúng khi scale)
- [x] Cost guard (budget tracking trong Redis)
- [x] Conversation history (session lưu Redis — stateless)
- [x] Config từ environment variables
- [x] Structured logging (JSON)
- [x] Graceful shutdown
- [x] Public URL ready (Railway / Render config)

---

## Cấu Trúc

```
06-lab-complete/
├── app/
│   ├── main.py          # Entry point — kết hợp tất cả
│   ├── config.py        # 12-factor config
│   ├── auth.py          # API Key authentication
│   ├── rate_limiter.py  # Sliding window trên Redis
│   ├── cost_guard.py    # Budget protection trên Redis
│   ├── session_store.py # Conversation history trên Redis
│   └── redis_client.py  # Redis connection dùng chung
├── utils/mock_llm.py    # Mock LLM (không cần API key)
├── nginx.conf           # Load balancer config
├── Dockerfile           # Multi-stage, production-ready
├── docker-compose.yml   # Nginx → 3 agents → Redis
├── railway.toml         # Deploy Railway
├── render.yaml          # Deploy Render
├── .env.example         # Template (copy thành .env.local)
├── .dockerignore
└── requirements.txt
```

**Architecture:**

```
Client → Nginx (:8080) → Agent ×3 (stateless) → Redis (sessions, rate limit, cost)
```

---

## Chạy Local

```bash
# 1. Setup
cp .env.example .env.local

# 2. Chạy full stack (nginx + 3 agents + redis)
docker compose up --build

# 3. Health check (qua nginx, port 8080)
curl http://localhost:8080/health

# 4. Lấy API key từ .env.local, test endpoint
API_KEY=$(grep AGENT_API_KEY .env.local | cut -d= -f2)
curl -H "X-API-Key: $API_KEY" \
     -X POST http://localhost:8080/ask \
     -H "Content-Type: application/json" \
     -d '{"question": "What is deployment?"}'
```

### Test conversation history (stateless)

```bash
# Turn 1 — lấy session_id từ response
curl -s -H "X-API-Key: $API_KEY" -X POST http://localhost:8080/ask \
     -H "Content-Type: application/json" \
     -d '{"question": "What is Docker?"}'

# Turn 2 — gửi lại session_id, để ý "served_by" đổi instance
# nhưng "turn" vẫn tăng (state trong Redis, không trong memory!)
curl -s -H "X-API-Key: $API_KEY" -X POST http://localhost:8080/ask \
     -H "Content-Type: application/json" \
     -d '{"question": "Tell me more", "session_id": "<session_id_từ_turn_1>"}'

# Xem history
curl -s -H "X-API-Key: $API_KEY" \
     http://localhost:8080/chat/<session_id>/history
```

### Test rate limit (10 req/phút)

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" \
       -H "X-API-Key: $API_KEY" -X POST http://localhost:8080/ask \
       -H "Content-Type: application/json" \
       -d '{"question": "test"}'
done
# 10 request đầu: 200 — sau đó: 429
```

### Test load balancing & resilience

```bash
# Kill 1 agent instance — traffic tự chuyển sang 2 instances còn lại
docker kill $(docker ps -q --filter "name=agent" | head -1)
curl http://localhost:8080/health   # vẫn 200!
```

---

## Deploy Railway (< 5 phút)

```bash
# Cài Railway CLI
npm i -g @railway/cli

# Login và deploy
railway login
railway init
railway add --database redis        # Redis managed cho state
railway variables set AGENT_API_KEY=your-secret-key
railway up

# Nhận public URL!
railway domain
```

> Lưu ý: trên Railway/Render chỉ chạy 1 container (không có nginx),
> nhưng app vẫn stateless — set `REDIS_URL` trỏ tới Redis managed.

---

## Deploy Render

1. Push repo lên GitHub
2. Render Dashboard → New → Blueprint
3. Connect repo → Render đọc `render.yaml`
4. Set secrets: `OPENAI_API_KEY`, `AGENT_API_KEY`, `REDIS_URL` (Upstash free tier)
5. Deploy → Nhận URL!

---

## Kiểm Tra Production Readiness

```bash
python check_production_ready.py
```

Script này kiểm tra tất cả items trong checklist và báo cáo những gì còn thiếu.
