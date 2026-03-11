"""
@file_name: matrix_module.py
@author: Bin Liang
@date: 2026-03-10
@description: MatrixModule — first IM channel module implementation

MatrixModule integrates NexusMatrix Server as a system-level Module (not a Skill).
It provides:
- MCP Tools: matrix_send_message, matrix_create_room, matrix_search_agents, etc.
- Hooks: data_gathering (inject Matrix context), after_execution (mark_read)
- Trigger: MatrixTrigger (separate background process for message polling)

MatrixModule is a peer of JobModule and ChatModule — it hooks into the AgentRuntime
pipeline via the standard Module lifecycle.
"""

from __future__ import annotations

from typing import Any, List, Optional

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
)
from xyz_agent_context.channel.channel_sender_registry import ChannelSenderRegistry

from ._matrix_hooks import matrix_hook_data_gathering, matrix_hook_after_event_execution
from ._matrix_credential_manager import MatrixCredentialManager
from .matrix_client import NexusMatrixClient


# Default NexusMatrix server URL (local deployment)
DEFAULT_SERVER_URL = "http://localhost:8953"

# MCP server port for Matrix tools
MATRIX_MCP_PORT = 7810


class MatrixModule(XYZBaseModule):
    """
    Matrix communication module.

    Enables Agents to communicate with each other via the NexusMatrix Server
    (Matrix protocol). Provides MCP tools for messaging, room management,
    and agent discovery.

    Instance level: Agent-level (one per Agent, is_public=True).
    """

    # Class-level flag to ensure channel sender is registered only once
    _sender_registered: bool = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Register Matrix channel sender on first instantiation
        if not MatrixModule._sender_registered:
            MatrixModule.register_channel_sender()
            MatrixModule._sender_registered = True

    # =========================================================================
    # Configuration
    # =========================================================================

    def get_config(self) -> ModuleConfig:
        """Return Module configuration."""
        return ModuleConfig(
            name="MatrixModule",
            priority=5,  # Lower priority than Chat/SocialNetwork, higher than RAG
            enabled=True,
            description=(
                "Matrix communication channel for inter-Agent messaging. "
                "Provides tools for sending/receiving messages, managing rooms, "
                "and discovering other agents via NexusMatrix Server."
            ),
            module_type="capability",
        )

    # =========================================================================
    # MCP Server
    # =========================================================================

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """Return MCP Server configuration for Matrix tools."""
        return MCPServerConfig(
            server_name="matrix_module",
            server_url=f"http://localhost:{MATRIX_MCP_PORT}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        """Create MCP Server with Matrix tools registered."""
        try:
            from fastmcp import FastMCP

            mcp = FastMCP(
                "MatrixModule MCP",
            )
            mcp.settings.port = MATRIX_MCP_PORT

            # Register all matrix_* tools
            from ._matrix_mcp_tools import register_matrix_mcp_tools
            register_matrix_mcp_tools(mcp)

            logger.info(f"MatrixModule MCP server created on port {MATRIX_MCP_PORT}")
            return mcp

        except Exception as e:
            logger.error(f"Failed to create MatrixModule MCP server: {e}")
            return None

    # =========================================================================
    # Instructions
    # =========================================================================

    async def get_instructions(self, ctx_data: ContextData) -> str:
        """
        Return Matrix-specific instructions for the system prompt.

        When credentials exist: show full Matrix tools and context.
        When credentials are missing: only show matrix_register tool.
        """
        # Check if Matrix info was injected by hook_data_gathering
        matrix_info = ctx_data.extra_data.get("matrix_info")

        # Common tool list (always available regardless of credential state)
        tools_section = """Available tools (prefix: matrix_*):
- `matrix_register`: Register (or re-register) on NexusMatrix Server
- `matrix_send_message`: Send a message to a room
- `matrix_create_room`: Create a new room and invite someone
- `matrix_join_room`: Join a room you've been invited to
- `matrix_list_rooms`: List your joined rooms
- `matrix_get_room_members`: Get members of a room
- `matrix_search_agents`: Search for agents in the registry
- `matrix_get_agent_profile`: View another agent's profile"""

        if not matrix_info:
            # No credentials — show full tools but guide to register first
            return f"""
## Matrix Communication
You are NOT yet registered on NexusMatrix Server. You need to call `matrix_register` first before using other Matrix tools.

{tools_section}

**First step**: Call `matrix_register` to get your Matrix account. After that, you can use all other tools above.
"""

        matrix_id = matrix_info.get("matrix_user_id", "")
        rooms = matrix_info.get("joined_rooms", [])
        siblings = ctx_data.extra_data.get("sibling_agents", [])

        instructions = f"""
## Matrix Communication
You have a Matrix account: `{matrix_id}`

You can communicate with other Agents or users via Matrix protocol.
{tools_section}
"""

        if rooms:
            instructions += "\n### Your Joined Rooms\n"
            for r in rooms[:10]:  # Cap at 10 rooms in instructions
                room_line = f"- `{r.get('room_id', '')}` — {r.get('name', 'unnamed')}"
                members = r.get("members", [])
                if members:
                    member_names = [
                        f"{m.get('agent_name', 'unknown')} (`{m.get('matrix_user_id', '')}`)"
                        for m in members
                    ]
                    room_line += f"  [Members: {', '.join(member_names)}]"
                instructions += room_line + "\n"

        # Agent directory — all registered agents with their identities
        agent_dir = matrix_info.get("agent_directory", {})
        if agent_dir:
            instructions += "\n### Agent Directory\n"
            for mid, info in list(agent_dir.items())[:15]:
                aid = info.get("agent_id", "")
                aname = info.get("agent_name", "Unknown")
                instructions += f"- **{aname}** — agent_id: `{aid}`, matrix_id: `{mid}`\n"

        if siblings:
            instructions += "\n### Known Sibling Agents\n"
            for s in siblings[:5]:  # Cap at 5 siblings
                name = s.get("name", "Unknown")
                mid = s.get("matrix", {}).get("user_id", "N/A")
                role = s.get("role", "")
                instructions += f"- **{name}** (`{mid}`) — {role}\n"

        return instructions

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Inject Matrix-specific context.

        Delegates to _matrix_hooks.matrix_hook_data_gathering.
        Only injects Matrix's own info — does NOT touch Social Network.
        """
        try:
            # Derive workspace paths from settings
            from xyz_agent_context.settings import settings
            base_workspace_path = settings.base_working_path
            workspace_path = ""
            if self.agent_id and self.user_id:
                import os
                workspace_path = os.path.join(
                    base_workspace_path, f"{self.agent_id}_{self.user_id}"
                )

            return await matrix_hook_data_gathering(
                agent_id=self.agent_id,
                db=self.db,
                ctx_data=ctx_data,
                workspace_path=workspace_path,
                base_workspace_path=base_workspace_path,
            )
        except Exception as e:
            logger.error(f"MatrixModule hook_data_gathering failed: {e}")
            return ctx_data

    async def hook_after_event_execution(self, params: HookAfterExecutionParams) -> None:
        """
        Post-execution cleanup for Matrix.

        Delegates to _matrix_hooks.matrix_hook_after_event_execution.
        Only does Matrix's own cleanup — entity extraction is SocialNetwork's job.
        """
        try:
            await matrix_hook_after_event_execution(params=params, db=self.db)
        except Exception as e:
            logger.error(f"MatrixModule hook_after_event_execution failed: {e}")

    # =========================================================================
    # Channel Sender Registration
    # =========================================================================

    @classmethod
    def register_channel_sender(cls) -> None:
        """
        Register the Matrix sender in ChannelSenderRegistry.

        Called once at module init. Enables contact_agent composite operation
        to route messages through Matrix.
        """
        async def matrix_send_to_agent(
            agent_id: str,
            target_id: str,
            message: str,
            room_id: str = "",
            **kwargs,
        ) -> dict:
            """
            Send a message to a target agent via Matrix.

            If room_id is provided, sends directly. Otherwise creates a new room.

            Args:
                agent_id: Sender agent ID
                target_id: Target Matrix user ID
                message: Message content
                room_id: Existing room ID (optional)

            Returns:
                Result dict
            """
            from xyz_agent_context.utils import get_db_client
            from ._matrix_credential_manager import ensure_agent_registered

            db = await get_db_client()
            cred = await ensure_agent_registered(db, agent_id)
            if not cred:
                return {"success": False, "error": "No Matrix credentials and auto-registration failed"}

            client = NexusMatrixClient(server_url=cred.server_url)
            try:
                # Create room if needed
                if not room_id:
                    result = await client.create_room(
                        api_key=cred.api_key,
                        invite_user_ids=[target_id],
                        is_direct=True,
                    )
                    if not result:
                        return {"success": False, "error": "Failed to create room"}
                    room_id = result.get("room_id", "")

                # Send message
                send_result = await client.send_message(
                    api_key=cred.api_key,
                    room_id=room_id,
                    content=message,
                )

                return {
                    "success": bool(send_result),
                    "room_id": room_id,
                    "data": send_result,
                }
            finally:
                await client.close()

        ChannelSenderRegistry.register("matrix", matrix_send_to_agent)
