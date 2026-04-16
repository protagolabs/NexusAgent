"""Temporary MCP server for tau2 tools

This module creates a temporary MCP (Model Context Protocol) server that exposes
tau2's environment tools to NexusAgent using the FastMCP library.

Architecture:
- tau2 tools are wrapped as MCP-compatible tools using FastMCP
- FastMCP handles the MCP protocol implementation
- NexusAgent connects to this server and uses the tools
- Server automatically shuts down after the agent session completes
"""

import asyncio
import threading
from typing import Any, Dict, List, Optional
from threading import Thread
from loguru import logger

# FastMCP is the standard MCP server library used by NexusAgent
from mcp.server.fastmcp import FastMCP

from tau2.environment.tool import Tool


# Module-level registry for active servers (Bug #3 fix - server reuse pattern)
_ACTIVE_SERVERS: Dict[int, 'Tau2MCPServer'] = {}
_SERVERS_LOCK = threading.Lock()


class Tau2MCPServer:
    """Temporary MCP server for tau2 tools

    This server exposes tau2 environment tools through the MCP protocol using FastMCP,
    allowing NexusAgent to discover and call them.

    Usage:
        >>> tools = [tool1, tool2, tool3]
        >>> server = Tau2MCPServer(tools, port=8765)
        >>> server.start()
        >>> # ... use the server URL with NexusAgent ...
        >>> server.stop()
    """

    def __init__(self, tools: List[Tool], port: Optional[int] = None):
        """Initialize MCP server

        Args:
            tools: List of tau2 Tool objects to expose
            port: Port to run on (if None, uses a random available port)
        """
        self.tools = {tool.name: tool for tool in tools}
        self.port = port or self._find_free_port()
        self.mcp = self._create_mcp_server()
        self.server_thread = None

        # NEW: Bug #3 fix - Add shutdown event for graceful termination
        self._shutdown_event = threading.Event()
        self._start_time = None

    def _find_free_port(self) -> int:
        """Find an available port"""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port

    def _create_mcp_server(self) -> FastMCP:
        """Create FastMCP server with tau2 tools

        This method dynamically creates tool functions and registers them with FastMCP.
        """
        import inspect
        from typing import get_origin, get_args

        # Create FastMCP server
        mcp = FastMCP("tau2_tools")
        mcp.settings.port = self.port

        # Tools that should NOT be registered as MCP tools.
        # transfer_to_human_agents is a tau2 orchestrator control tool, not an
        # actual MCP capability — NexusAgent does not need to call it.
        _SKIP_TOOLS = {"transfer_to_human_agents"}

        # Register each tau2 tool as an MCP tool
        for tool_name, tool in self.tools.items():
            if tool_name in _SKIP_TOOLS:
                logger.info(f"[Tau2 MCP Server] Skipping tool: {tool_name}")
                continue
            # Create a closure to capture the tool
            def make_tool_function(captured_tool: Tool):
                # Get the tool's description
                description = captured_tool.short_desc or captured_tool.name
                if captured_tool.long_desc:
                    description += f"\n\n{captured_tool.long_desc}"

                # Create the async function for this tool
                async def tool_function(**kwargs) -> str:
                    """Dynamically generated tool function"""
                    logger.info(f"[Tau2 MCP Server] Executing tool: {captured_tool.name} with raw args: {kwargs}")

                    try:
                        # Handle nested kwargs - FastMCP sometimes wraps parameters in a 'kwargs' key
                        if len(kwargs) == 1 and 'kwargs' in kwargs:
                            actual_kwargs = kwargs['kwargs']
                            if isinstance(actual_kwargs, dict):
                                kwargs = actual_kwargs
                                logger.debug(f"[Tau2 MCP Server] Unwrapped nested kwargs: {kwargs}")

                        # Fix for retail domain: Normalize order_id to include '#' prefix
                        # The LLM sometimes strips the '#' prefix, treating it as markdown
                        if 'order_id' in kwargs:
                            order_id = kwargs['order_id']
                            if isinstance(order_id, str) and order_id and not order_id.startswith('#'):
                                # Check if this looks like a retail order ID (e.g., W1234567)
                                if order_id[0].isalpha() and order_id[1:].isdigit():
                                    kwargs['order_id'] = f'#{order_id}'
                                    logger.info(f"[Tau2 MCP Server] Normalized order_id: {order_id} -> {kwargs['order_id']}")

                        logger.info(f"[Tau2 MCP Server] Processed args for {captured_tool.name}: {kwargs}")

                        # Validate and create parameters object
                        params_obj = captured_tool.params(**kwargs)
                        # Execute the tool
                        result = captured_tool._call(**params_obj.model_dump())

                        logger.info(f"[Tau2 MCP Server] Tool result: {str(result)[:200]}")
                        return str(result)
                    except Exception as e:
                        error_msg = f"Error executing tool '{captured_tool.name}': {str(e)}"
                        logger.error(f"[Tau2 MCP Server] {error_msg}")
                        logger.error(f"[Tau2 MCP Server] Original kwargs: {kwargs}")
                        raise Exception(error_msg)

                # Set function name and docstring
                tool_function.__name__ = captured_tool.name
                tool_function.__doc__ = description

                # Build complete function signature with proper parameters
                # FastMCP uses inspect.signature() to generate JSON schema
                params = []
                annotations = {'return': str}

                for field_name, field_info in captured_tool.params.model_fields.items():
                    # Get the annotation
                    annotation = field_info.annotation

                    # Determine if field is required
                    # In pydantic v2, field is required if it has no default value
                    is_required = field_info.is_required()

                    if is_required:
                        # Required parameter - use Parameter.empty as default
                        param = inspect.Parameter(
                            field_name,
                            inspect.Parameter.KEYWORD_ONLY,
                            annotation=annotation
                        )
                    else:
                        # Optional parameter - use the field's default
                        default_val = field_info.default if field_info.default is not None else None
                        param = inspect.Parameter(
                            field_name,
                            inspect.Parameter.KEYWORD_ONLY,
                            default=default_val,
                            annotation=annotation
                        )

                    params.append(param)
                    annotations[field_name] = annotation

                # Create new signature with the parameters
                tool_function.__signature__ = inspect.Signature(params)
                tool_function.__annotations__ = annotations

                return tool_function

            # Create and register the tool function
            tool_func = make_tool_function(tool)

            # Register with FastMCP
            # FastMCP will use the function's signature and annotations to generate the schema
            mcp.tool(
                name=tool.name,
                description=tool.short_desc or tool.name
            )(tool_func)

        logger.info(f"[Tau2 MCP Server] Registered {len(self.tools)} tools: {list(self.tools.keys())}")

        return mcp

    def start(self):
        """Start the MCP server in a background thread (Bug #3 fix)"""
        # NEW: Check if port is already in use by another server (reuse pattern)
        with _SERVERS_LOCK:
            if self.port in _ACTIVE_SERVERS:
                existing = _ACTIVE_SERVERS[self.port]
                if existing.is_alive():
                    logger.warning(
                        f"[Tau2 MCP Server] Port {self.port} already has an active server. "
                        f"Reusing existing server."
                    )
                    self.server_thread = existing.server_thread
                    self._start_time = existing._start_time
                    return
                else:
                    logger.info(f"[Tau2 MCP Server] Cleaning up dead server on port {self.port}")
                    del _ACTIVE_SERVERS[self.port]

        if self.server_thread is not None and self.server_thread.is_alive():
            logger.warning("[Tau2 MCP Server] Server is already running")
            return

        # NEW: Clear shutdown event for new start
        self._shutdown_event.clear()

        logger.info(f"[Tau2 MCP Server] Starting FastMCP server on port {self.port}")
        logger.info(f"[Tau2 MCP Server] Exposing {len(self.tools)} tools: {list(self.tools.keys())}")

        # Run server in a separate thread
        def run_server():
            try:
                import time
                self._start_time = time.time()
                # FastMCP.run() is a blocking call that runs the server
                # Use 'sse' transport for Server-Sent Events support
                self.mcp.run(transport='sse')
            except Exception as e:
                # NEW: Only log as error if not shutdown signal
                if not self._shutdown_event.is_set():
                    logger.error(f"[Tau2 MCP Server] Server error: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                else:
                    logger.info("[Tau2 MCP Server] Stopped by shutdown signal")

        self.server_thread = Thread(
            target=run_server,
            daemon=True,  # Daemon thread so process can exit cleanly
            name="Tau2MCPServer"
        )
        self.server_thread.start()

        # NEW: Quick health check with reduced timeout (2s instead of 5s)
        if not self._wait_for_server_ready(timeout=2.0):
            logger.warning("[Tau2 MCP Server] Quick health check timed out - server may still be starting")
        else:
            logger.debug(f"[Tau2 MCP Server] Server started at http://127.0.0.1:{self.port}/sse")

        # NEW: Register this server
        with _SERVERS_LOCK:
            _ACTIVE_SERVERS[self.port] = self

    def stop(self, timeout: float = 5.0):
        """Stop the MCP server gracefully (Bug #3 fix)

        Args:
            timeout: Maximum seconds to wait for graceful shutdown
        """
        if self.server_thread is None:
            logger.debug("[Tau2 MCP Server] No server thread to stop")
            return

        if not self.server_thread.is_alive():
            logger.info("[Tau2 MCP Server] Server thread already stopped")
            self.server_thread = None
            return

        logger.info(f"[Tau2 MCP Server] Stopping server on port {self.port}")

        # Strategy 1: Set shutdown event
        self._shutdown_event.set()

        # Strategy 2: Wait for graceful shutdown
        import time
        self.server_thread.join(timeout=timeout)

        if self.server_thread.is_alive():
            logger.warning(
                f"[Tau2 MCP Server] Server did not stop gracefully after {timeout}s. "
                f"Thread will be abandoned (daemon threads will be terminated by Python on exit)."
            )
        else:
            logger.info("[Tau2 MCP Server] Server stopped successfully")

        self.server_thread = None

        # Strategy 3: Verify port is released
        if not self._is_port_free(self.port):
            logger.warning(
                f"[Tau2 MCP Server] Port {self.port} is still in use after shutdown. "
                f"This may cause issues on next start."
            )

        # NEW: Unregister from active servers
        with _SERVERS_LOCK:
            if self.port in _ACTIVE_SERVERS and _ACTIVE_SERVERS[self.port] is self:
                del _ACTIVE_SERVERS[self.port]

    def _wait_for_server_ready(self, timeout: float = 5.0, interval: float = 0.1) -> bool:
        """Wait for server to be ready and accepting connections (Bug #3 fix)

        Args:
            timeout: Maximum seconds to wait
            interval: Seconds between checks

        Returns:
            True if server is ready, False if timeout
        """
        import time
        start = time.time()

        while time.time() - start < timeout:
            if self._is_server_responding():
                return True
            time.sleep(interval)

        return False

    def _is_server_responding(self) -> bool:
        """Check if server is responding on its port (Bug #3 fix)

        Returns:
            True if server accepts connections
        """
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)

        try:
            result = sock.connect_ex(('127.0.0.1', self.port))
            return result == 0
        except Exception:
            return False
        finally:
            sock.close()

    def _is_port_free(self, port: int) -> bool:
        """Check if a port is free (Bug #3 fix)

        Args:
            port: Port number to check

        Returns:
            True if port is free
        """
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False
        finally:
            sock.close()

    def is_alive(self) -> bool:
        """Check if server thread is running (Bug #3 fix)

        Returns:
            True if server is alive
        """
        return self.server_thread is not None and self.server_thread.is_alive()

    def get_url(self) -> str:
        """Get the MCP server URL for NexusAgent"""
        return f"http://127.0.0.1:{self.port}/sse"

    def __enter__(self):
        """Context manager entry"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.stop()
