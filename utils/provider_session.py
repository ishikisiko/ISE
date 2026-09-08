"""Conversation-scoped routing metadata for provider requests.

Session identifiers are opaque, never credentials. ContextVar prevents shared
clients from leaking one request's routing identity into another request.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
from uuid import uuid4


_SESSION: ContextVar[str | None] = ContextVar("ise_provider_session", default=None)


@contextmanager
def provider_session(conversation_id: str | None = None):
    raw = str(conversation_id or uuid4().hex)
    value = "ise-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    token = _SESSION.set(value)
    try:
        yield value
    finally:
        _SESSION.reset(token)


def with_provider_session(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with provider_session(kwargs.get("conversation_id")):
            return function(*args, **kwargs)
    return wrapped


def provider_headers(provider: str, fallback_session: str) -> dict[str, str]:
    if provider != "opencode-go":
        return {}
    return {
        "User-Agent": "ISE/1.0 (agentic-search)",
        "x-opencode-session": _SESSION.get() or fallback_session,
    }
