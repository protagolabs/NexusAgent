#!/usr/bin/env python3
"""
Unit tests for the conversation-dump feature.

Run:
    .venv/bin/python scripts/test_conversation_dump.py

Tests the building blocks in isolation (no DB / SDK required):
    1. disabled → everything is a no-op, no files created
    2. enabled → directory structure matches plan
    3. manifest.json schema
    4. reconstruct_calls for a single LLM call with no tools
    5. reconstruct_calls for a conversation with tool use
    6. on_mcp_call captures success and failure
    7. thinking blocks are preserved verbatim
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# -----------------------------------------------------------------------------
# pure imports (no DB)
# -----------------------------------------------------------------------------
from xyz_agent_context.agent_runtime.dump_context import (
    get_current_dump,
    set_current_dump,
    reset_current_dump,
)
from xyz_agent_context.agent_runtime.conversation_dump_service import (
    ConversationDumpService,
)
from xyz_agent_context.agent_runtime.dump_reconstruct import reconstruct_calls
from xyz_agent_context.agent_runtime.dump_trace_builder import build_trace_md


PASSED = 0
FAILED = 0


def ok(name: str) -> None:
    global PASSED
    PASSED += 1
    print(f"  ✓ {name}")


def fail(name: str, reason: str) -> None:
    global FAILED
    FAILED += 1
    print(f"  ✗ {name}: {reason}")


# -----------------------------------------------------------------------------
# Test 1 — disabled is no-op
# -----------------------------------------------------------------------------
async def test_disabled_is_noop():
    print("Test 1: disabled mode is fully no-op")
    os.environ.pop("CONVERSATION_DUMP_ENABLED", None)
    with tempfile.TemporaryDirectory() as td:
        svc = ConversationDumpService(
            agent_id="a", user_id="u", event_id="evt", base_dir=td
        )
        assert svc.enabled is False, "service should be disabled"
        await svc.start()
        await svc.snapshot_context(
            system_prompt="x", messages=[], mcp_urls={}
        )
        await svc.on_stream_event({"type": "assistant"})
        await svc.on_mcp_call(
            server_url="u", tool_name="t", args={}, output="o", latency_s=0.1
        )
        await svc.finalize()
        # No files should have been created
        files = list(Path(td).rglob("*"))
        if files:
            fail("no_files_when_disabled", f"found {files}")
            return
    ok("disabled service creates no files")


# -----------------------------------------------------------------------------
# Test 2 — enabled → directory structure
# -----------------------------------------------------------------------------
async def test_directory_structure():
    print("Test 2: enabled mode produces correct directory structure")
    os.environ["CONVERSATION_DUMP_ENABLED"] = "1"
    with tempfile.TemporaryDirectory() as td:
        svc = ConversationDumpService(
            agent_id="agent_x", user_id="user_y", event_id="evt_123", base_dir=td
        )
        assert svc.enabled is True
        await svc.start()
        d = svc.dump_dir
        if not d.exists():
            fail("dir_exists", f"not created: {d}")
            return
        if not (d / "context").is_dir():
            fail("context_dir", "missing")
            return
        if not (d / "llm_calls").is_dir():
            fail("llm_calls_dir", "missing")
            return
        if not (d / "mcp_calls").is_dir():
            fail("mcp_calls_dir", "missing")
            return
        if not (d / "manifest.json").is_file():
            fail("manifest_stub", "missing")
            return
        ok("directory tree created correctly")

        # Snapshot, stream event, mcp call, finalize
        await svc.snapshot_context(
            system_prompt="hello system",
            messages=[{"role": "user", "content": "hi"}],
            mcp_urls={"m": "http://x"},
            narrative_list=[],
            continuity_result={"is_continuous": True, "confidence": 0.9, "reason": "same"},
        )
        if not (d / "context" / "system_prompt.md").is_file():
            fail("system_prompt_md", "missing")
            return
        if not (d / "context" / "messages.json").is_file():
            fail("messages_json", "missing")
            return
        if not (d / "context" / "tools.json").is_file():
            fail("tools_json", "missing")
            return
        if not (d / "context" / "narrative.md").is_file():
            fail("narrative_md", "missing")
            return
        ok("context/* files written")

        await svc.on_stream_event({
            "_class": "AssistantMessage",
            "content": [{"type": "text", "text": "Hello back"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })
        jsonl = d / "llm_calls" / "stream_events.jsonl"
        if not jsonl.is_file():
            fail("stream_events_jsonl", "missing")
            return
        ok("stream_events.jsonl written")

        await svc.on_mcp_call(
            server_url="http://mcp", tool_name="search", args={"q": "a"},
            output="{result:1}", latency_s=0.25
        )
        mcp_files = list((d / "mcp_calls").glob("*.json"))
        if len(mcp_files) != 1:
            fail("mcp_call_file", f"got {mcp_files}")
            return
        ok("mcp_calls/NN_*.json written")

        await svc.finalize(
            final_output="Hello back",
            usage={"input_tokens": 10, "output_tokens": 5},
            execution_state=[{"type": "text"}],
        )
        if not (d / "llm_calls" / "reconstructed.json").is_file():
            fail("reconstructed", "missing")
            return
        if not (d / "llm_calls" / "execution_state.json").is_file():
            fail("execution_state", "missing")
            return
        if not (d / "trace.md").is_file():
            fail("trace_md", "missing")
            return
        ok("finalize writes reconstructed/execution_state/trace.md")

        manifest = json.loads((d / "manifest.json").read_text("utf-8"))
        required = {
            "schema_version", "agent_id", "user_id", "event_id",
            "started_at", "ended_at", "total_seconds", "status",
            "llm", "mcp", "prompt_sizes",
        }
        missing = required - set(manifest.keys())
        if missing:
            fail("manifest_schema", f"missing fields: {missing}")
            return
        if manifest["status"] != "completed":
            fail("manifest_status", f"got {manifest['status']}")
            return
        ok("manifest.json has all required fields")
    os.environ.pop("CONVERSATION_DUMP_ENABLED", None)


# -----------------------------------------------------------------------------
# Test 3 — reconstruct: single call, no tools
# -----------------------------------------------------------------------------
def test_reconstruct_single():
    print("Test 3: reconstruct_calls with a single assistant response")
    events = [
        {
            "_class": "AssistantMessage",
            "content": [{"type": "text", "text": "Hi there"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 3},
        }
    ]
    initial = {"system": "sys", "messages": [{"role": "user", "content": "hi"}], "tools": []}
    calls = reconstruct_calls(events, initial)
    if len(calls) != 1:
        fail("single_call_count", f"got {len(calls)}")
        return
    c = calls[0]
    if c["call_index"] != 1:
        fail("call_index", "wrong")
        return
    if c["request"]["system"] != "sys":
        fail("request_system", "wrong")
        return
    if c["response"]["stop_reason"] != "end_turn":
        fail("stop_reason", "wrong")
        return
    ok("single-call reconstruction")


# -----------------------------------------------------------------------------
# Test 4 — reconstruct with tool use
# -----------------------------------------------------------------------------
def test_reconstruct_with_tools():
    print("Test 4: reconstruct_calls with 2 tool turns (3 LLM calls total)")
    events = [
        # Call 1 response: assistant uses a tool
        {
            "_class": "AssistantMessage",
            "content": [
                {"type": "thinking", "thinking": "Let me check..."},
                {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "x"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 20, "output_tokens": 5},
        },
        # Client executes tool; sdk emits UserMessage with tool_result
        {
            "_class": "UserMessage",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "result-1"}],
        },
        # Call 2 response: assistant uses another tool
        {
            "_class": "AssistantMessage",
            "content": [
                {"type": "tool_use", "id": "t2", "name": "fetch", "input": {"id": 1}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 30, "output_tokens": 4},
        },
        # Another tool result
        {
            "_class": "UserMessage",
            "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "result-2"}],
        },
        # Call 3: final answer
        {
            "_class": "AssistantMessage",
            "content": [{"type": "text", "text": "Done."}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 40, "output_tokens": 2},
        },
    ]
    initial = {"system": "S", "messages": [{"role": "user", "content": "Q"}]}
    calls = reconstruct_calls(events, initial)
    if len(calls) != 3:
        fail("call_count", f"expected 3 got {len(calls)}")
        return
    # First call's request should have 1 user message
    if len(calls[0]["request"]["messages"]) != 1:
        fail("call1_request_len", f"got {len(calls[0]['request']['messages'])}")
        return
    # Second call's request should have user + assistant(tool_use) + user(tool_result) = 3
    if len(calls[1]["request"]["messages"]) != 3:
        fail("call2_request_len", f"got {len(calls[1]['request']['messages'])}")
        return
    # Third call's request should have 5 messages (user + a + u + a + u)
    if len(calls[2]["request"]["messages"]) != 5:
        fail("call3_request_len", f"got {len(calls[2]['request']['messages'])}")
        return
    ok("3-call reconstruction with tool boundaries")

    # Thinking preserved verbatim
    blocks = calls[0]["response"]["content"]
    thinking_blocks = [b for b in blocks if b.get("type") == "thinking"]
    if not thinking_blocks or thinking_blocks[0]["thinking"] != "Let me check...":
        fail("thinking_preserved", "lost")
        return
    ok("thinking blocks preserved verbatim")


# -----------------------------------------------------------------------------
# Test 5 — mcp call with error
# -----------------------------------------------------------------------------
async def test_mcp_error_capture():
    print("Test 5: on_mcp_call captures errors")
    os.environ["CONVERSATION_DUMP_ENABLED"] = "1"
    with tempfile.TemporaryDirectory() as td:
        svc = ConversationDumpService(
            agent_id="a", user_id="u", event_id="evt", base_dir=td
        )
        await svc.start()
        await svc.on_mcp_call(
            server_url="http://mcp", tool_name="bad", args={"k": 1},
            output=None, latency_s=0.5, error="ConnectionError('boom')"
        )
        files = list((svc.dump_dir / "mcp_calls").glob("*.json"))
        if len(files) != 1:
            fail("mcp_file_count", f"got {len(files)}")
            return
        data = json.loads(files[0].read_text("utf-8"))
        if data.get("error") != "ConnectionError('boom')":
            fail("error_preserved", f"got {data.get('error')}")
            return
        if data.get("latency_seconds") != 0.5:
            fail("latency_preserved", f"got {data.get('latency_seconds')}")
            return
        ok("mcp error is captured")
    os.environ.pop("CONVERSATION_DUMP_ENABLED", None)


# -----------------------------------------------------------------------------
# Test 6 — trace.md builder produces valid markdown
# -----------------------------------------------------------------------------
def test_trace_md_renders():
    print("Test 6: build_trace_md produces non-empty structured markdown")
    manifest = {
        "event_id": "evt_1",
        "agent_id": "a",
        "user_id": "u",
        "started_at": "2026-04-14T10:00:00Z",
        "total_seconds": 3.2,
        "user_input_preview": "Hello",
        "final_output_preview": "Hi",
        "llm": {"call_count": 1, "usage": {"input_tokens": 10, "output_tokens": 5}},
        "mcp": {"call_count": 0},
        "per_step_timing": {"step_3_agent_loop": 1.5},
    }
    snapshot = {
        "system_prompt": "You are an assistant",
        "narrative_list": [],
        "mcp_urls": {"m": "http://x"},
        "continuity_result": {"is_continuous": True, "confidence": 0.9, "reason": "same"},
    }
    calls = [{
        "call_index": 1,
        "request": {},
        "response": {
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [
                {"type": "thinking", "thinking": "Thinking..."},
                {"type": "text", "text": "Hi"},
            ],
        },
    }]
    md = build_trace_md(
        manifest=manifest, snapshot=snapshot,
        reconstructed_calls=calls, mcp_summary=[],
        final_output="Hi", include_thinking=True,
    )
    required_sections = [
        "# Conversation Trace", "## 1. Input", "## 2. Context Assembly",
        "## 3. System Prompt", "## 4. LLM Call Sequence", "## 6. Final Output",
    ]
    for sec in required_sections:
        if sec not in md:
            fail("trace_section", f"missing {sec}")
            return
    if "Thinking..." not in md:
        fail("thinking_in_trace", "not included")
        return
    ok("trace.md contains all required sections and full thinking")


# -----------------------------------------------------------------------------
# Test 7 — thinking can be omitted via env
# -----------------------------------------------------------------------------
def test_thinking_omitted():
    print("Test 7: include_thinking=False omits thinking content")
    manifest = {
        "event_id": "e", "agent_id": "a", "user_id": "u",
        "llm": {"call_count": 1}, "mcp": {"call_count": 0},
    }
    snapshot = {"system_prompt": "", "narrative_list": [], "mcp_urls": {}}
    calls = [{
        "call_index": 1, "request": {},
        "response": {
            "stop_reason": "end_turn",
            "usage": {},
            "content": [{"type": "thinking", "thinking": "SECRET"}],
        },
    }]
    md = build_trace_md(
        manifest=manifest, snapshot=snapshot, reconstructed_calls=calls,
        mcp_summary=[], include_thinking=False,
    )
    if "SECRET" in md:
        fail("thinking_omission", "secret leaked")
        return
    if "omitted" not in md:
        fail("omission_marker", "marker missing")
        return
    ok("thinking omitted when include_thinking=False")


# -----------------------------------------------------------------------------
# Driver
# -----------------------------------------------------------------------------
async def main():
    print("=" * 60)
    print("Conversation Dump — Unit Tests")
    print("=" * 60)

    await test_disabled_is_noop()
    await test_directory_structure()
    test_reconstruct_single()
    test_reconstruct_with_tools()
    await test_mcp_error_capture()
    test_trace_md_renders()
    test_thinking_omitted()

    print()
    print("=" * 60)
    print(f"Result: {PASSED} passed, {FAILED} failed")
    print("=" * 60)
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    asyncio.run(main())
