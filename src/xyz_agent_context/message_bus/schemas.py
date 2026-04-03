"""
@file_name: schemas.py
@author: NarraNexus
@date: 2026-04-02
@description: Pydantic data models for the MessageBus service

Defines the core data structures used across all MessageBus implementations:
- BusMessage: A message sent within a channel
- BusChannel: A communication channel (group or direct)
- BusChannelMember: Channel membership with read/processed cursors
- BusAgentInfo: Agent registration and discovery metadata
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class BusMessage(BaseModel):
    """A message sent within a MessageBus channel."""

    message_id: str
    channel_id: str
    from_agent: str
    content: str
    msg_type: str = "text"
    created_at: str  # ISO 8601


class BusChannel(BaseModel):
    """A communication channel in the MessageBus."""

    channel_id: str
    name: str
    channel_type: str = "group"  # "direct" | "group"
    created_by: str
    created_at: str  # ISO 8601


class BusChannelMember(BaseModel):
    """Channel membership record with read and processed cursors."""

    channel_id: str
    agent_id: str
    joined_at: str  # ISO 8601
    last_read_at: str  # ISO 8601
    last_processed_at: Optional[str] = None  # ISO 8601


class BusAgentInfo(BaseModel):
    """Agent registration and discovery metadata."""

    agent_id: str
    owner_user_id: str
    capabilities: List[str] = []
    description: str = ""
    visibility: str = "private"  # "public" | "private"
    registered_at: str  # ISO 8601
    last_seen_at: str  # ISO 8601
