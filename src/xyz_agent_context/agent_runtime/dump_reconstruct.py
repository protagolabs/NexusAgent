"""
Reconstruct discrete LLM call request/response pairs from a stream of
Claude Agent SDK message objects.

Streaming SDKs abstract away the underlying HTTP call boundaries. This module
reconstructs them post-hoc by walking the recorded events:

    - initial_request.messages              ── first request ──>
    - AssistantMessage (text/thinking/tool_use)   <── first response ──
    - UserMessage (tool_result)             ── second request ──>
    - AssistantMessage ...                        <── second response ──
    - ...

Each AssistantMessage terminates one logical LLM call. The running messages
list is augmented between calls so each reconstructed request reflects what
the model saw at that point in time.

Input events are dicts produced by `message_to_dict` in the SDK wrapper. This
module is a pure function with no imports from the SDK, to keep it testable.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional


def _event_type(ev: Dict[str, Any]) -> str:
    """Extract a normalised type tag from a recorded event dict."""
    return str(
        ev.get("_type")
        or ev.get("type")
        or ev.get("_class")
        or ""
    ).lower()


def _is_assistant(ev: Dict[str, Any]) -> bool:
    t = _event_type(ev)
    if "assistantmessage" in t or t == "assistant":
        return True
    return ev.get("role") == "assistant" and "content" in ev


def _is_user_tool_result(ev: Dict[str, Any]) -> bool:
    t = _event_type(ev)
    if "usermessage" in t or t == "user":
        content = ev.get("content")
        if isinstance(content, list):
            # Accept either explicit `type: tool_result` or the field-shape
            # fallback used when the SDK stripped the type attribute during
            # serialization (tool_result blocks always have `tool_use_id`).
            return any(
                isinstance(b, dict)
                and (b.get("type") == "tool_result" or "tool_use_id" in b)
                for b in content
            )
    return False


def _is_result(ev: Dict[str, Any]) -> bool:
    t = _event_type(ev)
    return "resultmessage" in t or t == "result"


def _is_system(ev: Dict[str, Any]) -> bool:
    t = _event_type(ev)
    return "systemmessage" in t or t == "system"


def _extract_content(ev: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the content blocks list from a message-like event."""
    content = ev.get("content")
    if isinstance(content, list):
        return content
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def _extract_usage(ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return ev.get("usage") or ev.get("message_usage")


def _extract_stop_reason(ev: Dict[str, Any]) -> Optional[str]:
    return ev.get("stop_reason")


def reconstruct_calls(
    stream_events: List[Dict[str, Any]],
    initial_request: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Group stream events into logical LLM call pairs.

    Args:
        stream_events: list of dicts as recorded by the SDK wrapper.
        initial_request: {"system": str, "messages": [...], "tools": [...], "mcp_server_urls": {...}}

    Returns:
        List of {"call_index", "request", "response"} dicts, one per assistant turn.
        If no assistant messages are found, returns an empty list.
    """
    # Running messages mirror what the model would see at each request point.
    running_messages: List[Any] = list(copy.deepcopy(initial_request.get("messages", []) or []))
    system = initial_request.get("system")
    tools = initial_request.get("tools")

    calls: List[Dict[str, Any]] = []
    call_index = 0

    for ev in stream_events:
        if _is_system(ev):
            # SystemMessage is typically the session initialization announce;
            # not a call boundary.
            continue

        if _is_result(ev):
            # ResultMessage is end-of-conversation summary, not a call.
            continue

        if _is_assistant(ev):
            call_index += 1
            response_content = _extract_content(ev)
            response = {
                "stop_reason": _extract_stop_reason(ev),
                "usage": _extract_usage(ev),
                "content": response_content,
            }
            request = {
                "system": system,
                "messages": copy.deepcopy(running_messages),
                "tools": tools,
            }
            calls.append({
                "call_index": call_index,
                "request": request,
                "response": response,
            })
            # Append this assistant turn to running messages for the next call.
            running_messages.append({
                "role": "assistant",
                "content": response_content,
            })
            continue

        if _is_user_tool_result(ev):
            # Append tool_result user message to running messages; this will be
            # part of the next assistant call's request.
            running_messages.append({
                "role": "user",
                "content": _extract_content(ev),
            })
            continue

        # Unknown event type — ignored for reconstruction. The raw dict
        # remains available in stream_events.jsonl.

    return calls
