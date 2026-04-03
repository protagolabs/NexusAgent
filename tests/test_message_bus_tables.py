"""
@file_name: test_message_bus_tables.py
@author: NexusAgent
@date: 2026-04-02
@description: Tests for MessageBus table creation and basic operations

Verifies:
1. All 5 bus tables are created correctly
2. Unprocessed message cursor query works
3. Message failure tracking works
"""

from __future__ import annotations

import pytest

from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.database_table_management.create_message_bus_tables import (
    BUS_TABLE_NAMES,
    create_bus_tables_sqlite,
)
from xyz_agent_context.utils.database_table_management.create_table_base import (
    check_table_exists_sqlite,
)


@pytest.fixture
async def db():
    """Provide an initialized in-memory SQLiteBackend with bus tables."""
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await create_bus_tables_sqlite(backend)
    yield backend
    await backend.close()


# --- 1. Verify all 5 tables are created ---

class TestBusTableCreation:
    """Verify all MessageBus tables exist after creation."""

    async def test_all_tables_exist(self, db):
        """All 5 bus tables should exist in sqlite_master."""
        for table_name in BUS_TABLE_NAMES:
            exists = await check_table_exists_sqlite(table_name, db)
            assert exists, f"Table {table_name} was not created"

    async def test_idempotent_creation(self, db):
        """Running create_bus_tables_sqlite twice should not raise."""
        await create_bus_tables_sqlite(db)
        for table_name in BUS_TABLE_NAMES:
            exists = await check_table_exists_sqlite(table_name, db)
            assert exists

    async def test_indexes_exist(self, db):
        """All 4 indexes should exist in sqlite_master."""
        expected_indexes = [
            "idx_bus_msg_channel_time",
            "idx_bus_member_agent",
            "idx_bus_registry_visibility",
            "idx_bus_registry_owner",
        ]
        rows = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        index_names = {row["name"] for row in rows}
        for idx_name in expected_indexes:
            assert idx_name in index_names, f"Index {idx_name} was not created"


# --- 2. Unprocessed message cursor query ---

class TestUnprocessedMessageQuery:
    """Test the cursor-based unprocessed message query pattern."""

    async def test_unprocessed_messages_for_agent(self, db):
        """
        Insert a channel with two members and messages, then query
        unprocessed messages for one agent using the cursor model.
        """
        # Create a channel
        await db.insert("bus_channels", {
            "channel_id": "ch_001",
            "name": "General",
            "channel_type": "group",
            "created_by": "agt_alice",
            "created_at": "2026-04-01T00:00:00",
        })

        # Add two members
        await db.insert("bus_channel_members", {
            "channel_id": "ch_001",
            "agent_id": "agt_alice",
            "joined_at": "2026-04-01T00:00:00",
            "last_processed_at": "2026-04-01T12:00:00",
        })
        await db.insert("bus_channel_members", {
            "channel_id": "ch_001",
            "agent_id": "agt_bob",
            "joined_at": "2026-04-01T00:00:00",
            "last_processed_at": None,  # Never processed any messages
        })

        # Insert messages
        messages = [
            ("msg_001", "ch_001", "agt_alice", "Hello Bob", "2026-04-01T10:00:00"),
            ("msg_002", "ch_001", "agt_bob", "Hi Alice", "2026-04-01T11:00:00"),
            ("msg_003", "ch_001", "agt_alice", "How are you?", "2026-04-01T13:00:00"),
            ("msg_004", "ch_001", "agt_bob", "Good thanks", "2026-04-01T14:00:00"),
        ]
        for msg_id, ch_id, from_agent, content, created_at in messages:
            await db.insert("bus_messages", {
                "message_id": msg_id,
                "channel_id": ch_id,
                "from_agent": from_agent,
                "content": content,
                "created_at": created_at,
            })

        # Query unprocessed messages for agt_bob (cursor model)
        # Bob has last_processed_at = None, so COALESCE gives '1970-01-01'
        # Bob should see messages from agt_alice only (not own messages)
        rows = await db.execute(
            """
            SELECT m.* FROM bus_messages m
            JOIN bus_channel_members cm ON m.channel_id = cm.channel_id
            WHERE cm.agent_id = ?
              AND m.created_at > COALESCE(cm.last_processed_at, '1970-01-01')
              AND m.from_agent != ?
            ORDER BY m.created_at ASC
            """,
            ("agt_bob", "agt_bob"),
        )

        assert len(rows) == 2
        assert rows[0]["message_id"] == "msg_001"
        assert rows[0]["content"] == "Hello Bob"
        assert rows[1]["message_id"] == "msg_003"
        assert rows[1]["content"] == "How are you?"

    async def test_no_unprocessed_after_cursor_update(self, db):
        """After updating last_processed_at, no messages should be unprocessed."""
        await db.insert("bus_channels", {
            "channel_id": "ch_002",
            "name": "Direct",
            "channel_type": "direct",
            "created_by": "agt_x",
            "created_at": "2026-04-01T00:00:00",
        })
        await db.insert("bus_channel_members", {
            "channel_id": "ch_002",
            "agent_id": "agt_y",
            "joined_at": "2026-04-01T00:00:00",
            "last_processed_at": "2026-04-02T00:00:00",
        })
        await db.insert("bus_messages", {
            "message_id": "msg_old",
            "channel_id": "ch_002",
            "from_agent": "agt_x",
            "content": "Old message",
            "created_at": "2026-04-01T23:00:00",
        })

        rows = await db.execute(
            """
            SELECT m.* FROM bus_messages m
            JOIN bus_channel_members cm ON m.channel_id = cm.channel_id
            WHERE cm.agent_id = ?
              AND m.created_at > COALESCE(cm.last_processed_at, '1970-01-01')
              AND m.from_agent != ?
            ORDER BY m.created_at ASC
            """,
            ("agt_y", "agt_y"),
        )

        assert len(rows) == 0


# --- 3. Message failure tracking ---

class TestMessageFailures:
    """Test bus_message_failures insert and query."""

    async def test_insert_and_query_failure(self, db):
        """Insert a failure record and query it back."""
        await db.insert("bus_message_failures", {
            "message_id": "msg_fail_001",
            "agent_id": "agt_broken",
            "retry_count": 1,
            "last_error": "Connection timeout",
            "last_retry_at": "2026-04-02T10:00:00",
        })

        row = await db.get_one("bus_message_failures", {
            "message_id": "msg_fail_001",
            "agent_id": "agt_broken",
        })
        assert row is not None
        assert row["retry_count"] == 1
        assert row["last_error"] == "Connection timeout"

    async def test_update_retry_count(self, db):
        """Update retry_count on subsequent failure."""
        await db.insert("bus_message_failures", {
            "message_id": "msg_fail_002",
            "agent_id": "agt_flaky",
            "retry_count": 0,
            "last_error": "Timeout",
            "last_retry_at": "2026-04-02T09:00:00",
        })

        await db.update(
            "bus_message_failures",
            {"message_id": "msg_fail_002", "agent_id": "agt_flaky"},
            {"retry_count": 1, "last_error": "Timeout again", "last_retry_at": "2026-04-02T09:05:00"},
        )

        row = await db.get_one("bus_message_failures", {
            "message_id": "msg_fail_002",
            "agent_id": "agt_flaky",
        })
        assert row["retry_count"] == 1
        assert row["last_error"] == "Timeout again"

    async def test_composite_primary_key(self, db):
        """Different agents can have failures for the same message."""
        await db.insert("bus_message_failures", {
            "message_id": "msg_shared",
            "agent_id": "agt_a",
            "retry_count": 2,
            "last_error": "Error A",
        })
        await db.insert("bus_message_failures", {
            "message_id": "msg_shared",
            "agent_id": "agt_b",
            "retry_count": 1,
            "last_error": "Error B",
        })

        row_a = await db.get_one("bus_message_failures", {
            "message_id": "msg_shared",
            "agent_id": "agt_a",
        })
        row_b = await db.get_one("bus_message_failures", {
            "message_id": "msg_shared",
            "agent_id": "agt_b",
        })
        assert row_a["retry_count"] == 2
        assert row_b["retry_count"] == 1
