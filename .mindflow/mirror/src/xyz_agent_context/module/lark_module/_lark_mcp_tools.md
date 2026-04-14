---
code_file: src/xyz_agent_context/module/lark_module/_lark_mcp_tools.py
stub: false
last_verified: 2026-04-14
---

## Why it exists

Registers 21 MCP tools (lark_* prefix) on the FastMCP server so
that the LLM agent can interact with Lark: contacts, messaging,
docs, calendar, tasks, and bot management.

## Design decisions

- **One function per tool** — each tool reads the agent's credential
  from DB then delegates to `LarkCLIClient`.  No business logic lives
  here; it is a thin adapter layer.
- **`lark_bind_bot` reuses `_do_bind` from `backend/routes/lark.py`**
  — eliminates the previous duplication where the MCP tool had its
  own bind implementation that differed from the HTTP route.
- **`lark_auth_status` uses `_determine_auth_status`** — shared
  helper avoids the magic string `"(no logged-in users)"` being
  hardcoded in multiple places.

## Upstream / downstream

- **Upstream**: `lark_module.py` calls `register_lark_mcp_tools(mcp)`.
- **Downstream**: `LarkCLIClient`, `LarkCredentialManager`,
  `backend/routes/lark.py` (shared helpers).

## Gotchas

- `lark_bind_bot` accepts `app_secret` as a plain MCP tool argument.
  This means the secret may appear in LLM execution traces.  Consider
  restricting this tool to frontend API bridge calls only.
