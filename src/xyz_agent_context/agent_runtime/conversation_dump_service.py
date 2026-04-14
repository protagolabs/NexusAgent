"""
ConversationDumpService — per-turn conversation archive.

Writes a self-contained directory for every Event (one user-agent turn):

    data/conversation_dumps/{agent_id}/{user_id}/{YYYYMMDD}/{HHMMSS}_{event_id}/
        manifest.json
        trace.md
        context/
            system_prompt.md
            messages.json
            tools.json
            narrative.md
        llm_calls/
            stream_events.jsonl          (append-only during agent loop)
            reconstructed.json           (written at finalize)
            execution_state.json         (written at finalize)
        mcp_calls/
            NN_{tool_name}.json          (one per MCP invocation)

Environment variables:
    CONVERSATION_DUMP_ENABLED       "1" to enable; default disabled → all no-op
    CONVERSATION_DUMP_DIR           root dir; default "data/conversation_dumps"
    CONVERSATION_DUMP_INCLUDE_THINKING  "1" (default) to keep full thinking blocks

When disabled the service is constructed but every method is a fast no-op — safe
to leave the setup/teardown hooks in place at zero cost.

All disk I/O is wrapped in asyncio.to_thread to avoid blocking the event loop.
A single asyncio.Lock serializes jsonl appends and the MCP call counter.

Failures never propagate: any I/O error is caught and logged at WARNING. The
agent runtime must continue unimpaired if dump fails.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Serialization helper
# ---------------------------------------------------------------------------

def _safe_default(obj: Any) -> Any:
    """json.dumps default= that tolerates arbitrary objects.

    Order of preference: pydantic model_dump → asdict-style dict → __dict__ → str.
    """
    # datetime / date
    if isinstance(obj, datetime):
        return obj.isoformat()
    # Enum
    if hasattr(obj, "value") and hasattr(obj.__class__, "__members__"):
        return obj.value
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json")
        except Exception:
            pass
    # Pydantic v1 / dataclass-like
    if hasattr(obj, "dict") and callable(obj.dict):
        try:
            return obj.dict()
        except Exception:
            pass
    # Generic
    if hasattr(obj, "__dict__"):
        try:
            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
        except Exception:
            pass
    return repr(obj)


def _dump_json(obj: Any, indent: int = 2) -> str:
    return json.dumps(obj, indent=indent, ensure_ascii=False, default=_safe_default)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ConversationDumpService:
    """
    One instance per AgentRuntime.run() invocation.

    Constructed unconditionally by agent_runtime.run(); if CONVERSATION_DUMP_ENABLED
    is not "1", self.enabled is False and every method returns immediately.
    """

    SCHEMA_VERSION = "1.0"

    def __init__(
        self,
        agent_id: str,
        user_id: str,
        event_id: Optional[str],
        base_dir: Optional[str] = None,
    ) -> None:
        self.enabled = os.getenv("CONVERSATION_DUMP_ENABLED", "") == "1"
        self.include_thinking = os.getenv("CONVERSATION_DUMP_INCLUDE_THINKING", "1") == "1"

        self.agent_id = agent_id
        self.user_id = user_id
        # event_id may be unknown at construction time; updated via set_event_id()
        self.event_id = event_id or "unknown"

        # Resolve base dir. Default is repo_root/data/conversation_dumps.
        if base_dir is None:
            base_dir = os.getenv("CONVERSATION_DUMP_DIR") or "data/conversation_dumps"
        self._base_dir = Path(base_dir)

        now = datetime.now(timezone.utc)
        self._started_at_wall = now
        self._started_at_mono = time.monotonic()

        date_part = now.strftime("%Y%m%d")
        time_part = now.strftime("%H%M%S")
        self._dir = (
            self._base_dir
            / agent_id
            / user_id
            / date_part
            / f"{time_part}_{self.event_id}"
        )

        self._lock = asyncio.Lock()
        self._mcp_counter = 0
        self._stream_event_count = 0
        self._initial_request: Optional[Dict[str, Any]] = None
        self._finalized = False

        # Per-step timing; populated by runtime via record_step_time().
        self._per_step_timing: Dict[str, float] = {}

        # Cached snapshot data (set during snapshot_context, used in trace.md).
        self._snapshot: Dict[str, Any] = {}

    # ------------------------------------------------------------------ utils

    def _noop(self) -> bool:
        return not self.enabled or self._finalized

    @property
    def dump_dir(self) -> Path:
        return self._dir

    def set_event_id(self, event_id: str) -> None:
        """Called once the Event is created in Step 0, so the directory name
        can incorporate the real event_id. Must be invoked before start()."""
        if self._noop() or not event_id:
            return
        self.event_id = event_id
        # Rebuild the directory path with the real event_id
        date_part = self._started_at_wall.strftime("%Y%m%d")
        time_part = self._started_at_wall.strftime("%H%M%S")
        self._dir = (
            self._base_dir
            / self.agent_id
            / self.user_id
            / date_part
            / f"{time_part}_{event_id}"
        )

    def record_step_time(self, step_name: str, seconds: float) -> None:
        if self._noop():
            return
        self._per_step_timing[step_name] = round(seconds, 4)

    # ------------------------------------------------------------------ start

    async def start(self) -> None:
        """Create the directory tree and an initial manifest stub."""
        if not self.enabled:
            return
        try:
            await asyncio.to_thread(self._mkdirs)
            await self._write_manifest_stub()
            logger.info(f"[ConversationDump] started: {self._dir}")
        except Exception as exc:
            logger.warning(f"[ConversationDump] start() failed, disabling: {exc}")
            self.enabled = False

    def _mkdirs(self) -> None:
        (self._dir / "context").mkdir(parents=True, exist_ok=True)
        (self._dir / "llm_calls").mkdir(parents=True, exist_ok=True)
        (self._dir / "mcp_calls").mkdir(parents=True, exist_ok=True)

    async def _write_manifest_stub(self) -> None:
        stub = {
            "schema_version": self.SCHEMA_VERSION,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "event_id": self.event_id,
            "started_at": self._started_at_wall.isoformat(),
            "status": "in_progress",
        }
        await asyncio.to_thread(
            (self._dir / "manifest.json").write_text,
            _dump_json(stub),
            "utf-8",
        )

    # -------------------------------------------------------- context snapshot

    async def snapshot_context(
        self,
        *,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        mcp_urls: Dict[str, str],
        narrative_list: Optional[List[Any]] = None,
        continuity_result: Any = None,
        load_result: Any = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt_layers: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Write context/* files. Safe to call once per turn."""
        if self._noop():
            return
        try:
            self._snapshot = {
                "system_prompt": system_prompt,
                "messages": messages,
                "mcp_urls": mcp_urls,
                "narrative_list": narrative_list,
                "continuity_result": continuity_result,
                "load_result": load_result,
                "tools": tools,
                "system_prompt_layers": system_prompt_layers,
            }
            await asyncio.to_thread(self._write_context_files)
        except Exception as exc:
            logger.warning(f"[ConversationDump] snapshot_context() failed: {exc}")

    def _write_context_files(self) -> None:
        ctx_dir = self._dir / "context"

        # system_prompt.md — layered if layers provided, else single block
        # Use 5-backtick fences so nested ``` inside the prompt don't close
        # the wrapper early (system prompts often contain markdown code blocks).
        sp_md = ["# System Prompt", ""]
        layers = self._snapshot.get("system_prompt_layers")
        if layers:
            for i, layer in enumerate(layers, 1):
                name = layer.get("name", f"Layer {i}")
                content = layer.get("content", "")
                sp_md.append(f"## Layer {i}: {name} ({len(content)} chars)")
                sp_md.append("")
                sp_md.append("`````text")
                sp_md.append(content)
                sp_md.append("`````")
                sp_md.append("")
        sp_md.append(f"## Full Assembled ({len(self._snapshot['system_prompt'])} chars)")
        sp_md.append("")
        sp_md.append("`````text")
        sp_md.append(self._snapshot["system_prompt"])
        sp_md.append("`````")
        (ctx_dir / "system_prompt.md").write_text("\n".join(sp_md), "utf-8")

        # messages.json
        (ctx_dir / "messages.json").write_text(
            _dump_json(self._snapshot["messages"]), "utf-8"
        )

        # tools.json — tools + mcp_urls
        tools_payload = {
            "mcp_urls": self._snapshot.get("mcp_urls") or {},
            "tools": self._snapshot.get("tools"),  # may be None if not resolved
        }
        (ctx_dir / "tools.json").write_text(_dump_json(tools_payload), "utf-8")

        # narrative.md — narrative + continuity + load_result
        n_md = ["# Narrative & Module Context", ""]
        nl = self._snapshot.get("narrative_list") or []
        n_md.append(f"## Selected Narratives ({len(nl)})")
        for i, narr in enumerate(nl):
            nid = getattr(narr, "id", None) or (isinstance(narr, dict) and narr.get("id")) or "?"
            nname = getattr(narr, "name", None) or (isinstance(narr, dict) and narr.get("name")) or "?"
            ndesc = getattr(narr, "description", "") or (isinstance(narr, dict) and narr.get("description", "")) or ""
            n_md.append(f"- **{nid}** `{nname}` — {ndesc[:200]}")
        n_md.append("")

        cr = self._snapshot.get("continuity_result")
        if cr is not None:
            n_md.append("## Continuity Detection")
            n_md.append("")
            n_md.append("```json")
            n_md.append(_dump_json(cr))
            n_md.append("```")
            n_md.append("")

        lr = self._snapshot.get("load_result")
        if lr is not None:
            n_md.append("## Module Load Result")
            n_md.append("")
            n_md.append("```json")
            n_md.append(_dump_json(lr))
            n_md.append("```")

        (ctx_dir / "narrative.md").write_text("\n".join(n_md), "utf-8")

    # ------------------------------------------------------- stream / mcp

    def record_initial_request(self, payload: Dict[str, Any]) -> None:
        """Called by the SDK wrapper right before it enters the stream loop."""
        if self._noop():
            return
        self._initial_request = payload

    async def on_stream_event(self, event: Dict[str, Any]) -> None:
        """Append one streaming LLM event to stream_events.jsonl."""
        if self._noop():
            return
        async with self._lock:
            try:
                line = json.dumps(event, ensure_ascii=False, default=_safe_default)
                await asyncio.to_thread(self._append_line, "llm_calls/stream_events.jsonl", line)
                self._stream_event_count += 1
            except Exception as exc:
                logger.warning(f"[ConversationDump] on_stream_event failed: {exc}")

    def _append_line(self, rel_path: str, line: str) -> None:
        path = self._dir / rel_path
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")

    async def on_mcp_call(
        self,
        *,
        server_url: str,
        tool_name: str,
        args: Any,
        output: Any,
        latency_s: float,
        error: Optional[str] = None,
    ) -> None:
        """Write one mcp_calls/NN_{tool_name}.json."""
        if self._noop():
            return
        async with self._lock:
            self._mcp_counter += 1
            idx = self._mcp_counter
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in (tool_name or "unknown"))
        payload = {
            "call_index": idx,
            "server_url": server_url,
            "tool_name": tool_name,
            "args": args,
            "output": output,
            "latency_seconds": round(latency_s, 4),
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await asyncio.to_thread(
                (self._dir / "mcp_calls" / f"{idx:02d}_{safe_name}.json").write_text,
                _dump_json(payload),
                "utf-8",
            )
        except Exception as exc:
            logger.warning(f"[ConversationDump] on_mcp_call failed: {exc}")

    # ------------------------------------------------------------ finalize

    async def finalize(
        self,
        *,
        final_output: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
        execution_state: Any = None,
        extra_manifest: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write reconstructed.json, execution_state.json, trace.md, update manifest."""
        if self._noop():
            return
        try:
            # 1. Read back stream events (if any)
            stream_events = await asyncio.to_thread(self._read_stream_events)

            # 2. Reconstruct LLM calls (lazy import to avoid circular)
            from .dump_reconstruct import reconstruct_calls

            reconstructed = reconstruct_calls(
                stream_events=stream_events,
                initial_request=self._initial_request or {},
            )
            await asyncio.to_thread(
                (self._dir / "llm_calls" / "reconstructed.json").write_text,
                _dump_json(reconstructed),
                "utf-8",
            )

            # 3. Execution state snapshot
            if execution_state is not None:
                await asyncio.to_thread(
                    (self._dir / "llm_calls" / "execution_state.json").write_text,
                    _dump_json(execution_state),
                    "utf-8",
                )

            # 4. Backfill MCP calls from stream events / reconstructed data.
            #
            # Claude Agent SDK runs in a subprocess and talks to MCP servers
            # directly, bypassing utils/mcp_executor.py. That means the
            # mcp_calls/ dir is empty even when the model used many tools.
            # Extract tool_use/tool_result pairs from reconstructed calls and
            # materialise them as synthetic MCP records so the manifest and
            # trace.md reflect what actually happened.
            existing_count = self._mcp_counter
            if existing_count == 0:
                await asyncio.to_thread(
                    self._materialise_mcp_from_reconstructed,
                    reconstructed,
                    stream_events,
                )

            # 5. Backfill token usage from stream events if caller didn't
            # provide any. The Claude SDK surfaces token usage on ResultMessage
            # events which are already captured in stream_events.jsonl.
            if not usage:
                usage = _extract_usage_from_stream(stream_events)

            # 6. Manifest update
            ended_at = datetime.now(timezone.utc)
            total_seconds = time.monotonic() - self._started_at_mono

            # Read back MCP call summaries (now possibly including backfilled)
            mcp_dir = self._dir / "mcp_calls"
            mcp_files = sorted(mcp_dir.glob("*.json")) if mcp_dir.exists() else []
            mcp_summary = []
            for p in mcp_files:
                try:
                    data = json.loads(p.read_text("utf-8"))
                    mcp_summary.append({
                        "call_index": data.get("call_index"),
                        "tool_name": data.get("tool_name"),
                        "latency_seconds": data.get("latency_seconds"),
                        "error": data.get("error"),
                        "source": data.get("source", "executor"),
                    })
                except Exception:
                    pass

            manifest = {
                "schema_version": self.SCHEMA_VERSION,
                "agent_id": self.agent_id,
                "user_id": self.user_id,
                "event_id": self.event_id,
                "started_at": self._started_at_wall.isoformat(),
                "ended_at": ended_at.isoformat(),
                "total_seconds": round(total_seconds, 3),
                "status": "completed",
                "final_output_preview": (final_output or "")[:200],
                "user_input_preview": self._preview_user_input(),
                "per_step_timing": self._per_step_timing,
                "llm": {
                    "call_count": len(reconstructed),
                    "stream_event_count": self._stream_event_count,
                    "usage": usage or {},
                },
                "mcp": {
                    "call_count": len(mcp_summary),
                    "calls": mcp_summary,
                },
                "prompt_sizes": {
                    "system_prompt_chars": len(self._snapshot.get("system_prompt", "") or ""),
                    "messages_count": len(self._snapshot.get("messages", []) or []),
                },
            }
            if extra_manifest:
                manifest.update(extra_manifest)

            await asyncio.to_thread(
                (self._dir / "manifest.json").write_text,
                _dump_json(manifest),
                "utf-8",
            )

            # 5. trace.md (lazy import)
            from .dump_trace_builder import build_trace_md

            trace_md = build_trace_md(
                manifest=manifest,
                snapshot=self._snapshot,
                reconstructed_calls=reconstructed,
                mcp_summary=mcp_summary,
                final_output=final_output,
                include_thinking=self.include_thinking,
            )
            await asyncio.to_thread(
                (self._dir / "trace.md").write_text, trace_md, "utf-8"
            )

            logger.info(f"[ConversationDump] finalized: {self._dir}")
        except Exception as exc:
            logger.warning(f"[ConversationDump] finalize() failed: {exc}")
        finally:
            self._finalized = True

    def _read_stream_events(self) -> List[Dict[str, Any]]:
        path = self._dir / "llm_calls" / "stream_events.jsonl"
        if not path.exists():
            return []
        out: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out

    def _preview_user_input(self) -> str:
        msgs = self._snapshot.get("messages") or []
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, list):
                    # Extract text blocks
                    parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            parts.append(block.get("text", ""))
                    content = " ".join(parts)
                return str(content)[:200]
        return ""

    # ------------------------------------------------------------------ misc

    def _materialise_mcp_from_reconstructed(
        self,
        reconstructed: List[Dict[str, Any]],
        stream_events: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """
        Walk reconstructed LLM calls and write one mcp_calls/NN_{tool}.json per
        tool_use. The tool output is fetched from the following call's request
        messages by matching tool_use_id. This captures MCP traffic that
        happens inside the Claude Agent SDK subprocess (invisible to
        utils/mcp_executor.py).

        Blocks are identified either by an explicit `type` field or — as a
        fallback for older dumps — by their field shape: tool_use has
        {id, name, input}; tool_result has {tool_use_id, content}.
        """
        def _is_tool_use(b: Dict[str, Any]) -> bool:
            if b.get("type") == "tool_use":
                return True
            return "id" in b and "name" in b and "input" in b

        def _is_tool_result(b: Dict[str, Any]) -> bool:
            if b.get("type") == "tool_result":
                return True
            return "tool_use_id" in b

        # First pass: collect all tool_use occurrences keyed by id.
        tool_uses: List[Dict[str, Any]] = []
        for call in reconstructed:
            resp = call.get("response") or {}
            for block in resp.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if _is_tool_use(block):
                    tool_uses.append({
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": block.get("input"),
                        "call_index": call.get("call_index"),
                    })

        # Second pass: collect all tool_result occurrences keyed by id. We
        # scan both the reconstructed requests (covers intermediate results)
        # AND the raw stream events (covers the trailing result that came
        # after the last LLM call, which was never part of any request).
        results_by_id: Dict[str, Any] = {}

        def _scan_blocks(blocks):
            if not isinstance(blocks, list):
                return
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if _is_tool_result(block):
                    tid = block.get("tool_use_id")
                    if tid and tid not in results_by_id:
                        results_by_id[tid] = block.get("content")

        for call in reconstructed:
            for msg in (call.get("request") or {}).get("messages") or []:
                if isinstance(msg, dict):
                    _scan_blocks(msg.get("content"))

        for ev in stream_events or []:
            if isinstance(ev, dict):
                _scan_blocks(ev.get("content"))

        # Write synthetic records.
        mcp_dir = self._dir / "mcp_calls"
        for i, use in enumerate(tool_uses, 1):
            tool_name = use["name"] or "unknown"
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool_name)
            payload = {
                "call_index": i,
                "tool_name": tool_name,
                "tool_use_id": use["id"],
                "llm_call_index": use["call_index"],
                "args": use["input"],
                "output": results_by_id.get(use["id"]),
                "latency_seconds": None,        # not observable from outside SDK
                "error": None,
                "source": "sdk_inferred",       # distinguish from executor-captured
            }
            try:
                (mcp_dir / f"{i:02d}_{safe}.json").write_text(
                    _dump_json(payload), "utf-8"
                )
            except Exception as exc:
                logger.debug(f"[ConversationDump] synthetic mcp write failed: {exc}")
        # Keep the counter consistent with what we wrote.
        self._mcp_counter = len(tool_uses)


def _extract_usage_from_stream(stream_events: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Sum token usage across the streamed events. The Claude Agent SDK surfaces
    per-turn usage on ResultMessage / message_stop events; we accept either
    a top-level `usage` dict or a nested `message_usage`.
    """
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    found = False
    for ev in stream_events:
        if not isinstance(ev, dict):
            continue
        for key in ("usage", "message_usage"):
            u = ev.get(key)
            if not isinstance(u, dict):
                continue
            found = True
            for k in list(totals.keys()):
                v = u.get(k)
                if isinstance(v, (int, float)):
                    totals[k] += int(v)
    return totals if found else {}
