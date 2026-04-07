"""
@file_name: test_messagebus_features.py
@date: 2026-04-07
@description: Tests for MessageBus feature parity additions

Covers: mentions in send_message, get_channel_members, kick_member,
get_agent_profile.
"""

from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.database_table_management.create_message_bus_tables import (
    create_bus_tables_sqlite,
)


@pytest.fixture
async def bus():
    """Provide a LocalMessageBus backed by an in-memory SQLite database."""
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await create_bus_tables_sqlite(backend)
    bus = LocalMessageBus(backend=backend)
    yield bus
    await backend.close()


@pytest.mark.asyncio
async def test_send_message_with_mentions(bus):
    ch = await bus.create_channel("test", ["agent_a", "agent_b"])
    await bus.send_message("agent_a", ch, "hello @agent_b", mentions=["agent_b"])
    msgs = await bus.get_messages(ch)
    assert len(msgs) == 1
    assert msgs[0].mentions == ["agent_b"]


@pytest.mark.asyncio
async def test_send_message_everyone_mention(bus):
    ch = await bus.create_channel("test", ["agent_a", "agent_b", "agent_c"])
    await bus.send_message("agent_a", ch, "hey all", mentions=["@everyone"])
    msgs = await bus.get_messages(ch)
    assert msgs[0].mentions == ["@everyone"]


@pytest.mark.asyncio
async def test_send_message_no_mentions(bus):
    ch = await bus.create_channel("test", ["agent_a", "agent_b"])
    await bus.send_message("agent_a", ch, "hello")
    msgs = await bus.get_messages(ch)
    assert msgs[0].mentions is None


@pytest.mark.asyncio
async def test_get_channel_members(bus):
    ch = await bus.create_channel("room", ["agent_a", "agent_b", "agent_c"])
    members = await bus.get_channel_members(ch)
    agent_ids = {m.agent_id for m in members}
    assert agent_ids == {"agent_a", "agent_b", "agent_c"}


@pytest.mark.asyncio
async def test_kick_member(bus):
    ch = await bus.create_channel("room", ["agent_a", "agent_b"])
    await bus.kick_member(ch, "agent_b")
    members = await bus.get_channel_members(ch)
    assert len(members) == 1
    assert members[0].agent_id == "agent_a"


@pytest.mark.asyncio
async def test_get_agent_profile(bus):
    await bus.register_agent("agent_x", "user1", ["chat"], "A chatbot", "public")
    profile = await bus.get_agent_profile("agent_x")
    assert profile is not None
    assert profile.agent_id == "agent_x"
    assert profile.description == "A chatbot"
    assert profile.capabilities == ["chat"]


@pytest.mark.asyncio
async def test_get_agent_profile_not_found(bus):
    profile = await bus.get_agent_profile("nonexistent")
    assert profile is None
