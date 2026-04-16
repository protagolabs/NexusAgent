import asyncio
import json
import os
import re
from typing import Any, Optional

import httpx
import litellm
from litellm import completion, completion_cost
from litellm.caching.caching import Cache
from litellm.main import ModelResponse, Usage
from loguru import logger

from tau2.config import (
    DEFAULT_LLM_CACHE_TYPE,
    DEFAULT_MAX_RETRIES,
    LLM_CACHE_ENABLED,
    REDIS_CACHE_TTL,
    REDIS_CACHE_VERSION,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
    REDIS_PREFIX,
    USE_LANGFUSE,
)
from tau2.data_model.message import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.environment.tool import Tool

# Task-based MCP server cache for NexusAgent integration
# Each task gets its own MCP server to avoid thread safety issues
# while still benefiting from caching within a task
import threading
import atexit

# Cache structure: {task_id: {"server": MCP_Server, "tools_hash": hash}}
_TASK_MCP_SERVERS = {}
_MCP_SERVER_LOCK = threading.RLock()  # Reentrant lock for better thread safety

# Task-based agent_id cache for NexusAgent integration
# Each task_id maps to a single agent_id for multi-turn conversations
# Cache structure: {task_id: agent_id}
_TASK_AGENT_IDS = {}
_AGENT_ID_LOCK = threading.RLock()

# Import Claude SDK
try:
    from claude_agent_sdk import tool, create_sdk_mcp_server, ClaudeSDKClient
    from claude_agent_sdk.types import ClaudeAgentOptions
    import pydantic
    CLAUDE_SDK_AVAILABLE = True
except ImportError:
    CLAUDE_SDK_AVAILABLE = False
    logger.warning("Claude SDK not available. Install claude-agent-sdk to use Claude Code.")

# litellm._turn_on_debug()

if USE_LANGFUSE:
    # set callbacks
    litellm.success_callback = ["langfuse"]
    litellm.failure_callback = ["langfuse"]

litellm.drop_params = True

if LLM_CACHE_ENABLED:
    if DEFAULT_LLM_CACHE_TYPE == "redis":
        logger.info(f"LiteLLM: Using Redis cache at {REDIS_HOST}:{REDIS_PORT}")
        litellm.cache = Cache(
            type=DEFAULT_LLM_CACHE_TYPE,
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            namespace=f"{REDIS_PREFIX}:{REDIS_CACHE_VERSION}:litellm",
            ttl=REDIS_CACHE_TTL,
        )
    elif DEFAULT_LLM_CACHE_TYPE == "local":
        logger.info("LiteLLM: Using local cache")
        litellm.cache = Cache(
            type="local",
            ttl=REDIS_CACHE_TTL,
        )
    else:
        raise ValueError(
            f"Invalid cache type: {DEFAULT_LLM_CACHE_TYPE}. Should be 'redis' or 'local'"
        )
    litellm.enable_cache()
else:
    logger.info("LiteLLM: Cache is disabled")
    litellm.disable_cache()


ALLOW_SONNET_THINKING = False

if not ALLOW_SONNET_THINKING:
    logger.warning("Sonnet thinking is disabled")


def convert_tau_to_claude_tools(tau_tools: list[Tool]) -> list[Any]:
    """
    Convert tau2 tool list to Claude Agent SDK compatible tools.

    Args:
        tau_tools: List of tau2 Tool objects

    Returns:
        List of Claude Agent SDK tools
    """
    if not CLAUDE_SDK_AVAILABLE:
        raise ImportError("Claude SDK is not available. Cannot convert tools.")

    claude_tools = []

    for t in tau_tools:
        # ---------------------------------------------------------
        # 1. Extract Schema (critical step)
        # ---------------------------------------------------------
        try:
            if issubclass(t.params, pydantic.BaseModel):
                input_schema = t.params.model_json_schema()
            else:
                # Fallback for non-Pydantic params
                input_schema = {
                    "type": "object",
                    "properties": {},
                    "description": "Params extraction required custom logic"
                }
        except Exception:
            # If t.params is just a regular class, provide empty schema
            input_schema = {"type": "object", "properties": {}}

        if "title" in input_schema:
            del input_schema["title"]

        # Remove 'title' from nested properties as well
        if "properties" in input_schema:
            for _, prop_schema in input_schema["properties"].items():
                if isinstance(prop_schema, dict) and "title" in prop_schema:
                    del prop_schema["title"]

        # ---------------------------------------------------------
        # 2. Define execution logic wrapper
        # ---------------------------------------------------------
        # Create a closure to capture the current tool
        def make_tool_wrapper(tool_obj):
            async def tool_wrapper(arguments, context=None):
                """Execute the tau2 tool and return results for Claude SDK.

                Args:
                    arguments: Dictionary of tool arguments
                    context: Optional context object (unused)

                Note: This execution is needed so Claude Code sees real results.
                The Orchestrator will skip re-executing tools from Claude SDK messages
                (identified by raw_data["source"] == "claude_sdk").
                """
                logger.info(f"Executing {tool_obj.name} with {arguments}")

                try:
                    # Instantiate parameters and call the tool
                    params_obj = tool_obj.params(**arguments)
                    result = tool_obj._call(**params_obj.model_dump())
                    # Return in Claude SDK MCP tool format
                    return {
                        "content": [
                            {"type": "text", "text": str(result)}
                        ]
                    }
                except Exception as e:
                    error_msg = f"Error executing {tool_obj.name}: {str(e)}"
                    logger.error(error_msg)
                    return {
                        "content": [
                            {"type": "text", "text": error_msg}
                        ],
                        "isError": True
                    }

            return tool_wrapper

        # ---------------------------------------------------------
        # 3. Dynamically apply @tool decorator
        # ---------------------------------------------------------
        # Build full description: short + long + error info
        full_desc = t.short_desc
        if t.long_desc:
            full_desc += f"\nDetails: {t.long_desc}"

        # Add exception information to help the model understand error handling
        if hasattr(t, 'raises') and t.raises:
            error_descs = [f"- {e['type']}: {e['desc']}" for e in t.raises]
            full_desc += "\nPotential Errors:\n" + "\n".join(error_descs)

        # Apply the @tool decorator
        decorator = tool(
            name=t.name,
            description=full_desc,
            input_schema=input_schema
        )

        # Wrap the tool
        wrapped_tool = decorator(make_tool_wrapper(t))
        claude_tools.append(wrapped_tool)

    return claude_tools


def _parse_ft_model_name(model: str) -> str:
    """
    Parse the ft model name from the litellm model name.
    e.g: "ft:gpt-4.1-mini-2025-04-14:sierra::BSQA2TFg" -> "gpt-4.1-mini-2025-04-14"
    """
    pattern = r"ft:(?P<model>[^:]+):(?P<provider>\w+)::(?P<id>\w+)"
    match = re.match(pattern, model)
    if match:
        return match.group("model")
    else:
        return model


def get_response_cost(response: ModelResponse) -> float:
    """
    Get the cost of the response from the litellm completion.
    """
    response.model = _parse_ft_model_name(
        response.model
    )  # FIXME: Check Litellm, passing the model to completion_cost doesn't work.
    try:
        cost = completion_cost(completion_response=response)
    except Exception as e:
        logger.error(e)
        return 0.0
    return cost


def get_response_usage(response: ModelResponse) -> Optional[dict]:
    usage: Optional[Usage] = response.get("usage")
    if usage is None:
        return None
    return {
        "completion_tokens": usage.completion_tokens,
        "prompt_tokens": usage.prompt_tokens,
    }


def to_tau2_messages(
    messages: list[dict], ignore_roles: set[str] = set()
) -> list[Message]:
    """
    Convert a list of messages from a dictionary to a list of Tau2 messages.
    """
    tau2_messages = []
    for message in messages:
        role = message["role"]
        if role in ignore_roles:
            continue
        if role == "user":
            tau2_messages.append(UserMessage(**message))
        elif role == "assistant":
            tau2_messages.append(AssistantMessage(**message))
        elif role == "tool":
            tau2_messages.append(ToolMessage(**message))
        elif role == "system":
            tau2_messages.append(SystemMessage(**message))
        else:
            raise ValueError(f"Unknown message type: {role}")
    return tau2_messages


def to_litellm_messages(messages: list[Message]) -> list[dict]:
    """
    Convert a list of Tau2 messages to a list of litellm messages.
    """
    litellm_messages = []
    for message in messages:
        if isinstance(message, UserMessage):
            litellm_messages.append({"role": "user", "content": message.content})
        elif isinstance(message, AssistantMessage):
            tool_calls = None
            if message.is_tool_call():
                tool_calls = [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                        "type": "function",
                    }
                    for tc in message.tool_calls
                ]
            litellm_messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": tool_calls,
                }
            )
        elif isinstance(message, ToolMessage):
            litellm_messages.append(
                {
                    "role": "tool",
                    "content": message.content,
                    "tool_call_id": message.id,
                }
            )
        elif isinstance(message, SystemMessage):
            litellm_messages.append({"role": "system", "content": message.content})
    return litellm_messages


def _parse_tool_calls_from_text(text: str) -> list[ToolCall]:
    """
    Parse tool calls from text content in the format:
    TOOL_CALL: <tool_name>
    ARGUMENTS:
    {
      "param1": "value1"
    }
    """
    tool_calls = []

    # Split text by TOOL_CALL markers
    parts = re.split(r'TOOL_CALL:\s*', text)

    for i, part in enumerate(parts):
        if not part.strip():
            continue

        # Extract tool name (first line or word before ARGUMENTS)
        lines = part.split('\n')
        tool_name_line = lines[0].strip()

        # Tool name is everything before ARGUMENTS marker or newline
        if 'ARGUMENTS' in tool_name_line:
            tool_name = tool_name_line.split('ARGUMENTS')[0].strip()
        else:
            tool_name = tool_name_line.strip()

        # Find ARGUMENTS section
        arguments_match = re.search(r'ARGUMENTS:\s*(\{.*)', part, re.DOTALL)
        if not arguments_match:
            continue

        arguments_text = arguments_match.group(1)

        # Extract JSON by counting braces to handle nested objects
        brace_count = 0
        json_end = 0
        in_string = False
        escape_next = False

        for idx, char in enumerate(arguments_text):
            if escape_next:
                escape_next = False
                continue

            if char == '\\':
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string

            if not in_string:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        json_end = idx + 1
                        break

        if json_end == 0:
            logger.warning(f"Could not find matching braces for tool call: {tool_name}")
            continue

        arguments_str = arguments_text[:json_end].strip()

        try:
            arguments = json.loads(arguments_str)
            tool_calls.append(ToolCall(
                id=f"call_{i}",
                name=tool_name,
                arguments=arguments
            ))
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse tool call arguments for {tool_name}: {e}")
            logger.debug(f"Arguments string: {arguments_str}")
            continue

    return tool_calls


def _normalize_claude_tool_result_content(content: Any) -> str:
    """
    Normalize Claude SDK MCP tool result payloads into plain text.
    """
    if content is None:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, dict):
        # Claude MCP commonly returns {"type": "text", "text": "..."}
        if content.get("type") == "text" and "text" in content:
            return str(content["text"])
        if "text" in content:
            return str(content["text"])
        if "content" in content:
            return _normalize_claude_tool_result_content(content["content"])
        return json.dumps(content, default=str)

    if isinstance(content, list):
        parts = [
            _normalize_claude_tool_result_content(item)
            for item in content
        ]
        parts = [p for p in parts if p != ""]
        return "\n".join(parts)

    # SDK objects may expose `text` and/or `content` attributes.
    if hasattr(content, "text"):
        text = getattr(content, "text")
        if text is not None:
            return str(text)
    if hasattr(content, "content"):
        return _normalize_claude_tool_result_content(getattr(content, "content"))

    return str(content)


def generate_with_claude_sdk(
    messages: list[Message],
    tools: Optional[list[Tool]] = None,
    **kwargs: Any,
) -> AssistantMessage:
    """
    Generate a response using Claude SDK.

    Args:
        messages: The messages to send to the model.
        tools: The tools to use.
        **kwargs: Additional arguments (ignored for Claude SDK).

    Returns: An AssistantMessage.
    """
    if not CLAUDE_SDK_AVAILABLE:
        raise ImportError("Claude SDK is not available. Install claude-agent-sdk.")

    # Build structured prompt that preserves chat history
    # Extract system messages for system prompt
    system_messages = [msg for msg in messages if isinstance(msg, SystemMessage)]
    system_prompt = "\n\n".join([msg.content for msg in system_messages]) if system_messages else None

    # Build conversation history as a structured prompt
    conversation_parts = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            # Already handled in system_prompt
            continue
        elif isinstance(msg, UserMessage):
            conversation_parts.append(f"User: {msg.content}")
        elif isinstance(msg, AssistantMessage):
            # Preserve assistant messages with tool calls
            if msg.is_tool_call():
                assistant_text = "Assistant:"
                for tc in msg.tool_calls:
                    assistant_text += f"\n[Tool Call: {tc.name}]"
                    assistant_text += f"\nArguments: {json.dumps(tc.arguments)}"
                if msg.content:
                    assistant_text += f"\n{msg.content}"
                conversation_parts.append(assistant_text)
            elif msg.content:
                conversation_parts.append(f"Assistant: {msg.content}")
        elif isinstance(msg, ToolMessage):
            # Preserve tool results
            conversation_parts.append(f"Tool Result [{msg.id}]: {msg.content}")

    # Join into a single prompt that preserves the full conversation
    conversation_prompt = "\n\n".join(conversation_parts)

    # Convert tools to Claude SDK format if provided
    options = ClaudeAgentOptions(
        permission_mode='bypassPermissions',  # Allow tools without prompts
        cwd=os.getcwd(),  # Set working directory
        system_prompt=system_prompt,  # Include system prompt if present
    )

    if tools:
        claude_tools = convert_tau_to_claude_tools(tools)
        mcp_server = create_sdk_mcp_server(
            name="tau2_tools",
            version="1.0.0",
            tools=claude_tools
        )
        options.mcp_servers = {"tau2_tools": mcp_server}

    # Run the async query in a sync context
    async def _query():
        result_content = None
        result_tool_calls = []
        tool_results = {}  # Map tool_call_id -> result content

        # Use ClaudeSDKClient as async context manager
        async with ClaudeSDKClient(options=options) as client:
            # Send the query
            logger.debug(f"Sending conversation with history (length: {len(conversation_prompt)} chars)")
            await client.query(conversation_prompt)
            logger.debug("Query sent successfully")

            # Receive all messages until response is complete
            logger.debug("Receiving response messages...")
            async for message in client.receive_response():
                logger.debug(f"Received message type: {type(message).__name__}")

                # Check message type by class name
                message_type = type(message).__name__

                if message_type == 'AssistantMessage':
                    # AssistantMessage with content blocks
                    if hasattr(message, 'content') and isinstance(message.content, list):
                        for block in message.content:
                            block_type = type(block).__name__

                            if block_type == 'TextBlock':
                                # Text content
                                if hasattr(block, 'text'):
                                    # Accumulate text content
                                    if result_content:
                                        result_content += "\n" + block.text
                                    else:
                                        result_content = block.text
                                    logger.debug(f"Received text: {block.text[:100]}...")

                            elif block_type == 'ToolUseBlock':
                                # Tool call
                                tool_id = str(block.id)
                                # Remove mcp__tau2_tools__ prefix if present
                                tool_name = block.name
                                if tool_name.startswith('mcp__tau2_tools__'):
                                    tool_name = tool_name[len('mcp__tau2_tools__'):]
                                tool_input = block.input
                                # Some SDK types are pydantic-like objects
                                if hasattr(tool_input, "model_dump"):
                                    tool_input = tool_input.model_dump()
                                result_tool_calls.append(ToolCall(
                                    id=tool_id,
                                    name=tool_name,
                                    arguments=tool_input
                                ))
                                logger.debug(f"Received tool call: {tool_name}")

                elif message_type == 'ResultMessage':
                    # Final result message with metadata
                    if hasattr(message, 'result') and message.result:
                        # Use the result as final content if we don't have content yet
                        if not result_content:
                            result_content = str(message.result)
                    logger.debug("Received ResultMessage, response complete")

                elif message_type == 'SystemMessage':
                    # System messages (like init) - ignore
                    logger.debug("Received SystemMessage (ignoring)")

                elif message_type == 'UserMessage':
                    # CRITICAL: Tool result messages come back as UserMessage in MCP protocol
                    # Extract tool results to include in trajectory
                    if hasattr(message, 'content') and isinstance(message.content, list):
                        for block in message.content:
                            block_type = type(block).__name__
                            if block_type == 'ToolResultBlock':
                                tool_call_id = str(block.tool_use_id)
                                result_text = _normalize_claude_tool_result_content(
                                    getattr(block, "content", None)
                                )
                                tool_results[tool_call_id] = result_text
                                logger.debug(f"Captured tool result for {tool_call_id}: {result_text[:100]}...")
                    logger.debug("Received UserMessage (tool results captured)")

            logger.debug("All messages received")

        # If no structured tool calls found but we have text content, try parsing from text
        if not result_tool_calls and result_content and tools:
            parsed_tool_calls = _parse_tool_calls_from_text(result_content)
            if parsed_tool_calls:
                result_tool_calls = parsed_tool_calls
                logger.info(f"Parsed {len(parsed_tool_calls)} tool calls from text content")

        return result_content, result_tool_calls, tool_results

    # Run async function
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        content, tool_calls, tool_results = loop.run_until_complete(_query())
    except Exception as e:
        logger.error(f"Error running Claude SDK query: {e}")
        logger.error("Make sure the Claude CLI (claude-code) is installed and accessible.")
        logger.error("You can install it from: https://github.com/anthropics/claude-code")
        raise

    # Create AssistantMessage
    # Note: Only return tool_calls OR content, not both (as per agent requirements)
    if tool_calls:
        # Keep only tool calls to avoid mixed content+tool_call messages.
        message = AssistantMessage(
            role="assistant",
            content=None,
            tool_calls=tool_calls,
            cost=None,  # Claude SDK doesn't provide cost info directly
            usage=None,  # Claude SDK doesn't provide usage info directly
            raw_data={
                "source": "claude_sdk",
                "tools_already_executed": True,
                "tool_results": tool_results  # Pass actual tool results to orchestrator
            },
        )
    else:
        # Pure text response
        message = AssistantMessage(
            role="assistant",
            content=content,
            tool_calls=None,
            cost=None,
            usage=None,
            raw_data={"source": "claude_sdk"},
        )

    return message


def _get_or_create_mcp_server(tools: Optional[list[Tool]] = None, task_id: Optional[str] = None) -> Optional[str]:
    """Get or create a task-specific MCP server for the given tools.

    This function manages MCP server instances per task to avoid thread safety issues
    while still benefiting from caching within a task.

    Args:
        tools: List of tau2 Tool objects (None if no tools)
        task_id: Task ID for server isolation (required if tools are provided)

    Returns:
        MCP server URL if tools are provided, None otherwise
    """
    global _TASK_MCP_SERVERS

    if not tools or len(tools) == 0:
        return None

    # If no task_id provided, create ephemeral server (no caching)
    if not task_id:
        logger.warning("No task_id provided, creating ephemeral MCP server (no caching)")
        from tau2.integrations.nexusagent.mcp_server import Tau2MCPServer
        temp_server = Tau2MCPServer(tools)
        temp_server.start()
        if not temp_server.is_alive():
            raise RuntimeError("Ephemeral MCP server thread failed to start")
        return temp_server.get_url()

    # Calculate hash of current tools (based on tool names)
    current_hash = hash(tuple(sorted(tool.name for tool in tools)))

    with _MCP_SERVER_LOCK:
        # Check if we have a server for this task
        task_cache = _TASK_MCP_SERVERS.get(task_id)

        if task_cache:
            cached_server = task_cache["server"]
            cached_hash = task_cache["tools_hash"]

            # Reuse if tools match and server is alive
            if cached_hash == current_hash and cached_server.is_alive():
                logger.info(f"♻️  Reusing MCP server for task {task_id}")
                return cached_server.get_url()
            else:
                # Tools changed or server died, clean up old server
                logger.info(f"Stopping old MCP server for task {task_id} (tools changed or server died)")
                try:
                    cached_server.stop(timeout=5.0)
                except Exception as e:
                    logger.warning(f"Error stopping old MCP server: {e}")

        # Start new server for this task
        from tau2.integrations.nexusagent.mcp_server import Tau2MCPServer

        logger.info(f"🚀 Starting new MCP server for task {task_id}")
        logger.info(f"   Tools: {[tool.name for tool in tools]}")

        new_server = Tau2MCPServer(tools)
        new_server.start()

        # Verify server is responding
        if not new_server.is_alive():
            logger.error("MCP server thread failed to start")
            raise RuntimeError("MCP server thread failed to start")

        mcp_url = new_server.get_url()
        logger.info(f"✓ MCP server ready at {mcp_url} for task {task_id}")

        # Cache the server
        _TASK_MCP_SERVERS[task_id] = {
            "server": new_server,
            "tools_hash": current_hash
        }

        return mcp_url


def cleanup_task_mcp_server(task_id: str):
    """Clean up MCP server for a specific task.

    Args:
        task_id: Task ID whose MCP server should be cleaned up
    """
    global _TASK_MCP_SERVERS

    with _MCP_SERVER_LOCK:
        task_cache = _TASK_MCP_SERVERS.get(task_id)
        if task_cache:
            logger.info(f"Cleaning up MCP server for task {task_id}")
            try:
                task_cache["server"].stop(timeout=5.0)
            except Exception as e:
                logger.warning(f"Error stopping MCP server for task {task_id}: {e}")
            finally:
                del _TASK_MCP_SERVERS[task_id]


def cleanup_all_mcp_servers():
    """Clean up all MCP servers (called at process exit)."""
    global _TASK_MCP_SERVERS

    with _MCP_SERVER_LOCK:
        if _TASK_MCP_SERVERS:
            logger.info(f"Cleaning up {len(_TASK_MCP_SERVERS)} MCP servers")
            for task_id, task_cache in list(_TASK_MCP_SERVERS.items()):
                try:
                    task_cache["server"].stop(timeout=5.0)
                except Exception as e:
                    logger.warning(f"Error stopping MCP server for task {task_id}: {e}")
            _TASK_MCP_SERVERS.clear()


def cleanup_task_agent_id(task_id: str):
    """Clean up cached agent_id for a specific task.

    Args:
        task_id: Task ID whose agent_id should be removed from cache
    """
    global _TASK_AGENT_IDS

    with _AGENT_ID_LOCK:
        if task_id in _TASK_AGENT_IDS:
            agent_id = _TASK_AGENT_IDS[task_id]
            logger.info(f"Removing cached agent_id for task {task_id}: {agent_id}")
            del _TASK_AGENT_IDS[task_id]


def cleanup_all_agent_ids():
    """Clean up all cached agent_ids (called at process exit)."""
    global _TASK_AGENT_IDS

    with _AGENT_ID_LOCK:
        if _TASK_AGENT_IDS:
            logger.info(f"Cleaning up {len(_TASK_AGENT_IDS)} cached agent_ids")
            _TASK_AGENT_IDS.clear()


def cleanup_all_resources():
    """Clean up all resources (MCP servers and agent_ids)."""
    cleanup_all_mcp_servers()
    cleanup_all_agent_ids()


# Register cleanup at exit
atexit.register(cleanup_all_resources)


def _remove_bootstrap_md(agent_id: str, user_id: str):
    """Remove Bootstrap.md to prevent newly created agents from entering bootstrap mode."""
    from pathlib import Path
    bootstrap_path = Path.home() / ".nexusagent" / "workspaces" / f"{agent_id}_{user_id}" / "Bootstrap.md"
    try:
        if bootstrap_path.exists():
            bootstrap_path.unlink()
            logger.info(f"✓ Removed Bootstrap.md for {agent_id}")
    except Exception as e:
        logger.warning(f"Failed to remove Bootstrap.md: {e}")


def generate_with_nexusagent(
    messages: list[Message],
    tools: Optional[list[Tool]] = None,
    backend_url: Optional[str] = None,
    agent_id: Optional[str] = None,
    user_id: Optional[str] = None,
    task_id: Optional[str] = None,
    domain: Optional[str] = None,
    **kwargs: Any,
) -> AssistantMessage:
    """
    Generate a response using NexusAgent backend.

    Args:
        messages: The tau2 messages (conversation history).
        tools: The tau2 tools (not used - NexusAgent has its own tools).
        backend_url: URL of NexusAgent backend (default: from config).
        agent_id: NexusAgent agent ID (default: from config, or will create new agent if "auto").
        user_id: NexusAgent user ID (default: fixed "abc" for tau2 testing).
        task_id: Task ID for session isolation (used for agent creation).
        **kwargs: Additional arguments.

    Returns:
        AssistantMessage with the response from NexusAgent.
    """
    from tau2.integrations.nexusagent.nexusagent_backend import NexusAgentClient
    from tau2 import config

    # Read configuration
    if backend_url is None:
        backend_url = getattr(config, "NEXUSAGENT_BACKEND_URL", "ws://localhost:8000")

    # Use user_id from parameter, config, or environment variable
    if user_id is None:
        user_id = getattr(config, "NEXUSAGENT_DEFAULT_USER_ID", "abc")
        logger.info(f"Using user_id='{user_id}' for tau2 testing")

    # Ensure the user exists in NarraNexus database before creating agents
    # NarraNexus requires users to exist before agents can be created
    http_backend_url = backend_url.replace("ws://", "http://").replace("wss://", "https://")

    async def _ensure_user_exists():
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{http_backend_url}/api/auth/create-user",
                json={"user_id": user_id, "display_name": f"tau2 test user ({user_id})"}
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    logger.info(f"✓ Created user '{user_id}' in NarraNexus")
                elif "already exists" in result.get("error", ""):
                    logger.debug(f"User '{user_id}' already exists")
                else:
                    logger.warning(f"User creation response: {result}")

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(_ensure_user_exists())
    except Exception as e:
        logger.warning(f"Failed to ensure user exists (may still work if user already exists): {e}")

    # Create a new agent for each task if agent_id is "auto" or not provided
    # IMPORTANT: Cache agent_id per task_id to support multi-turn conversations
    if agent_id is None or agent_id in ["auto", "create_new"]:
        global _TASK_AGENT_IDS, _AGENT_ID_LOCK

        # If task_id is provided, check if we already have an agent for this task
        if task_id:
            with _AGENT_ID_LOCK:
                if task_id in _TASK_AGENT_IDS:
                    agent_id = _TASK_AGENT_IDS[task_id]
                    logger.info(f"♻️  Reusing cached agent for task {task_id}: {agent_id}")
                else:
                    # Create new agent for this task and cache it
                    agent_name = f"{domain or 'tau2'} task {task_id}"

                    logger.info(f"🚀 Creating new agent for task {task_id}...")

                    async def _create_agent():
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            response = await client.post(
                                f"{http_backend_url}/api/auth/agents",
                                json={
                                    "created_by": user_id,
                                    "agent_name": agent_name,
                                    "agent_description": "Agent created for tau2 benchmark testing"
                                }
                            )
                            if response.status_code == 200:
                                result = response.json()
                                if result.get("success"):
                                    return result["agent"]["agent_id"]
                                else:
                                    raise ValueError(f"Failed to create agent: {result.get('error', 'Unknown error')}")
                            else:
                                raise ValueError(f"HTTP error creating agent: {response.status_code}")

                    # Run async agent creation
                    try:
                        loop = asyncio.get_event_loop()
                    except RuntimeError:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)

                    try:
                        agent_id = loop.run_until_complete(_create_agent())
                        logger.info(f"✓ Created new agent: {agent_id}")
                        # Remove Bootstrap.md so the agent doesn't enter bootstrap mode
                        _remove_bootstrap_md(agent_id, user_id)
                        # Cache the agent_id for this task
                        _TASK_AGENT_IDS[task_id] = agent_id
                        logger.info(f"✓ Cached agent_id for task {task_id}")
                    except Exception as e:
                        logger.error(f"Failed to create agent: {e}")
                        logger.warning("Falling back to default agent from config")
                        agent_id = getattr(config, "NEXUSAGENT_DEFAULT_AGENT_ID", "default_agent")
        else:
            # No task_id provided, create a new agent without caching
            logger.warning("No task_id provided - creating ephemeral agent (not cached)")
            agent_name = f"{domain or 'tau2'} agent"

            async def _create_agent():
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{http_backend_url}/api/auth/agents",
                        json={
                            "created_by": user_id,
                            "agent_name": agent_name,
                            "agent_description": "Agent created for tau2 benchmark testing"
                        }
                    )
                    if response.status_code == 200:
                        result = response.json()
                        if result.get("success"):
                            return result["agent"]["agent_id"]
                        else:
                            raise ValueError(f"Failed to create agent: {result.get('error', 'Unknown error')}")
                    else:
                        raise ValueError(f"HTTP error creating agent: {response.status_code}")

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            try:
                agent_id = loop.run_until_complete(_create_agent())
                logger.info(f"✓ Created ephemeral agent: {agent_id}")
                _remove_bootstrap_md(agent_id, user_id)
            except Exception as e:
                logger.error(f"Failed to create agent: {e}")
                logger.warning("Falling back to default agent from config")
                agent_id = getattr(config, "NEXUSAGENT_DEFAULT_AGENT_ID", "default_agent")

    logger.info(f"Connecting to NexusAgent at {backend_url} (agent={agent_id}, user={user_id})")

    # Get or create task-specific MCP server for tools
    # This provides caching within a task while avoiding thread safety issues
    mcp_url = _get_or_create_mcp_server(tools, task_id=task_id)

    # Create client
    client = NexusAgentClient(
        backend_url=backend_url,
        agent_id=agent_id,
        user_id=user_id,
        external_mcp_url=mcp_url,  # Pass external MCP URL
    )

    # Run async call in event loop
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        # Don't pass tools to generate() since we're using external MCP server
        result = loop.run_until_complete(
            client.generate(messages=messages, tools=None, **kwargs)
        )
        return result
    except Exception as e:
        logger.error(f"Error calling NexusAgent backend: {e}")
        logger.error("Make sure NexusAgent backend is running at the configured URL")
        raise


def generate(
    model: str,
    messages: list[Message],
    tools: Optional[list[Tool]] = None,
    tool_choice: Optional[str] = None,
    **kwargs: Any,
) -> UserMessage | AssistantMessage:
    """
    Generate a response from the model.

    Args:
        model: The model to use.
        messages: The messages to send to the model.
        tools: The tools to use.
        tool_choice: The tool choice to use.
        **kwargs: Additional arguments to pass to the model.

    Returns: A tuple containing the message and the cost.
    """
    # DEBUG: Log the model parameter
    logger.info(f"[DEBUG] generate() called with model='{model}', model.lower()='{model.lower()}'")

    # Check if using NexusAgent backend
    if model.lower() in ["nexusagent", "nexus", "nexusagent-backend"]:
        logger.info("Using NexusAgent backend for generation")
        # Extract task_id and domain from kwargs if available (for session management)
        task_id = kwargs.pop('task_id', None)
        domain = kwargs.pop('domain', None)
        return generate_with_nexusagent(messages=messages, tools=tools, task_id=task_id, domain=domain, **kwargs)

    logger.info(f"[DEBUG] Model '{model}' not matched for NexusAgent, continuing to other routes")

    # Check if using Claude SDK
    if model == "claude" or model == "claude-code":
        logger.info("Using Claude SDK for generation")
        return generate_with_claude_sdk(messages=messages, tools=tools, **kwargs)

    if kwargs.get("num_retries") is None:
        kwargs["num_retries"] = DEFAULT_MAX_RETRIES

    if model.startswith("claude") and not ALLOW_SONNET_THINKING:
        kwargs["thinking"] = {"type": "disabled"}
    litellm_messages = to_litellm_messages(messages)
    tools = [tool.openai_schema for tool in tools] if tools else None
    if tools and tool_choice is None:
        tool_choice = "auto"
    try:
        response = completion(
            model=model,
            messages=litellm_messages,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs,
        )
    except Exception as e:
        logger.error(e)
        raise e
    cost = get_response_cost(response)
    usage = get_response_usage(response)
    response = response.choices[0]
    try:
        finish_reason = response.finish_reason
        if finish_reason == "length":
            logger.warning("Output might be incomplete due to token limit!")
    except Exception as e:
        logger.error(e)
        raise e
    assert response.message.role == "assistant", (
        "The response should be an assistant message"
    )
    content = response.message.content
    tool_calls = response.message.tool_calls or []
    tool_calls = [
        ToolCall(
            id=tool_call.id,
            name=tool_call.function.name,
            arguments=json.loads(tool_call.function.arguments),
        )
        for tool_call in tool_calls
    ]
    tool_calls = tool_calls or None

    message = AssistantMessage(
        role="assistant",
        content=content,
        tool_calls=tool_calls,
        cost=cost,
        usage=usage,
        raw_data=response.to_dict(),
    )
    return message


def get_cost(messages: list[Message]) -> tuple[float, float] | None:
    """
    Get the cost of the interaction between the agent and the user.
    Returns None if any message has no cost.
    """
    agent_cost = 0
    user_cost = 0
    for message in messages:
        if isinstance(message, ToolMessage):
            continue
        if message.cost is not None:
            if isinstance(message, AssistantMessage):
                agent_cost += message.cost
            elif isinstance(message, UserMessage):
                user_cost += message.cost
        else:
            logger.warning(f"Message {message.role}: {message.content} has no cost")
            return None
    return agent_cost, user_cost


def get_token_usage(messages: list[Message]) -> dict:
    """
    Get the token usage of the interaction between the agent and the user.
    """
    usage = {"completion_tokens": 0, "prompt_tokens": 0}
    for message in messages:
        if isinstance(message, ToolMessage):
            continue
        if message.usage is None:
            logger.warning(f"Message {message.role}: {message.content} has no usage")
            continue
        usage["completion_tokens"] += message.usage["completion_tokens"]
        usage["prompt_tokens"] += message.usage["prompt_tokens"]
    return usage
