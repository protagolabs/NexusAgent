"""NexusAgent backend client for tau2-bench

This module provides a WebSocket client to interact with NexusAgent backend,
allowing tau2 to use NexusAgent as an LLM provider.

Tool Integration:
- tau2's environment tools are exposed through a temporary MCP server
- The MCP server URL is passed to NexusAgent in the WebSocket request
- NexusAgent can then discover and call these tools during execution
- The MCP server is automatically cleaned up after the session
"""

import asyncio
import json
from typing import Any, Optional

import httpx
import websockets
from loguru import logger

from tau2.data_model.message import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.environment.tool import Tool
from tau2.integrations.nexusagent.mcp_server import Tau2MCPServer


def normalize_order_id(arguments: dict) -> dict:
    """Normalize order_id in arguments to include '#' prefix if needed.

    The LLM sometimes strips the '#' prefix from retail order IDs, treating it
    as markdown syntax. This function ensures order IDs have the correct format.

    Args:
        arguments: Tool call arguments dict

    Returns:
        New dict with normalized order_id (or original dict if no normalization needed)
    """
    if 'order_id' not in arguments:
        return arguments

    order_id = arguments.get('order_id')
    if not isinstance(order_id, str) or not order_id or order_id.startswith('#'):
        return arguments

    # Check if this looks like a retail order ID (e.g., W1234567)
    if order_id[0].isalpha() and order_id[1:].isdigit():
        normalized_args = arguments.copy()
        normalized_args['order_id'] = f'#{order_id}'
        logger.debug(f"Normalized order_id in trajectory: {order_id} -> {normalized_args['order_id']}")
        return normalized_args

    return arguments


class NexusAgentClient:
    """WebSocket client for NexusAgent backend

    Args:
        backend_url: URL of NexusAgent backend (e.g., "ws://localhost:8000")
        agent_id: Agent ID in NexusAgent database
        user_id: User ID in NexusAgent database
        timeout: WebSocket timeout in seconds
        external_mcp_url: Optional external MCP server URL (if provided, won't start internal server)
    """

    def __init__(
        self,
        backend_url: str,
        agent_id: str,
        user_id: str,
        timeout: float = 300.0,
        external_mcp_url: Optional[str] = None,
    ):
        self.backend_url = backend_url
        self.agent_id = agent_id
        self.user_id = user_id
        self.timeout = timeout
        self.external_mcp_url = external_mcp_url
        # Convert ws:// or wss:// to http:// or https:// for REST API
        self.http_backend_url = backend_url.replace("ws://", "http://").replace("wss://", "https://")

    async def register_mcp_server(self, name: str, url: str) -> Optional[str]:
        """Register an MCP server URL in NarraNexus database via REST API.

        The latest NarraNexus loads MCP URLs from the database (not from WebSocket payload),
        so we must register the tau2 MCP server before starting the agent session.

        Args:
            name: MCP server name (e.g., "tau2_tools")
            url: MCP server SSE URL

        Returns:
            mcp_id if registration succeeded, None otherwise
        """
        api_url = f"{self.http_backend_url}/api/agents/{self.agent_id}/mcps?user_id={self.user_id}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    api_url,
                    json={
                        "name": name,
                        "url": url,
                        "description": "tau2-bench tools (temporary)",
                        "is_enabled": True,
                    }
                )

                if response.status_code == 200:
                    result = response.json()
                    if result.get("success") and result.get("mcp"):
                        mcp_id = result["mcp"]["mcp_id"]
                        logger.info(f"✓ Registered MCP server '{name}' in database (mcp_id={mcp_id})")
                        return mcp_id
                    else:
                        logger.error(f"Failed to register MCP: {result.get('error', 'Unknown error')}")
                        return None
                else:
                    logger.error(f"HTTP error registering MCP: {response.status_code} {response.text}")
                    return None

        except Exception as e:
            logger.error(f"Exception registering MCP server: {e}")
            return None

    async def unregister_mcp_server(self, mcp_id: str) -> bool:
        """Remove a previously registered MCP server from NarraNexus database.

        Args:
            mcp_id: The MCP ID returned by register_mcp_server()

        Returns:
            True if deletion succeeded, False otherwise
        """
        api_url = f"{self.http_backend_url}/api/agents/{self.agent_id}/mcps/{mcp_id}?user_id={self.user_id}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.delete(api_url)

                if response.status_code == 200:
                    result = response.json()
                    if result.get("success"):
                        logger.info(f"✓ Unregistered MCP server (mcp_id={mcp_id})")
                        return True
                    else:
                        logger.error(f"Failed to unregister MCP: {result.get('error', 'Unknown error')}")
                        return False
                else:
                    logger.error(f"HTTP error unregistering MCP: {response.status_code}")
                    return False

        except Exception as e:
            logger.error(f"Exception unregistering MCP server: {e}")
            return False

    async def update_agent_awareness(self, awareness_content: str) -> bool:
        """Update agent awareness via REST API

        This stores the system context (instructions + policy) in agent's awareness,
        so the agent can reference it during execution without needing to include
        it in every input message.

        Args:
            awareness_content: Complete system context to set as agent awareness

        Returns:
            True if update was successful, False otherwise
        """
        url = f"{self.http_backend_url}/api/agents/{self.agent_id}/awareness"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    url,
                    json={"awareness": awareness_content}
                )

                if response.status_code == 200:
                    result = response.json()
                    if result.get("success"):
                        logger.info(f"✓ Agent awareness updated successfully ({len(awareness_content)} chars)")
                        logger.debug(f"Awareness content preview:\n{awareness_content[:200]}...")
                        return True
                    else:
                        logger.error(f"Failed to update agent awareness: {result.get('error', 'Unknown error')}")
                        return False
                else:
                    logger.error(f"HTTP error updating agent awareness: {response.status_code}")
                    logger.error(f"Response: {response.text}")
                    return False

        except Exception as e:
            logger.error(f"Exception updating agent awareness: {e}")
            return False

    async def generate(
        self,
        messages: list[Message],
        tools: Optional[list[Tool]] = None,
        max_retries: int = 2,
        **kwargs: Any,
    ) -> AssistantMessage:
        """Generate response using NexusAgent backend

        Args:
            messages: List of tau2 messages (conversation history)
            tools: List of tau2 tools (will be exposed via temporary MCP server)
            max_retries: Maximum number of retries on failure (default: 2)
            **kwargs: Additional arguments

        Returns:
            AssistantMessage with the response from NexusAgent

        Raises:
            ConnectionError: If unable to connect to NexusAgent backend
            TimeoutError: If request times out
            ValueError: If response format is invalid
        """
        last_error = None

        for attempt in range(max_retries + 1):
            if attempt > 0:
                logger.warning(f"Retry attempt {attempt}/{max_retries} after error: {last_error}")
                # Wait before retrying (exponential backoff)
                wait_time = min(2 ** attempt, 10)  # Cap at 10 seconds
                logger.info(f"Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)

            try:
                return await self._generate_once(messages, tools, **kwargs)
            except ValueError as e:
                error_str = str(e)
                # Transient errors that are worth retrying
                retryable_patterns = ["TaskGroup", "Claude API error: unknown", "Claude API error: server_error", "Claude API error: rate_limit"]
                if any(pattern in error_str for pattern in retryable_patterns):
                    last_error = e
                    logger.warning(f"Retryable error on attempt {attempt + 1}/{max_retries}: {e}")
                    continue
                else:
                    # Permanent errors (auth, billing, invalid_request) — don't retry
                    raise
            except (ConnectionError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as e:
                # These errors might be temporary, retry
                last_error = e
                logger.warning(f"Connection/timeout error on attempt {attempt + 1}: {e}")
                if attempt < max_retries:
                    continue
                else:
                    raise

        # If we get here, all retries failed
        logger.error(f"All {max_retries + 1} attempts failed. Last error: {last_error}")
        raise last_error or RuntimeError("All retry attempts failed")

    async def _generate_once(
        self,
        messages: list[Message],
        tools: Optional[list[Tool]] = None,
        **kwargs: Any,  # Reserved for future extensions
    ) -> AssistantMessage:
        """Internal method: Generate response using NexusAgent backend (single attempt)

        This is the actual implementation, called by generate() with retry logic.

        Args:
            messages: List of tau2 messages
            tools: List of tau2 tools
            **kwargs: Reserved for future extensions (currently unused)
        """
        # Note: kwargs is currently unused but kept for API compatibility
        logger.info(f"Connecting to NexusAgent backend at {self.backend_url}")

        # Extract and update system context to agent awareness
        system_context = self._extract_system_context(messages)
        if system_context:
            logger.info("Updating agent awareness with system context...")
            await self.update_agent_awareness(system_context)

        # Convert tau2 messages to NexusAgent input format
        # Note: SystemMessage is now excluded because it's in agent awareness
        # use_native_multiturn=True: Only send latest user message, NexusAgent manages session internally
        input_content = self._convert_messages_to_input(messages, exclude_system=True, use_native_multiturn=True)
        logger.debug(f"Input content: {input_content}")

        # MCP server setup: use external server if provided, otherwise start internal one
        # IMPORTANT: Latest NarraNexus loads MCP URLs from database, NOT from WebSocket payload.
        # We register the MCP URL via REST API before connecting, and clean up after.
        mcp_server = None
        registered_mcp_id = None  # Track registered MCP for cleanup

        if self.external_mcp_url:
            # Use external MCP server (managed by llm_utils.py global cache)
            logger.info(f"Using external MCP server at {self.external_mcp_url}")
            # Register in NarraNexus database so the runtime can discover it
            registered_mcp_id = await self.register_mcp_server("tau2_tools", self.external_mcp_url)
            if not registered_mcp_id:
                logger.warning("Failed to register external MCP server in database, agent may not have tools")
        elif tools and len(tools) > 0:
            # Start temporary internal MCP server
            logger.info(f"Starting temporary MCP server for {len(tools)} tau2 tools")
            mcp_server = Tau2MCPServer(tools)
            mcp_server.start()

            if not mcp_server.is_alive():
                logger.error("MCP server thread failed to start")
                raise RuntimeError("MCP server thread failed to start")

            # Quick verification
            logger.debug("Quick MCP server verification...")
            max_wait = 2.0
            wait_interval = 0.2
            elapsed = 0.0

            while elapsed < max_wait:
                if mcp_server._is_server_responding():
                    logger.debug(f"✓ MCP server verified after {elapsed:.1f}s")
                    break
                await asyncio.sleep(wait_interval)
                elapsed += wait_interval

            if elapsed >= max_wait:
                logger.warning(f"Quick verification timed out, but server thread is alive - proceeding anyway")

            mcp_url = mcp_server.get_url()
            logger.info(f"✓ Temporary MCP server ready at {mcp_url}")
            logger.info(f"✓ Tools available: {[tool.name for tool in tools]}")

            # Register in NarraNexus database
            registered_mcp_id = await self.register_mcp_server("tau2_tools", mcp_url)
            if not registered_mcp_id:
                logger.warning("Failed to register internal MCP server in database, agent may not have tools")

        # Build WebSocket request (no mcp_urls field — latest NarraNexus loads from DB)
        request_data = {
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "input_content": input_content,
            "working_source": "chat",
        }

        # Connect and send request
        ws_endpoint = f"{self.backend_url}/ws/agent/run"
        # Track thinking/response content (internal, not shown to user)
        internal_thinking = ""
        internal_response = ""
        # Track all tool calls to extract send_message_to_user_directly
        tool_calls = []
        # NEW: Track tool results by tool_id for Bug #2 fix
        tool_results_map = {}  # {tool_id: result_string}
        current_tool_id = None  # Track current executing tool
        # Track the final user-visible response content
        user_visible_content = None

        try:
            async with websockets.connect(
                ws_endpoint,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=10,
            ) as websocket:
                # Send request
                await websocket.send(json.dumps(request_data))
                logger.debug("Request sent to NexusAgent")

                # Receive streaming responses
                async for message_str in websocket:
                    try:
                        message_data = json.loads(message_str)
                        message_type = message_data.get("type")

                        logger.debug(f"Received message type: {message_type}")

                        if message_type == "progress":
                            # Progress update - contains tool call information
                            # IMPORTANT: This is where we capture ALL tool calls (MCP and internal)
                            details = message_data.get("details")
                            if details and isinstance(details, dict):
                                # Extract tool_call_id from details (now included by response_processor)
                                tool_call_id = details.get("tool_call_id")

                                # Case 1: Tool call (has tool_name and arguments)
                                tool_name = details.get("tool_name")
                                if tool_name:
                                    arguments = details.get("arguments", {})

                                    # Use tool_call_id from details, or generate fallback
                                    if not tool_call_id:
                                        tool_call_id = f"call_{len(tool_calls)}"
                                        logger.warning(f"No tool_call_id in details, generated fallback: {tool_call_id}")

                                    # Check if this is send_message_to_user_directly (user-visible content)
                                    if tool_name.endswith("send_message_to_user_directly"):
                                        if "content" in arguments:
                                            user_visible_content = arguments["content"]
                                            logger.info(f"Extracted user-visible content from progress: {user_visible_content[:100]}")
                                        # Don't record send_message_to_user_directly as a tool call
                                        # (it's internal communication, not a tau2 environment tool)
                                    else:
                                        # This is an external tool call (MCP tool from tau2)
                                        # Record it so tau2 can replay it during evaluation

                                        # IMPORTANT: Remove MCP prefix from tool name
                                        # MCP tools are named like "mcp__tau2_tools__get_user_details"
                                        # but tau2 environment expects "get_user_details"
                                        clean_tool_name = tool_name
                                        if "mcp__tau2_tools__" in tool_name:
                                            clean_tool_name = tool_name.replace("mcp__tau2_tools__", "")
                                            logger.info(f"Stripped MCP prefix: {tool_name} -> {clean_tool_name}")

                                        logger.info(f"Captured MCP tool call [{tool_call_id}]: {clean_tool_name}")

                                        # Check if we already recorded this tool call
                                        # (avoid duplicates if multiple progress messages for same tool)
                                        if not any(tc.id == tool_call_id for tc in tool_calls):
                                            # Normalize arguments for trajectory recording
                                            # This ensures order_id has '#' prefix for retail domain
                                            normalized_args = normalize_order_id(arguments)

                                            tool_calls.append(ToolCall(
                                                id=tool_call_id,
                                                name=clean_tool_name,
                                                arguments=normalized_args,
                                            ))

                                    # Track this tool_call_id for result association
                                    current_tool_id = tool_call_id

                                # Case 2: Tool output (has output and tool_call_id)
                                output = details.get("output")
                                if output is not None:
                                    # Use tool_call_id from details (for tool output messages)
                                    # or current_tool_id (for immediate output in same message)
                                    result_tool_id = tool_call_id or current_tool_id

                                    if result_tool_id:
                                        # Store tool result with proper ID matching
                                        tool_results_map[result_tool_id] = str(output)
                                        logger.info(f"Captured tool result [{result_tool_id}]: {str(output)[:200]}...")
                                    else:
                                        logger.warning(f"Tool output received but no tool_call_id available: {str(output)[:100]}...")

                            step = message_data.get("step", "")
                            logger.debug(f"NexusAgent progress: {step}")

                        elif message_type == "agent_thinking":
                            # Agent thinking - internal only, not shown to user in tau2
                            thinking = message_data.get("thinking_content", message_data.get("content", ""))
                            internal_thinking += thinking
                            logger.debug(f"Agent thinking (internal): {thinking[:100]}...")

                        elif message_type == "agent_response":
                            # Agent response - internal only, not shown to user in tau2
                            # This contains the agent's reasoning/thinking process
                            delta = message_data.get("delta", message_data.get("content", ""))
                            internal_response += delta
                            logger.debug(f"Agent response (internal): {delta[:100]}...")

                        elif message_type == "tool_call":
                            # Tool call from agent - deprecated format, kept for compatibility
                            tool_name = message_data.get("tool_name", "")
                            tool_args = message_data.get("arguments", {})
                            tool_id = message_data.get("tool_id", f"call_{len(tool_calls)}")

                            # Track current tool ID for result association
                            current_tool_id = tool_id

                            logger.debug(f"Tool call: {tool_name} with args {tool_args}")

                            # Check if this is send_message_to_user_directly (user-visible content)
                            if tool_name.endswith("send_message_to_user_directly"):
                                if "content" in tool_args:
                                    user_visible_content = tool_args["content"]
                                    logger.info(f"Extracted user-visible content from tool_call: {user_visible_content[:100]}")
                                # Don't record send_message_to_user_directly as a tool call
                                # (it's internal communication, not a tau2 environment tool)
                            else:
                                # Strip MCP prefix (same as progress path)
                                clean_tool_name = tool_name
                                if "mcp__tau2_tools__" in tool_name:
                                    clean_tool_name = tool_name.replace("mcp__tau2_tools__", "")
                                    logger.info(f"Stripped MCP prefix: {tool_name} -> {clean_tool_name}")

                                # Dedup check (avoid duplicates if both progress and tool_call arrive)
                                if not any(tc.id == tool_id for tc in tool_calls):
                                    normalized_args = normalize_order_id(tool_args)
                                    tool_calls.append(ToolCall(
                                        id=tool_id,
                                        name=clean_tool_name,
                                        arguments=normalized_args,
                                    ))

                        elif message_type == "error":
                            # Error from NexusAgent
                            error_msg = message_data.get("error_message", "Unknown error")
                            error_type = message_data.get("error_type", "Error")
                            error_traceback = message_data.get("traceback", "")

                            logger.error(f"NexusAgent error ({error_type}): {error_msg}")
                            if error_traceback:
                                logger.error(f"Traceback from NexusAgent:\n{error_traceback}")

                            # Provide more context for common errors
                            if "TaskGroup" in error_msg or "TaskGroup" in error_type:
                                logger.error(
                                    "TaskGroup error detected - this usually indicates:\n"
                                    "1. MCP server connection failed\n"
                                    "2. Async task exception in Claude Agent SDK\n"
                                    "3. Resource cleanup issue\n"
                                    "Check NexusAgent backend logs for more details."
                                )

                            raise ValueError(f"NexusAgent error: {error_msg}")

                        elif message_type == "complete":
                            # Execution complete
                            logger.info("NexusAgent execution completed")
                            break

                        else:
                            # Unknown message type - log it
                            logger.warning(f"Unknown message type: {message_type}")
                            logger.debug(f"Full message: {json.dumps(message_data)[:200]}")

                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse message: {e}")
                        continue

        except websockets.exceptions.WebSocketException as e:
            logger.error(f"WebSocket error: {e}")
            raise ConnectionError(f"Failed to connect to NexusAgent: {e}")
        except asyncio.TimeoutError:
            logger.error(f"Request timed out after {self.timeout} seconds")
            raise TimeoutError(f"NexusAgent request timed out")
        finally:
            # Clean up: unregister MCP from database first, then stop server
            if registered_mcp_id:
                logger.info(f"Unregistering MCP server (mcp_id={registered_mcp_id})")
                try:
                    await self.unregister_mcp_server(registered_mcp_id)
                except Exception as e:
                    logger.error(f"Error unregistering MCP server: {e}")

            # Clean up temporary MCP server (only if using internal server)
            # External servers are managed by llm_utils.py and should NOT be stopped here
            if mcp_server is not None:
                logger.info("Stopping temporary internal MCP server")
                try:
                    mcp_server.stop(timeout=10.0)
                except Exception as e:
                    logger.error(f"Error stopping MCP server: {e}")
                    # Don't fail the whole request due to cleanup error

        # Build AssistantMessage
        # Following the same logic as frontend (chatStore.ts):
        # Only return the content from send_message_to_user_directly tool call
        # Internal thinking/response is NOT included in the final response
        final_content = user_visible_content

        if final_content is None:
            # No send_message_to_user_directly was called
            # This matches frontend behavior: "(Agent decided no response needed)"
            logger.warning("No send_message_to_user_directly tool call found")
            final_content = "(Agent decided no response needed)"

        logger.info(f"Final user-visible content: {final_content[:200]}...")
        logger.debug(f"Internal thinking length: {len(internal_thinking)} chars (not returned to tau2)")
        logger.debug(f"Internal response length: {len(internal_response)} chars (not returned to tau2)")
        logger.info(f"Captured {len(tool_calls)} MCP tool calls (already executed by NexusAgent)")

        # IMPORTANT: NexusAgent executes tools internally via MCP and manages its own session.
        # For tau2 benchmark evaluation, we need to record tool calls in the trajectory.
        #
        # Strategy:
        # - Return tool_calls to tau2 orchestrator for recording in trajectory
        # - Set special flags in raw_data so orchestrator knows:
        #   1. Tools are already executed (tools_already_executed=True)
        #   2. This is the final response with user-visible content (nexusagent_complete=True)
        #   3. Don't loop back to agent after executing tools
        has_external_tools = len(tool_calls) > 0

        if has_external_tools:
            # Return tool calls for tau2 to record in trajectory
            # Orchestrator will execute them (to update tau2's database) but should not loop back
            message_content = None
            message_tool_calls = tool_calls
            logger.info(f"Returning {len(tool_calls)} tool calls for tau2 trajectory recording")
        else:
            # No tool calls, return user-visible content directly
            message_content = final_content
            message_tool_calls = None
            logger.info("Returning user-visible content (no tool calls)")

        return AssistantMessage(
            role="assistant",
            content=message_content,
            tool_calls=message_tool_calls,
            cost=None,  # NexusAgent doesn't provide cost info
            usage=None,  # NexusAgent doesn't provide usage info
            raw_data={
                "source": "nexusagent",
                "backend_url": self.backend_url,
                "agent_id": self.agent_id,
                "internal_thinking": internal_thinking,  # Preserved for debugging
                "internal_response": internal_response,  # Preserved for debugging
                "user_visible_content": final_content,  # The actual final response for user
                # Flags for orchestrator:
                "tools_already_executed": True if has_external_tools else False,
                "tool_results": tool_results_map,  # {tool_id: result_string}
                "nexusagent_complete": True,  # Signal: this is a complete response, don't loop back
            },
        )

    def _extract_system_context(self, messages: list[Message]) -> Optional[str]:
        """Extract system context from messages

        Args:
            messages: List of tau2 Message objects

        Returns:
            System context string if found, None otherwise
        """
        for msg in messages:
            if isinstance(msg, SystemMessage):
                return msg.content
        return None

    def _convert_messages_to_input(self, messages: list[Message], exclude_system: bool = False, use_native_multiturn: bool = True) -> str:
        """Convert tau2 messages to NexusAgent input string

        Args:
            messages: List of tau2 Message objects
            exclude_system: If True, skip SystemMessage (used when system context is in agent awareness)
            use_native_multiturn: If True, only send the latest user message and rely on NexusAgent's
                                 session management for conversation history (default: True)

        Returns:
            Formatted string for NexusAgent input_content
        """
        parts = []

        if use_native_multiturn:
            # Use NexusAgent's native multi-turn conversation capability
            # Only send the latest user message, NexusAgent will handle history via its session
            logger.info("Using NexusAgent's native multi-turn conversation capability")

            # Find the latest UserMessage
            latest_user_message = None
            for msg in reversed(messages):
                if isinstance(msg, UserMessage):
                    latest_user_message = msg
                    break

            if latest_user_message:
                parts.append(f"{latest_user_message.content}")
            else:
                # No user message found, send a default
                logger.warning("No UserMessage found in message history")
                parts.append("Hello")
        else:
            # Legacy mode: send full conversation history
            logger.info("Using legacy mode: sending full conversation history")

            for msg in messages:
                if isinstance(msg, SystemMessage):
                    # System messages provide context
                    # Skip if exclude_system=True (context is already in agent awareness)
                    if not exclude_system:
                        parts.append(f"[System Context]\n{msg.content}\n")

                elif isinstance(msg, UserMessage):
                    # User messages are the main input
                    parts.append(f"{msg.content}")

                elif isinstance(msg, AssistantMessage):
                    # Previous assistant responses provide history
                    if msg.content:
                        parts.append(f"[Previous Response]\n{msg.content}\n")

                    # Include tool calls if any
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            parts.append(
                                f"[Tool Used: {tc.name}]\n"
                                f"Arguments: {json.dumps(tc.arguments)}\n"
                            )

                elif isinstance(msg, ToolMessage):
                    # Tool results from previous interactions
                    tool_name = getattr(msg, 'tool_name', 'unknown')
                    parts.append(
                        f"[Tool Result: {tool_name}]\n"
                        f"{msg.content}\n"
                    )

        # Join all parts with newlines
        input_content = "\n".join(parts).strip()

        # If no content, provide a default
        if not input_content:
            input_content = "Hello"

        return input_content
