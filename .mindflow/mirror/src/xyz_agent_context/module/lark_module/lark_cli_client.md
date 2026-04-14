---
code_file: src/xyz_agent_context/module/lark_module/lark_cli_client.py
stub: false
last_verified: 2026-04-14
---

## Why it exists

Unified async wrapper around all `lark-cli` subprocess calls.
Every CLI invocation goes through `_run()`, which auto-appends
`--profile` and handles JSON parsing, timeouts, and error extraction.

## Design decisions

- **`shell=False` everywhere** — all args passed as a list to prevent
  command injection.
- **`--app-secret-stdin`** — secrets are passed via stdin, never as
  CLI arguments (would be visible in `ps aux`).
- **SSRF protection on `doc_url`** — `fetch_document` and
  `update_document` validate URLs against a Lark domain whitelist
  before passing them to the subprocess.
- **Timeout kill** — on `asyncio.TimeoutError`, the subprocess is
  explicitly killed to prevent zombie processes.

## Upstream / downstream

- **Upstream**: `_lark_mcp_tools.py`, `lark_trigger.py`,
  `backend/routes/lark.py`.
- **Downstream**: `lark-cli` binary (must be installed globally via
  `npm install -g @larksuite/cli`).

## Gotchas

- Debug log at `_run` line 42 logs the full command.  Sensitive data
  should never be placed in `args` — always use `stdin_data`.
- `subscribe_events` returns a raw `Process` object; the caller is
  responsible for reading stdout and managing lifecycle.
