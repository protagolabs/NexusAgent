---
code_file: src/xyz_agent_context/module/lark_module/lark_context_builder.py
stub: false
last_verified: 2026-04-14
---

## Why it exists

Builds execution context for Lark-triggered messages by implementing
`ChannelContextBuilderBase`.  Fetches conversation history and maps
Lark-specific fields to the normalized format the runtime expects.

## Design decisions

- **Inherits `ChannelContextBuilderBase`** — same pattern as other
  channel integrations (e.g., LINE, Discord).  Implements three
  methods: `get_message_info`, `get_conversation_history`,
  `get_room_members`.
- **`get_room_members` returns `[]`** — Lark CLI has no
  `+chat-members` shortcut yet.  Can be implemented via the API layer
  when needed.
- **Content JSON unwrapping** — Lark CLI may return message content
  as a JSON string `{"text": "hi"}`; the builder extracts the `text`
  field transparently.

## Upstream / downstream

- **Upstream**: `lark_trigger.py` (`_build_and_run_agent`).
- **Downstream**: `LarkCLIClient.list_chat_messages`,
  `ChannelContextBuilderBase.build_prompt`.
