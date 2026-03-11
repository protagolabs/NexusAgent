"""
@file_name: channel_contact_utils.py
@author: Bin Liang
@date: 2026-03-10
@description: Read/write utility functions for contact_info.channels

contact_info structure:
{
    "channels": {
        "matrix": {
            "id": "@agent_xxx:matrix.narranexus.com",
            "server": "https://matrix.narranexus.com",
            "rooms": {
                "agent_research_002": "!roomid123:matrix.narranexus.com"
            }
        },
        "slack": {
            "id": "U12345678",
            "workspace": "narranexus.slack.com"
        }
    },
    "preferred_channel": "matrix"
}

Design principle:
- Social Network only does deep-merge JSON read/write; it never parses channels internals
- Adding a new channel just means adding a key under channels — zero Social Network changes
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional


def get_channel_info(contact_info: Dict[str, Any], channel: str) -> Optional[Dict[str, Any]]:
    """
    Read a specific channel's info from contact_info.

    Args:
        contact_info: The entity's contact_info field
        channel: Channel name, e.g. "matrix", "slack"

    Returns:
        Channel info dict, or None if not present
    """
    return contact_info.get("channels", {}).get(channel)


def set_channel_info(
    contact_info: Dict[str, Any],
    channel: str,
    info: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Write channel info into contact_info via deep merge.

    Does NOT overwrite unrelated fields. E.g. writing matrix.rooms won't touch matrix.id.

    Args:
        contact_info: The entity's contact_info field (modified in place)
        channel: Channel name
        info: Info dict to merge in

    Returns:
        Updated contact_info
    """
    if "channels" not in contact_info:
        contact_info["channels"] = {}

    existing = contact_info["channels"].get(channel, {})
    contact_info["channels"][channel] = _deep_merge(existing, info)
    return contact_info


def get_preferred_channel(contact_info: Dict[str, Any]) -> Optional[str]:
    """
    Get the preferred contact channel.

    Args:
        contact_info: The entity's contact_info field

    Returns:
        Preferred channel name, or None if not set
    """
    return contact_info.get("preferred_channel")


def get_room_id(
    contact_info: Dict[str, Any],
    channel: str,
    counterpart_id: str,
) -> Optional[str]:
    """
    Get the conversation ID with a specific counterpart on a specific channel.

    Args:
        contact_info: The entity's contact_info field
        channel: Channel name
        counterpart_id: The counterpart's identifier

    Returns:
        Conversation ID (e.g. Matrix room_id), or None
    """
    channel_info = get_channel_info(contact_info, channel)
    if not channel_info:
        return None
    return channel_info.get("rooms", {}).get(counterpart_id)


def set_room_id(
    contact_info: Dict[str, Any],
    channel: str,
    counterpart_id: str,
    room_id: str,
) -> Dict[str, Any]:
    """
    Write a conversation ID with a counterpart into contact_info.

    Args:
        contact_info: The entity's contact_info field (modified in place)
        channel: Channel name
        counterpart_id: The counterpart's identifier
        room_id: Conversation ID

    Returns:
        Updated contact_info
    """
    return set_channel_info(contact_info, channel, {
        "rooms": {counterpart_id: room_id}
    })


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep-merge two dicts. Values in override take precedence.

    Nested dicts are merged recursively; other types are overwritten.

    Args:
        base: Base dict
        override: Override dict

    Returns:
        New merged dict
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
