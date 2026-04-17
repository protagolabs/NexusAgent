"""
@file_name: _tool_policy_guard.py
@date: 2026-04-16
@description: PreToolUse hook — single place to express "what agents are
    allowed to do" before a tool runs.

Two policies live here:

1. **Workspace-scoped reads.** ``Read`` / ``Glob`` / ``Grep`` must target
   paths inside the per-agent workspace. Writes are governed elsewhere.

2. **Server-tool gating.** ``WebSearch`` relies on Anthropic's server-side
   tool ``web_search_20250305``. Aggregators like NetMind, OpenRouter,
   Yunwu don't implement it — a call silently hangs for 45+s and then
   times out. When the current provider does not advertise server-tool
   support, we deny ``WebSearch`` immediately with an actionable hint so
   the LLM pivots to ``WebFetch``.

Hooks fire before the permission-mode check, so these rules apply even
under ``permission_mode="bypassPermissions"``. See
https://docs.claude.com/en/api/agent-sdk/permissions.

Known limitations
-----------------
* **Server-tool scope.** Only ``WebSearch`` is gated today. If Anthropic
  rolls out more server tools (text_editor beta, computer_use, ...) and
  the CLI surfaces them, extend ``_SERVER_TOOLS`` below.
* **Subagent inheritance.** Hooks installed on the parent session do
  *not* propagate into Task-spawned subagents — those are separate
  subprocesses with their own options. Env-var-driven policies (model
  routing via ``CLAUDE_CODE_SUBAGENT_MODEL``) do propagate because
  env is inherited; tool-level policies do not.
* **Path resolution.** We follow symlinks via ``Path.resolve()`` so a
  workspace-interior symlink pointing outside still trips the check.
  OS-level mount escapes (bind mounts, namespaces) are out of scope.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger


# Tools whose target path must stay inside the workspace subtree.
_READ_TOOLS = frozenset({"Read", "Glob", "Grep"})

# Argument names those tools use to carry their target path.
_PATH_ARG_NAMES = ("file_path", "path")

# Anthropic server-side tools — require the provider endpoint to
# implement the server tool spec. Extend as new ones ship.
_SERVER_TOOLS = frozenset({"WebSearch"})

# Patterns in a Bash command that indicate an agent is trying to bypass
# the Lark MCP layer (shelling directly to lark-cli, or installing Lark
# packages via third-party managers). The MCP tools handle workspace
# isolation + credential hydration — direct shell-outs skip both.
_LARK_SHELL_PATTERNS = (
    # Match `lark-cli` invoked as a program: at start of line, after any
    # whitespace/shell separator, or as the last path segment of an
    # absolute/relative path (e.g. `/usr/bin/lark-cli`).
    re.compile(r"(?:^|[\s;&|`$(/])lark-cli(?:\s|$)"),
    re.compile(r"(?:^|\s)npm\s+(?:install|i)\s+.*@larksuite/cli"),
    re.compile(r"(?:^|\s)clawhub\s+install\s+lark[-\w]*"),
    re.compile(r"(?:^|\s)npx\s+skills\s+add\s+larksuite/cli"),
)


PreToolUseHook = Callable[
    [dict[str, Any], str | None, Any],
    Awaitable[dict[str, Any]],
]


def _deny(reason: str) -> dict[str, Any]:
    """Shape the SDK-expected 'deny' return value for a PreToolUse hook."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def build_tool_policy_guard(
    workspace: str | Path,
    supports_server_tools: bool = False,
) -> PreToolUseHook:
    """Return an async PreToolUse hook that enforces workspace + server-tool policies.

    Args:
        workspace: Absolute path of the per-agent workspace directory. Any
            ``Read`` / ``Glob`` / ``Grep`` that resolves outside this subtree
            is denied.
        supports_server_tools: Whether the current LLM provider endpoint
            serves Anthropic's server-side tools. When ``False`` (the default
            and correct choice for NetMind / OpenRouter / other aggregators),
            ``WebSearch`` is denied upfront so the LLM can fall back to
            ``WebFetch`` without wasting a 45-second timeout.

    Returns:
        A coroutine suitable for ``HookMatcher(hooks=[...])``.
    """
    workspace_root = Path(workspace).resolve(strict=False)

    async def _guard(
        input_data: dict[str, Any],
        _tool_use_id: str | None,
        _context: Any,
    ) -> dict[str, Any]:
        tool_name = input_data.get("tool_name", "")

        # --- Server-tool gate ---------------------------------------------
        if tool_name in _SERVER_TOOLS and not supports_server_tools:
            logger.info(
                f"[tool_policy_guard] blocked {tool_name}: provider does not "
                f"support Anthropic server-side tools"
            )
            return _deny(
                f"{tool_name} is an Anthropic server-side tool and the "
                f"current LLM provider does not implement it. "
                f"Use WebFetch on a specific URL instead, or ask the user "
                f"to provide the URL you need."
            )

        # --- Bash + lark-cli gate -----------------------------------------
        # Shelling directly to lark-cli (or using package managers to
        # "install" Lark integration) skips workspace isolation and
        # credential hydration. Redirect the agent to the MCP surface.
        if tool_name == "Bash":
            command = (input_data.get("tool_input") or {}).get("command", "") or ""
            if isinstance(command, str) and any(p.search(command) for p in _LARK_SHELL_PATTERNS):
                logger.info(
                    f"[tool_policy_guard] blocked Bash Lark shell-out: {command[:200]!r}"
                )
                return _deny(
                    "Shell-outs to `lark-cli`, `npm install @larksuite/cli`, "
                    "`clawhub install lark-*`, or `npx skills add larksuite/cli` "
                    "are blocked. Lark work goes through MCP tools:\n"
                    "  • Any CLI command → `mcp__lark_module__lark_cli(agent_id, command=\"...\")`\n"
                    "  • Create new app → `mcp__lark_module__lark_setup(agent_id, brand, owner_email)`\n"
                    "  • Grant all permissions → `mcp__lark_module__lark_configure_permissions(agent_id)`\n"
                    "  • Finish OAuth → `mcp__lark_module__lark_auth_complete(agent_id, device_code)`\n"
                    "  • Confirm console done → `mcp__lark_module__lark_mark_console_done(agent_id)`\n"
                    "  • Enable real-time receive → `mcp__lark_module__lark_enable_receive(agent_id, app_secret)`\n"
                    "  • Health check → `mcp__lark_module__lark_status(agent_id)`\n"
                    "  • Load domain docs → `mcp__lark_module__lark_skill(agent_id, name=\"lark-im\"|\"lark-contact\"|...)`\n"
                    "lark-cli is already installed — do not try to reinstall it. "
                    "If an MCP tool fails, report the error verbatim to the user "
                    "instead of shelling out as a workaround."
                )

        # --- Workspace-scoped read gate -----------------------------------
        if tool_name not in _READ_TOOLS:
            return {}

        tool_input = input_data.get("tool_input") or {}

        raw_path: str | None = None
        for key in _PATH_ARG_NAMES:
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                raw_path = value
                break

        if raw_path is None:
            # Grep/Glob with no explicit path default to CWD (= workspace).
            return {}

        try:
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = workspace_root / candidate
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(workspace_root)
        except (ValueError, OSError) as exc:
            reason = (
                f"Access denied: '{raw_path}' is outside the agent workspace "
                f"'{workspace_root}'. Agents may only read files inside their "
                f"own workspace."
            )
            logger.info(
                f"[tool_policy_guard] blocked {tool_name} on {raw_path!r}: {exc}"
            )
            return _deny(reason)

        return {}

    return _guard


# ---------------------------------------------------------------------------
# Backwards-compat alias.
#
# The original file was `_workspace_read_guard.py` exporting
# `build_workspace_read_guard`. Keep the old name so any code we missed
# still imports cleanly (and so git history stays greppable).
# ---------------------------------------------------------------------------
def build_workspace_read_guard(workspace: str | Path) -> PreToolUseHook:
    """Deprecated alias. Prefer ``build_tool_policy_guard``."""
    return build_tool_policy_guard(workspace=workspace, supports_server_tools=False)
