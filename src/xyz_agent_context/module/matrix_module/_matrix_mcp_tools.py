"""
@file_name: _matrix_mcp_tools.py
@author: Bin Liang
@date: 2026-03-10
@description: MCP atomic tools for Matrix operations (matrix_* prefix)

These tools map directly to NexusMatrix Server API endpoints.
The matrix_ prefix avoids collision with future slack_*, email_*, etc.

Each tool creates its own NexusMatrixClient from the credential stored
in the MCP-level database client. The agent_id is used to look up credentials.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger


def register_matrix_mcp_tools(mcp: Any) -> None:
    """
    Register all Matrix MCP tools on the given MCP server instance.

    Called by MatrixModule.create_mcp_server().

    Args:
        mcp: The FastMCP server instance
    """

    @mcp.tool()
    async def matrix_send_message(
        agent_id: str,
        room_id: str,
        content: str,
    ) -> dict:
        """
        Send a text message to a Matrix room.

        Use this tool to reply to messages or initiate conversations in Matrix rooms.

        Args:
            agent_id: Your agent ID (for credential lookup)
            room_id: Target room ID (e.g. "!abc123:matrix.example.com")
            content: Message text to send

        Returns:
            Result dict with event_id on success, or error details
        """
        client, cred = await _get_client_and_cred(agent_id)
        if not client:
            return {"success": False, "error": "Matrix credentials not found"}

        result = await client.send_message(
            api_key=cred.api_key,
            room_id=room_id,
            content=content,
        )
        await client.close()

        if result:
            return {"success": True, "data": result}
        return {"success": False, "error": "Failed to send message"}

    @mcp.tool()
    async def matrix_get_messages(
        agent_id: str,
        room_id: str,
        limit: int = 20,
    ) -> dict:
        """
        Get recent messages from a Matrix room.

        Use this to read conversation history or check for new messages.

        Args:
            agent_id: Your agent ID
            room_id: Room ID to fetch messages from
            limit: Maximum number of messages to return (default 20)

        Returns:
            Result dict with messages list
        """
        client, cred = await _get_client_and_cred(agent_id)
        if not client:
            return {"success": False, "error": "Matrix credentials not found"}

        messages = await client.get_messages(
            api_key=cred.api_key,
            room_id=room_id,
            limit=limit,
        )
        await client.close()

        return {"success": True, "messages": messages or []}

    @mcp.tool()
    async def matrix_create_room(
        agent_id: str,
        invite_user_id: str,
        name: str = "",
    ) -> dict:
        """
        Create a new Matrix room and optionally invite a user.

        Use this to start a new conversation with another agent.

        Args:
            agent_id: Your agent ID
            invite_user_id: Matrix user ID to invite (e.g. "@other:matrix.example.com")
            name: Optional room name

        Returns:
            Result dict with room_id on success
        """
        client, cred = await _get_client_and_cred(agent_id)
        if not client:
            return {"success": False, "error": "Matrix credentials not found"}

        result = await client.create_room(
            api_key=cred.api_key,
            name=name,
            invite_user_ids=[invite_user_id] if invite_user_id else None,
            is_direct=True,
        )
        await client.close()

        if result:
            return {"success": True, "data": result}
        return {"success": False, "error": "Failed to create room"}

    @mcp.tool()
    async def matrix_join_room(
        agent_id: str,
        room_id: str,
    ) -> dict:
        """
        Join a Matrix room.

        Use this to join a room you've been invited to.

        Args:
            agent_id: Your agent ID
            room_id: Room ID or alias to join

        Returns:
            Result dict indicating success or failure
        """
        client, cred = await _get_client_and_cred(agent_id)
        if not client:
            return {"success": False, "error": "Matrix credentials not found"}

        ok = await client.join_room(api_key=cred.api_key, room_id=room_id)
        await client.close()

        return {"success": ok}

    @mcp.tool()
    async def matrix_list_rooms(
        agent_id: str,
    ) -> dict:
        """
        List all Matrix rooms you've joined.

        Args:
            agent_id: Your agent ID

        Returns:
            Result dict with rooms list
        """
        client, cred = await _get_client_and_cred(agent_id)
        if not client:
            return {"success": False, "error": "Matrix credentials not found"}

        rooms = await client.list_rooms(api_key=cred.api_key)
        await client.close()

        return {"success": True, "rooms": rooms or []}

    @mcp.tool()
    async def matrix_get_room_members(
        agent_id: str,
        room_id: str,
    ) -> dict:
        """
        Get the member list of a Matrix room.

        Args:
            agent_id: Your agent ID
            room_id: Room ID

        Returns:
            Result dict with members list
        """
        client, cred = await _get_client_and_cred(agent_id)
        if not client:
            return {"success": False, "error": "Matrix credentials not found"}

        members = await client.get_room_members(api_key=cred.api_key, room_id=room_id)
        await client.close()

        return {"success": True, "members": members or []}

    @mcp.tool()
    async def matrix_search_agents(
        agent_id: str,
        query: str = "",
        capabilities: str = "",
    ) -> dict:
        """
        Search for agents in the NexusMatrix registry using semantic search.

        Use this to discover other agents you might want to communicate with.

        Args:
            agent_id: Your agent ID
            query: Search query (natural language description of what you're looking for)
            capabilities: Comma-separated capability filter (e.g. "chat,data_analysis")

        Returns:
            Result dict with matching agents
        """
        client, cred = await _get_client_and_cred(agent_id)
        if not client:
            return {"success": False, "error": "Matrix credentials not found"}

        cap_list = [c.strip() for c in capabilities.split(",") if c.strip()] if capabilities else None
        results = await client.search_agents(
            api_key=cred.api_key,
            query=query,
            capabilities=cap_list,
        )
        await client.close()

        return {"success": True, "agents": results or []}

    @mcp.tool()
    async def matrix_get_agent_profile(
        agent_id: str,
        target_agent_id: str,
    ) -> dict:
        """
        Get another agent's profile from the NexusMatrix registry.

        Args:
            agent_id: Your agent ID (for auth)
            target_agent_id: The agent whose profile you want to view (NexusAgent agent_id)

        Returns:
            Result dict with agent profile data
        """
        from xyz_agent_context.module.base import XYZBaseModule
        from ._matrix_credential_manager import MatrixCredentialManager

        client, cred = await _get_client_and_cred(agent_id)
        if not client:
            return {"success": False, "error": "Matrix credentials not found"}

        # Resolve target's NexusMatrix agent_id (agt_xxx) from credentials
        db = await XYZBaseModule.get_mcp_db_client()
        target_cred = await MatrixCredentialManager(db).get_credential(target_agent_id)
        nexus_target_id = target_cred.nexus_agent_id if target_cred else ""

        if not nexus_target_id:
            await client.close()
            return {"success": False, "error": f"Target agent {target_agent_id} has no NexusMatrix registration"}

        profile = await client.get_agent_profile(
            api_key=cred.api_key,
            agent_id=nexus_target_id,
        )
        await client.close()

        if profile:
            return {"success": True, "profile": profile}
        return {"success": False, "error": "Agent not found on NexusMatrix"}


    @mcp.tool()
    async def matrix_register(
        agent_id: str,
        force: bool = False,
    ) -> dict:
        """
        Register (or re-register) this agent on NexusMatrix Server.

        Use this when:
        - The agent has no Matrix credentials yet (registration failed at creation)
        - The agent needs to re-register (e.g. credentials expired, server changed)
        - The user explicitly asks to set up or reset Matrix communication

        Args:
            agent_id: Your agent ID
            force: If True, delete existing credentials and re-register from scratch

        Returns:
            Result dict with matrix_user_id on success, or error details

        Example:
            matrix_register(agent_id="your_agent_id")
            matrix_register(agent_id="your_agent_id", force=True)
        """
        from xyz_agent_context.module.base import XYZBaseModule
        from ._matrix_credential_manager import ensure_agent_registered
        from .matrix_module import DEFAULT_SERVER_URL

        try:
            db = await XYZBaseModule.get_mcp_db_client()
            cred = await ensure_agent_registered(db, agent_id, force=force)

            if cred:
                return {
                    "success": True,
                    "message": "Successfully registered on NexusMatrix Server",
                    "matrix_user_id": cred.matrix_user_id,
                    "server_url": cred.server_url,
                    "is_active": cred.is_active,
                }
            else:
                return {
                    "success": False,
                    "error": "Registration failed. Agent may not exist or NexusMatrix Server may be unavailable.",
                    "server_url": DEFAULT_SERVER_URL,
                }

        except Exception as e:
            logger.error(f"matrix_register failed for {agent_id}: {e}")
            return {"success": False, "error": f"Registration error: {str(e)}"}


async def _get_client_and_cred(agent_id: str):
    """
    Helper: look up Matrix credentials and create a client.
    Auto-registers on NexusMatrix if no credentials exist.

    Returns:
        Tuple of (NexusMatrixClient, MatrixCredential), or (None, None) if not found
    """
    from xyz_agent_context.module.base import XYZBaseModule
    from .matrix_client import NexusMatrixClient
    from ._matrix_credential_manager import ensure_agent_registered

    try:
        db = await XYZBaseModule.get_mcp_db_client()
        cred = await ensure_agent_registered(db, agent_id)
        if not cred:
            return None, None

        client = NexusMatrixClient(server_url=cred.server_url)
        return client, cred
    except Exception as e:
        logger.error(f"Failed to get Matrix client for agent {agent_id}: {e}")
        return None, None
