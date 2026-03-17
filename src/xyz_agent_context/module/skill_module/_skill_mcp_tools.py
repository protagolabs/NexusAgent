"""
@file_name: _skill_mcp_tools.py
@author: Bin Liang
@date: 2026-03-17
@description: SkillModule MCP Server tool definitions

Stateless MCP tools — each tool accepts agent_id + user_id and constructs
a temporary SkillModule instance to access the correct skills directory.
This follows the same pattern as ChatModule/JobModule MCP tools.

Tools:
- skill_save_config: Save an environment variable for a skill
- skill_list_required_env: Query a skill's required env vars and config status
- skill_save_study_summary: Save a structured study summary for a skill
"""

from loguru import logger
from mcp.server.fastmcp import FastMCP


def _get_skill_module(agent_id: str, user_id: str):
    """Create a temporary SkillModule instance for the given agent+user."""
    from xyz_agent_context.module.skill_module.skill_module import SkillModule
    return SkillModule(agent_id=agent_id, user_id=user_id)


def create_skill_mcp_server(port: int) -> FastMCP:
    """
    Create a SkillModule MCP Server instance.

    Args:
        port: MCP Server port

    Returns:
        FastMCP instance with all tools configured
    """
    mcp = FastMCP("skill_module")
    mcp.settings.port = port

    @mcp.tool()
    async def skill_save_config(
        agent_id: str,
        user_id: str,
        skill_name: str,
        env_key: str,
        env_value: str,
    ) -> str:
        """
        Save an environment variable for a skill.

        Use this tool after obtaining credentials (API keys, tokens, etc.) during
        skill study or registration. The value will be stored securely and injected
        into the agent's environment at runtime. It will also appear as "configured"
        in the frontend Skills panel.

        Args:
            agent_id: Your agent ID.
            user_id: The user ID.
            skill_name: Name of the skill (directory name under skills/).
            env_key: Environment variable name (e.g. "ARENA_API_KEY").
            env_value: The value to store (e.g. "arena_sk_xxxxx").

        Returns:
            Confirmation message.
        """
        try:
            sm = _get_skill_module(agent_id, user_id)
            sm.set_skill_env_config(skill_name, {env_key: env_value})

            logger.info(f"SkillMCP: Saved env {env_key} for skill '{skill_name}' (agent={agent_id})")
            return f"Successfully saved {env_key} for skill '{skill_name}'. It will be injected at runtime."

        except Exception as e:
            logger.error(f"SkillMCP: Failed to save env config: {e}")
            return f"Failed to save config: {str(e)}"

    @mcp.tool()
    async def skill_list_required_env(
        agent_id: str,
        user_id: str,
        skill_name: str,
    ) -> str:
        """
        List the required environment variables for a skill and their configuration status.

        Use this tool to check what credentials a skill needs and which are already configured.

        Args:
            agent_id: Your agent ID.
            user_id: The user ID.
            skill_name: Name of the skill.

        Returns:
            A summary of required env vars and their status.
        """
        try:
            sm = _get_skill_module(agent_id, user_id)
            requirements = sm.get_skill_requirements(skill_name)
            env_config = sm.get_skill_env_config(skill_name)

            required_env = requirements.get("env", [])
            if not required_env:
                return f"Skill '{skill_name}' has no required environment variables."

            lines = [f"Required environment variables for '{skill_name}':"]
            for key in required_env:
                configured = key in env_config and env_config[key]
                status = "✓ configured" if configured else "✗ not configured"
                lines.append(f"  - {key}: {status}")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"SkillMCP: Failed to list required env: {e}")
            return f"Failed to query: {str(e)}"

    @mcp.tool()
    async def skill_save_study_summary(
        agent_id: str,
        user_id: str,
        skill_name: str,
        summary: str,
    ) -> str:
        """
        Save a structured study summary for a skill.

        Call this tool at the END of your skill study to record what you learned.
        The summary should be well-formatted Markdown covering:
        - What this skill does (core capabilities)
        - Any accounts/registrations you completed
        - Any scheduled jobs you created (with their schedules and purposes)
        - Any pending actions that require human intervention
        - Key commands or APIs you discovered

        This summary is displayed to the user in the Skills panel.

        Args:
            agent_id: Your agent ID.
            user_id: The user ID.
            skill_name: Name of the skill.
            summary: Markdown-formatted study summary.

        Returns:
            Confirmation message.
        """
        try:
            sm = _get_skill_module(agent_id, user_id)
            sm.set_study_status(skill_name, "completed", result=summary)

            logger.info(f"SkillMCP: Saved study summary for '{skill_name}' ({len(summary)} chars)")
            return f"Study summary saved for skill '{skill_name}'."

        except Exception as e:
            logger.error(f"SkillMCP: Failed to save study summary: {e}")
            return f"Failed to save summary: {str(e)}"

    return mcp
