"""
@file_name: create_message_bus_tables.py
@author: NexusAgent
@date: 2026-04-02
@description: MessageBus table definitions and creation for SQLite

Defines DDL for the 5 message bus tables used by the multi-agent
communication system:
- bus_channels: Communication channels (group, direct, broadcast)
- bus_channel_members: Channel membership with cursor tracking
- bus_messages: Messages within channels
- bus_agent_registry: Agent discovery and capability registry
- bus_message_failures: Dead-letter tracking for failed deliveries

All tables use SQLite syntax with TEXT for timestamps (ISO 8601).
"""

from __future__ import annotations

from xyz_agent_context.utils.db_backend import DatabaseBackend

# ===== Table DDL =====

DDL_BUS_CHANNELS = """
CREATE TABLE IF NOT EXISTS bus_channels (
    channel_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    channel_type TEXT NOT NULL DEFAULT 'group',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

DDL_BUS_CHANNEL_MEMBERS = """
CREATE TABLE IF NOT EXISTS bus_channel_members (
    channel_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    joined_at TEXT,
    last_read_at TEXT,
    last_processed_at TEXT,
    PRIMARY KEY (channel_id, agent_id)
)
"""

DDL_BUS_MESSAGES = """
CREATE TABLE IF NOT EXISTS bus_messages (
    message_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    from_agent TEXT NOT NULL,
    content TEXT NOT NULL,
    msg_type TEXT NOT NULL DEFAULT 'text',
    mentions TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

DDL_BUS_AGENT_REGISTRY = """
CREATE TABLE IF NOT EXISTS bus_agent_registry (
    agent_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    capabilities TEXT,
    description TEXT,
    capability_embedding TEXT,
    visibility TEXT NOT NULL DEFAULT 'private',
    registered_at TEXT,
    last_seen_at TEXT
)
"""

DDL_BUS_MESSAGE_FAILURES = """
CREATE TABLE IF NOT EXISTS bus_message_failures (
    message_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_retry_at TEXT,
    PRIMARY KEY (message_id, agent_id)
)
"""

# ===== Index definitions =====

DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_bus_msg_channel_time ON bus_messages(channel_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_bus_member_agent ON bus_channel_members(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_bus_registry_visibility ON bus_agent_registry(visibility)",
    "CREATE INDEX IF NOT EXISTS idx_bus_registry_owner ON bus_agent_registry(owner_user_id)",
]

# All DDL statements in creation order
ALL_DDL = [
    DDL_BUS_CHANNELS,
    DDL_BUS_CHANNEL_MEMBERS,
    DDL_BUS_MESSAGES,
    DDL_BUS_AGENT_REGISTRY,
    DDL_BUS_MESSAGE_FAILURES,
]

# All table names for verification
BUS_TABLE_NAMES = [
    "bus_channels",
    "bus_channel_members",
    "bus_messages",
    "bus_agent_registry",
    "bus_message_failures",
]


async def create_bus_tables_sqlite(backend: DatabaseBackend) -> None:
    """
    Create all MessageBus tables and indexes in a SQLite database.

    Args:
        backend: An initialized DatabaseBackend (SQLiteBackend).
    """
    for ddl in ALL_DDL:
        await backend.execute_write(ddl)

    for idx_ddl in DDL_INDEXES:
        await backend.execute_write(idx_ddl)
