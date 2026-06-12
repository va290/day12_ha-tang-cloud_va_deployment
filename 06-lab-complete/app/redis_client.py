"""
Redis client dùng chung cho rate limiter, cost guard và session store.

Stateless design: mọi state nằm trong Redis để scale nhiều instances.
Nếu REDIS_URL không được set (dev local không có Redis), các module
sẽ tự fallback sang in-memory store (KHÔNG dùng cho production).
"""
import logging

import redis

from app.config import settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def get_redis() -> redis.Redis | None:
    """Trả về Redis client (lazy init), hoặc None nếu REDIS_URL không set."""
    global _client
    if not settings.redis_url:
        return None
    if _client is None:
        _client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _client


def redis_ok() -> bool:
    """Ping Redis — dùng cho /ready và /health."""
    r = get_redis()
    if r is None:
        return False
    try:
        return bool(r.ping())
    except Exception:
        return False
