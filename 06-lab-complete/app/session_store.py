"""
Session Store — Conversation history trong Redis (stateless design).

Bất kỳ agent instance nào cũng đọc/ghi được session của user,
nên kill 1 instance không làm mất hội thoại.
Fallback in-memory khi không có Redis (chỉ cho dev local).
"""
import json
from datetime import datetime, timezone

from app.redis_client import get_redis

SESSION_TTL_SECONDS = 3600  # session hết hạn sau 1 giờ không hoạt động
MAX_MESSAGES = 20           # giữ tối đa 10 turns

_local_sessions: dict[str, dict] = {}


def load_session(session_id: str) -> dict:
    r = get_redis()
    if r is not None:
        data = r.get(f"session:{session_id}")
        return json.loads(data) if data else {}
    return _local_sessions.get(session_id, {})


def save_session(session_id: str, data: dict) -> None:
    r = get_redis()
    if r is not None:
        r.setex(f"session:{session_id}", SESSION_TTL_SECONDS, json.dumps(data))
    else:
        _local_sessions[session_id] = data


def delete_session(session_id: str) -> None:
    r = get_redis()
    if r is not None:
        r.delete(f"session:{session_id}")
    else:
        _local_sessions.pop(session_id, None)


def append_to_history(session_id: str, role: str, content: str) -> list:
    """Thêm message vào history, trả về history mới."""
    session = load_session(session_id)
    history = session.get("history", [])
    history.append({
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    session["history"] = history[-MAX_MESSAGES:]
    save_session(session_id, session)
    return session["history"]
