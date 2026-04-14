"""
Build a human-readable trace.md from the collected dump artifacts.

Pure-function module so it can be unit-tested in isolation. Keeps the
markdown compact and uses <details> folds for bulky sections so the file
is readable in GitHub / VSCode preview.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _pretty_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def _render_content_blocks(
    blocks: List[Dict[str, Any]],
    include_thinking: bool,
) -> List[str]:
    """Render a list of Anthropic-style content blocks to markdown."""
    out: List[str] = []
    for b in blocks or []:
        if not isinstance(b, dict):
            out.append(f"- `{b!r}`")
            continue
        btype = b.get("type")
        # Claude SDK blocks may have lost their `type` field during
        # serialization; infer from field shape.
        if not btype:
            if "thinking" in b:
                btype = "thinking"
            elif "tool_use_id" in b:
                btype = "tool_result"
            elif "id" in b and "name" in b and "input" in b:
                btype = "tool_use"
            elif "text" in b:
                btype = "text"
        if btype == "thinking":
            content = b.get("thinking", "")
            if include_thinking:
                out.append("**Thinking**:")
                out.append("")
                out.append("> " + content.replace("\n", "\n> "))
            else:
                out.append(f"**Thinking** ({len(content)} chars, omitted)")
            out.append("")
        elif btype == "text":
            text = b.get("text", "")
            out.append(text)
            out.append("")
        elif btype == "tool_use":
            name = b.get("name", "?")
            args = b.get("input", {})
            out.append(f"**Tool call**: `{name}`")
            out.append("")
            out.append("```json")
            out.append(_pretty_json(args))
            out.append("```")
            out.append("")
        elif btype == "tool_result":
            tid = b.get("tool_use_id", "?")
            content = b.get("content", "")
            preview = content if isinstance(content, str) else _pretty_json(content)
            if len(preview) > 2000:
                preview = preview[:2000] + f"\n... (truncated, {len(preview)} chars total)"
            out.append(f"**Tool result** (id={tid}):")
            out.append("")
            out.append("```")
            out.append(preview)
            out.append("```")
            out.append("")
        else:
            out.append(f"**[{btype}]**:")
            out.append("")
            out.append("```json")
            out.append(_pretty_json(b))
            out.append("```")
            out.append("")
    return out


def build_trace_md(
    *,
    manifest: Dict[str, Any],
    snapshot: Dict[str, Any],
    reconstructed_calls: List[Dict[str, Any]],
    mcp_summary: List[Dict[str, Any]],
    final_output: Optional[str] = None,
    include_thinking: bool = True,
) -> str:
    """Assemble the full trace.md as a single string."""
    lines: List[str] = []

    # ----- Header -----
    lines.append(f"# Conversation Trace — `{manifest.get('event_id', '?')}`")
    lines.append("")
    lines.append(
        f"**Agent** `{manifest.get('agent_id')}` · "
        f"**User** `{manifest.get('user_id')}` · "
        f"**Started** {manifest.get('started_at', '?')}"
    )
    llm = manifest.get("llm", {}) or {}
    mcp = manifest.get("mcp", {}) or {}
    usage = llm.get("usage", {}) or {}
    lines.append(
        f"**Duration** {manifest.get('total_seconds', '?')}s · "
        f"**LLM calls** {llm.get('call_count', 0)} · "
        f"**MCP calls** {mcp.get('call_count', 0)} · "
        f"**Tokens** in={usage.get('input_tokens', '?')} out={usage.get('output_tokens', '?')}"
    )
    lines.append("")

    # ----- 1. Input -----
    lines.append("## 1. Input")
    lines.append("")
    lines.append(f"> {manifest.get('user_input_preview', '') or '(no user input)'}")
    lines.append("")

    # ----- 2. Context Assembly -----
    lines.append("## 2. Context Assembly")
    lines.append("")
    cr = snapshot.get("continuity_result")
    if cr is not None:
        is_cont = None
        conf = None
        reason = None
        if hasattr(cr, "is_continuous"):
            is_cont = getattr(cr, "is_continuous", None)
            conf = getattr(cr, "confidence", None)
            reason = getattr(cr, "reason", None)
        elif isinstance(cr, dict):
            is_cont = cr.get("is_continuous")
            conf = cr.get("confidence")
            reason = cr.get("reason")
        lines.append(
            f"- **Continuity**: `{is_cont}` (confidence={conf}) — {reason}"
        )

    nl = snapshot.get("narrative_list") or []
    if nl:
        first = nl[0]
        nid = getattr(first, "id", None) or (isinstance(first, dict) and first.get("id")) or "?"
        nname = getattr(first, "name", None) or (isinstance(first, dict) and first.get("name")) or "?"
        lines.append(f"- **Primary narrative**: `{nid}` — {nname}")
        if len(nl) > 1:
            lines.append(f"- Auxiliary narratives: {len(nl) - 1}")

    lr = snapshot.get("load_result")
    if lr is not None:
        # Best-effort; load_result is a pydantic model in practice.
        ais = getattr(lr, "active_instances", None) or (
            isinstance(lr, dict) and lr.get("active_instances")
        ) or []
        etype = getattr(lr, "execution_type", None) or (
            isinstance(lr, dict) and lr.get("execution_type")
        )
        if hasattr(etype, "value"):
            etype = etype.value
        lines.append(f"- **Execution path**: `{etype}`")
        lines.append(f"- **Active instances**: {len(ais)}")
        for inst in ais[:10]:
            iid = getattr(inst, "instance_id", None) or (
                isinstance(inst, dict) and inst.get("instance_id")
            ) or "?"
            mclass = getattr(inst, "module_class", None) or (
                isinstance(inst, dict) and inst.get("module_class")
            ) or "?"
            lines.append(f"  - `{iid}` ({mclass})")

    urls = snapshot.get("mcp_urls") or {}
    lines.append(f"- **MCP servers attached**: {len(urls)}")
    for name, url in list(urls.items())[:10]:
        lines.append(f"  - `{name}` → `{url}`")
    lines.append("")

    # ----- 3. System Prompt Layers (folded) -----
    lines.append("## 3. System Prompt")
    lines.append("")
    sp = snapshot.get("system_prompt", "") or ""
    lines.append(f"Total size: **{len(sp)} chars** · see `context/system_prompt.md` for layered view")
    lines.append("")
    # Use a 5-backtick fence so that nested ``` inside the system prompt
    # (it often contains embedded markdown code blocks) don't close it early.
    lines.append("<details><summary>Click to expand full system prompt</summary>")
    lines.append("")
    lines.append("`````text")
    lines.append(sp)
    lines.append("`````")
    lines.append("")
    lines.append("</details>")
    lines.append("")

    # ----- 4. LLM Call Sequence -----
    lines.append("## 4. LLM Call Sequence")
    lines.append("")
    if not reconstructed_calls:
        lines.append("_No assistant messages were captured._")
        lines.append("")
    for call in reconstructed_calls:
        idx = call.get("call_index")
        resp = call.get("response", {}) or {}
        stop = resp.get("stop_reason")
        u = resp.get("usage") or {}
        # Per-call usage is not always available (Claude Agent SDK only
        # surfaces cumulative usage on ResultMessage). Only show in/out if
        # the values actually exist.
        header = f"### Call {idx}  — stop={stop}"
        if u.get("input_tokens") is not None or u.get("output_tokens") is not None:
            header += (
                f" · in={u.get('input_tokens', '-')} "
                f"out={u.get('output_tokens', '-')}"
            )
        lines.append(header)
        lines.append("")
        lines.extend(_render_content_blocks(resp.get("content", []), include_thinking))

    # ----- 5. MCP Calls Summary -----
    if mcp_summary:
        lines.append("## 5. MCP Call Summary")
        lines.append("")
        lines.append("| # | Tool | Latency (s) | Error |")
        lines.append("|---|------|-------------|-------|")
        for m in mcp_summary:
            lines.append(
                f"| {m.get('call_index')} | `{m.get('tool_name')}` | "
                f"{m.get('latency_seconds')} | {m.get('error') or ''} |"
            )
        lines.append("")
        lines.append("Full args/output: see `mcp_calls/` directory.")
        lines.append("")

    # ----- 6. Final Output -----
    lines.append("## 6. Final Output")
    lines.append("")
    if final_output:
        lines.append("> " + final_output.replace("\n", "\n> "))
    else:
        lines.append(f"> {manifest.get('final_output_preview', '') or '(no output)'}")
    lines.append("")

    # ----- 7. Per-step Timing -----
    pst = manifest.get("per_step_timing") or {}
    if pst:
        lines.append("## 7. Per-step Timing")
        lines.append("")
        for k, v in pst.items():
            lines.append(f"- `{k}`: {v}s")
        lines.append("")

    # ----- 8. Links -----
    lines.append("## 8. Files")
    lines.append("")
    lines.append("- `manifest.json` — structured summary")
    lines.append("- `context/system_prompt.md` — layered system prompt")
    lines.append("- `context/messages.json` — messages sent to LLM")
    lines.append("- `context/tools.json` — MCP URLs and tool schemas")
    lines.append("- `context/narrative.md` — narrative + continuity + module decisions")
    lines.append("- `llm_calls/stream_events.jsonl` — raw SDK stream events (append-only)")
    lines.append("- `llm_calls/reconstructed.json` — logical LLM call pairs")
    lines.append("- `llm_calls/execution_state.json` — processed execution state")
    lines.append("- `mcp_calls/NN_*.json` — per-tool invocation records")
    lines.append("")

    return "\n".join(lines)
