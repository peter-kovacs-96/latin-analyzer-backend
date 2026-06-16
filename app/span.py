"""Per-request span-ID context variable.

Usage:
    sid = span.new()         # generate & store a new 8-char hex ID
    sid = span.current()     # read the ID (returns "-" if not set)
"""
from contextvars import ContextVar
import uuid

_var: ContextVar[str] = ContextVar("span_id", default="")


def new() -> str:
    """Generate a fresh span ID, store it, and return it."""
    sid = uuid.uuid4().hex[:8]
    _var.set(sid)
    return sid


def current() -> str:
    """Return the current span ID, or '-' when none has been set."""
    return _var.get() or "-"
