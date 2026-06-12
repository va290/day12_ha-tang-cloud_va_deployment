# LLM Compare Agent — Final Project (Day 12)

> Sản phẩm production hóa từ **Day01 Lab — LLM API Foundation**,
> áp theo format production-ready của `06-lab-complete`.

## Sản phẩm là gì?

API service cho phép:

1. **Chat có hội thoại** (`POST /ask`) — chatbot giữ conversation history
   (Day01 Task 4: streaming chatbot với history, chuyển từ in-memory sang Redis)
2. **So sánh model** (`POST /compare`) — gọi GPT-4o và GPT-4o-mini với cùng
   prompt, trả về response + latency + chi phí ước tính của từng model
   (Day01 Task 3: `compare_models` + bảng giá `COST_PER_1K_OUTPUT_TOKENS`)

Không có `OPENAI_API_KEY` → tự fallback **mock LLM** (chạy offline, $0).

## Nguồn gốc từ Day01

| Day01 (1 file CLI) | Sản phẩm này |
|--------------------|--------------|
| `call_openai()` / `call_openai_mini()` | `app/llm.py: call_llm()` — nhận messages/history |
| `compare_models()` | `POST /compare` — thêm cost cho cả 2 model + cost_ratio |
| `streaming_chatbot()` history in-memory | `POST /ask` — history trong Redis (stateless) |
| `retry_with_backoff()` | Bọc mọi lệnh gọi OpenAI API |
| `COST_PER_1K_OUTPUT_TOKENS` | Cost guard tính tiền thật theo bảng giá này |

## Production checklist (format 06-lab-complete)

- [x] Dockerfile multi-stage, non-root, < 500 MB
- [x] docker-compose: nginx LB + 3 agent instances + redis
- [x] API Key authentication (`X-API-Key`)
- [x] Rate limiting — Redis sliding window, 10 req/phút/user
- [x] Cost guard — daily budget, 402 khi vượt
- [x] Conversation history trong Redis (stateless, kill instance không mất hội thoại)
- [x] `GET /health` + `GET /ready` (ping Redis thật)
- [x] Graceful shutdown (SIGTERM)
- [x] Structured JSON logging
- [x] Config 12-factor từ environment variables
- [x] railway.toml — sẵn sàng deploy Railway

## Cấu trúc

```
my-production-agent/
├── app/
│   ├── main.py          # FastAPI app — endpoints + middleware
│   ├── llm.py           # ← Logic Day01: call_llm, compare_models, retry, pricing
│   ├── config.py        # 12-factor config
│   ├── auth.py          # API Key authentication
│   ├── rate_limiter.py  # Sliding window trên Redis
│   ├── cost_guard.py    # Budget protection trên Redis
│   ├── session_store.py # Conversation history trên Redis
│   └── redis_client.py  # Redis connection dùng chung
├── nginx.conf           # Load balancer config
├── Dockerfile
├── docker-compose.yml   # Nginx (:8081) → 3 agents → Redis
├── railway.toml
├── .env.example
└── requirements.txt
```

## Chạy Local

```bash
cp .env.example .env.local
docker compose up --build

# Health (qua nginx, port 8081)
curl http://localhost:8081/health

API_KEY=$(grep AGENT_API_KEY .env.local | cut -d= -f2)

# Chat multi-turn
curl -X POST http://localhost:8081/ask \
     -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
     -d '{"question": "Temperature trong LLM là gì?"}'
# → lấy session_id từ response, gửi lại để tiếp tục hội thoại

# So sánh GPT-4o vs GPT-4o-mini (tính năng chính từ Day01)
curl -X POST http://localhost:8081/compare \
     -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
     -d '{"prompt": "Giải thích temperature vs top_p trong 1 câu"}'
```

## Kiểm tra production readiness

```bash
python check_production_ready.py   # phải pass 20/20
```

## Deploy Railway

```bash
railway init --name llm-compare-agent
railway add --database redis
railway add --service agent \
  --variables "ENVIRONMENT=production" \
  --variables "AGENT_API_KEY=$(openssl rand -hex 16)" \
  --variables 'REDIS_URL=${{Redis.REDIS_URL}}'
railway up --service agent --detach
railway domain --service agent
```

(Muốn dùng OpenAI thật: thêm `--variables "OPENAI_API_KEY=..."`)
