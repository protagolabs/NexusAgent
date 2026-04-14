"""
ContextVar bridge for the conversation-dump feature.

Why a ContextVar:
    The dump service must be reachable from deep layers (MCP executor,
    SDK wrappers) that do not have `ctx: RunContext` in scope. A ContextVar
    is the cleanest way to thread optional state through async code without
    changing function signatures, and it propagates correctly across
    `asyncio.gather` / `asyncio.create_task`.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .conversation_dump_service import ConversationDumpService

_CURRENT_DUMP: ContextVar[Optional["ConversationDumpService"]] = ContextVar(
    "conversation_dump", default=None
)


def get_current_dump() -> Optional["ConversationDumpService"]:
    """Return the active dump service for this async context, or None."""
    return _CURRENT_DUMP.get()


def set_current_dump(svc: Optional["ConversationDumpService"]) -> Token:
    """Set the current dump service. Returns a Token to pass to reset."""
    return _CURRENT_DUMP.set(svc)


def reset_current_dump(token: Token) -> None:
    """Reset the dump contextvar using the token returned by set_current_dump."""
    _CURRENT_DUMP.reset(token)
