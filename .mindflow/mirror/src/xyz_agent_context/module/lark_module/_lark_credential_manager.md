---
code_file: src/xyz_agent_context/module/lark_module/_lark_credential_manager.py
stub: false
last_verified: 2026-04-14
---

## Why it exists

CRUD layer for the `lark_credentials` table.  Every file that needs
to read/write Lark bot credentials goes through this manager rather
than touching the DB directly.

## Design decisions

- **Dataclass, not Pydantic** — `LarkCredential` is a plain
  `@dataclass` because it is internal-only and does not need
  serialization validation.
- **`_encode_secret` / `_decode_secret`** — base64 encoding (NOT
  encryption).  Naming was changed from `_encrypt/_decrypt` to avoid
  misleading future readers.  A TODO remains for AES-based encryption
  in production/cloud mode.
- **DB column still named `app_secret_encrypted`** — the column name
  is kept for backward compatibility with existing databases; the
  Python field was renamed to `app_secret_encoded`.

## Upstream / downstream

- **Upstream**: `lark_module.py`, `_lark_mcp_tools.py`, `lark_trigger.py`,
  `backend/routes/lark.py`.
- **Downstream**: `AsyncDatabaseClient` via `self.db`.

## Gotchas

- `save_credential` does a read-then-write (SELECT then INSERT/UPDATE).
  Under rapid concurrent bind requests for the same agent_id, a race
  is theoretically possible.  The UNIQUE index on agent_id provides a
  safety net.
