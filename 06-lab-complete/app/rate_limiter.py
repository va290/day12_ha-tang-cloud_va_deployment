"""
Rate Limiter — Sliding Window trên Redis.

Stateless: counter nằm trong Redis (sorted set timestamp), nên limit
đúng kể cả khi chạy nhiều agent instances sau load balancer.
Fallback in-memory khi không có Redis (chỉ cho dev local).
"""
import time
import uuid
from collections import defaultdict, deque

from fastapi import HTTPException

from app.config import settings
from app.redis_client import get_redis

WINDOW_SECONDS = 60

# Fallback khi không có Redis — không chia sẻ giữa các instances!
_local_windows: dict[str, deque] = defaultdict(deque)


def _raise_429(limit: int, retry_after: int):
    raise HTTPException(
        status_code=429,
        detail={
            "error": "Rate limit exceeded",
            "limit": limit,
            "window_seconds": WINDOW_SECONDS,
            "retry_after_seconds": retry_after,
        },
        headers={
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
            "Retry-After": str(retry_after),
        },
    )


def check_rate_limit(user_id: str) -> None:
    """Sliding window: đếm request trong 60s gần nhất. Raise 429 nếu vượt."""
    limit = settings.rate_limit_per_minute
    now = time.time()
    r = get_redis()

    if r is not None:
        key = f"ratelimit:{user_id}"
        member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, now - WINDOW_SECONDS)
        pipe.zadd(key, {member: now})
        pipe.zcard(key)
        pipe.expire(key, WINDOW_SECONDS + 10)
        _, _, count, _ = pipe.execute()
        if count > limit:
            # Request bị chặn không được tính vào window
            r.zrem(key, member)
            oldest = r.zrange(key, 0, 0, withscores=True)
            retry_after = (
                int(oldest[0][1] + WINDOW_SECONDS - now) + 1 if oldest else WINDOW_SECONDS
            )
            _raise_429(limit, retry_after)
        return

    # Fallback in-memory
    window = _local_windows[user_id]
    while window and window[0] < now - WINDOW_SECONDS:
        window.popleft()
    if len(window) >= limit:
        retry_after = int(window[0] + WINDOW_SECONDS - now) + 1
        _raise_429(limit, retry_after)
    window.append(now)
