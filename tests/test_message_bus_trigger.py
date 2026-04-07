"""
@file_name: test_message_bus_trigger.py
@author: NarraNexus
@date: 2026-04-03
@description: Tests for send_to_agent and MessageBusTrigger

Verifies:
1. send_to_agent auto-creates a direct channel on first call
2. send_to_agent reuses an existing direct channel on subsequent calls
3. MessageBusTrigger finds pending messages for registered agents
"""

from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.database_table_management.create_message_bus_tables import (
    create_bus_tables_sqlite,
)


@pytest.fixture
async def db():
    """Provide an initialized in-memory SQLiteBackend with bus tables."""
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await create_bus_tables_sqlite(backend)
    yield backend
    await backend.close()


@pytest.fixture
async def bus(db):
    """Provide a LocalMessageBus instance."""
    return LocalMessageBus(backend=db)


# --- 1. send_to_agent auto-creates direct channel ---

class TestSendToAgent:
    """Test the send_to_agent convenience method."""

    async def test_auto_creates_direct_channel(self, bus: LocalMessageBus, db):
        """First send_to_agent call should create a new direct channel."""
        msg_id = await bus.send_to_agent(
            from_agent="agt_alice",
            to_agent="agt_bob",
            content="Hello Bob!",
        )
        assert msg_id.startswith("msg_")

        # Verify a direct channel was created
        rows = await db.execute(
            "SELECT * FROM bus_channels WHERE channel_type = 'direct'"
        )
        assert len(rows) == 1
        assert rows[0]["name"] == "dm_agt_alice_agt_bob"

        # Verify both agents are members
        members = await db.execute(
            "SELECT agent_id FROM bus_channel_members WHERE channel_id = ?",
            (rows[0]["channel_id"],),
        )
        member_ids = {m["agent_id"] for m in members}
        assert member_ids == {"agt_alice", "agt_bob"}

    async def test_reuses_existing_direct_channel(self, bus: LocalMessageBus, db):
        """Subsequent send_to_agent calls should reuse the same channel."""
        msg_id_1 = await bus.send_to_agent(
            from_agent="agt_alice",
            to_agent="agt_bob",
            content="First message",
        )
        msg_id_2 = await bus.send_to_agent(
            from_agent="agt_alice",
            to_agent="agt_bob",
            content="Second message",
        )

        assert msg_id_1 != msg_id_2

        # Still only one direct channel
        rows = await db.execute(
            "SELECT * FROM bus_channels WHERE channel_type = 'direct'"
        )
        assert len(rows) == 1

    async def test_reverse_direction_reuses_channel(self, bus: LocalMessageBus, db):
        """Sending from B to A should reuse the channel created by A to B."""
        await bus.send_to_agent(
            from_agent="agt_alice",
            to_agent="agt_bob",
            content="Hello from Alice",
        )
        await bus.send_to_agent(
            from_agent="agt_bob",
            to_agent="agt_alice",
            content="Hello from Bob",
        )

        # Still only one direct channel
        rows = await db.execute(
            "SELECT * FROM bus_channels WHERE channel_type = 'direct'"
        )
        assert len(rows) == 1

    async def test_message_appears_in_channel(self, bus: LocalMessageBus):
        """Message sent via send_to_agent should be retrievable."""
        await bus.send_to_agent(
            from_agent="agt_x",
            to_agent="agt_y",
            content="Test content",
        )

        # agt_y is a channel member, so pending messages should include the sent one
        pending = await bus.get_pending_messages("agt_y")
        assert len(pending) == 1
        assert pending[0].content == "Test content"
        assert pending[0].from_agent == "agt_x"

        # Also check via get_messages on the channel
        rows = await bus._db.execute(
            "SELECT channel_id FROM bus_channels WHERE channel_type = 'direct'"
        )
        ch_id = rows[0]["channel_id"]
        messages = await bus.get_messages(ch_id)
        assert len(messages) == 1
        assert messages[0].content == "Test content"
        assert messages[0].from_agent == "agt_x"


# --- 2. Trigger finds pending messages ---

class TestTriggerPendingMessages:
    """Test that the trigger can discover pending messages for agents."""

    async def test_finds_pending_for_registered_agent(self, bus: LocalMessageBus, db):
        """Registered agents should have pending messages discovered."""
        # Register two agents
        await bus.register_agent(
            agent_id="agt_alpha",
            owner_user_id="user_1",
            capabilities=["chat"],
            description="Alpha agent",
        )
        await bus.register_agent(
            agent_id="agt_beta",
            owner_user_id="user_1",
            capabilities=["chat"],
            description="Beta agent",
        )

        # Send a message from alpha to beta
        await bus.send_to_agent(
            from_agent="agt_alpha",
            to_agent="agt_beta",
            content="Hello beta!",
        )

        # Beta should have pending messages
        pending = await bus.get_pending_messages("agt_beta")
        assert len(pending) == 1
        assert pending[0].content == "Hello beta!"
        assert pending[0].from_agent == "agt_alpha"

    async def test_no_pending_after_ack(self, bus: LocalMessageBus, db):
        """After ack_processed, messages should no longer be pending."""
        await bus.register_agent(
            agent_id="agt_a",
            owner_user_id="user_1",
            capabilities=[],
            description="A",
        )
        await bus.register_agent(
            agent_id="agt_b",
            owner_user_id="user_1",
            capabilities=[],
            description="B",
        )

        await bus.send_to_agent(
            from_agent="agt_a",
            to_agent="agt_b",
            content="Process me",
        )

        pending = await bus.get_pending_messages("agt_b")
        assert len(pending) == 1

        # Ack the message
        await bus.ack_processed(
            agent_id="agt_b",
            channel_id=pending[0].channel_id,
            up_to_timestamp=pending[0].created_at,
        )

        # No more pending
        pending_after = await bus.get_pending_messages("agt_b")
        assert len(pending_after) == 0

    async def test_trigger_build_prompt(self, bus: LocalMessageBus):
        """MessageBusTrigger should build a readable prompt from messages."""
        from xyz_agent_context.message_bus.schemas import BusMessage

        trigger = MessageBusTrigger(bus=bus)
        messages = [
            BusMessage(
                message_id="msg_001",
                channel_id="ch_001",
                from_agent="agt_alice",
                content="Hello!",
                created_at="2026-04-03T10:00:00",
            ),
        ]
        prompt = trigger._build_prompt(messages)
        assert "Message Bus - Incoming Messages" in prompt
        assert "agt_alice" in prompt
        assert "Hello!" in prompt
