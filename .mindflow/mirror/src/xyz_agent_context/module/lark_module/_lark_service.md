---
code_file: src/xyz_agent_context/module/lark_module/_lark_service.py
stub: false
last_verified: 2026-04-14
---

## Why it exists

Shared Lark business logic that both the HTTP routes (`backend/routes/lark.py`)
and MCP tools (`_lark_mcp_tools.py`) need.  Lives in the core package to
avoid circular imports — the API layer imports from here, never the other
way around.

## Design decisions

- **`do_bind()`** — single implementation of the bind flow (validate,
  register CLI profile, save credential, fetch bot name).  Both the
  HTTP route and MCP tool call this; the route adds owner-email
  resolution on top.
- **`resolve_owner()`** — looks up a Lark user by email via the CLI,
  returns `(open_id, display_name)`.
- **`determine_auth_status()`** — pure function that interprets the
  lark-cli `auth status` output into `"logged_in"` / `"not_logged_in"`.
  Extracts the magic sentinel string `"(no logged-in users)"` into a
  constant to avoid hardcoding it in multiple files.

## Upstream / downstream

- **Upstream**: `backend/routes/lark.py`, `_lark_mcp_tools.py`.
- **Downstream**: `LarkCLIClient`, `LarkCredentialManager`.
