"""
Cost Guard — Bảo vệ budget LLM, tracking trong Redis.

- Mỗi user (API key) có daily budget (DAILY_BUDGET_USD)
- Chi phí tính bằng bảng giá thật của Day01 (COST_PER_1K_OUTPUT_TOKENS)
- Spending lưu trong Redis theo key cost:{user}:{ngày} → đúng khi scale
- Vượt budget → 402 Payment Required
- Fallback in-memory khi không có Redis (chỉ cho dev local)
"""
import time

from fastapi import HTTPException

from app.config import settings
from app.redis_client import get_redis

_local_costs: dict[str, float] = {}


def _day_key(user_id: str) -> str:
    return f"cost:{user_id}:{time.strftime('%Y-%m-%d')}"


def get_spent(user_id: str) -> float:
    """Tổng chi tiêu hôm nay của user (USD)."""
    r = get_redis()
    key = _day_key(user_id)
    if r is not None:
        return float(r.get(key) or 0.0)
    return _local_costs.get(key, 0.0)


def check_budget(user_id: str) -> None:
    """Raise 402 nếu user đã vượt daily budget."""
    spent = get_spent(user_id)
    if spent >= settings.daily_budget_usd:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Daily budget exceeded",
                "used_usd": round(spent, 4),
                "budget_usd": settings.daily_budget_usd,
                "resets_at": "midnight UTC",
            },
        )


def record_cost(user_id: str, cost_usd: float) -> float:
    """Ghi nhận chi phí (USD) sau khi gọi LLM. Trả về tổng đã dùng hôm nay."""
    r = get_redis()
    key = _day_key(user_id)
    if r is not None:
        total = float(r.incrbyfloat(key, cost_usd))
        r.expire(key, 2 * 24 * 3600)  # giữ 2 ngày rồi tự xóa
        return total
    _local_costs[key] = _local_costs.get(key, 0.0) + cost_usd
    return _local_costs[key]
