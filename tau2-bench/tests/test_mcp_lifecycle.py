#!/usr/bin/env python3
"""Test MCP server lifecycle management

Tests for Bug #3 fix - verify MCP server starts, stops, and doesn't leak resources.
"""

import time
import pytest
from tau2.integrations.nexusagent.mcp_server import Tau2MCPServer
from tau2.environment.tool import as_tool


def test_tool(message: str) -> str:
    """Simple test tool"""
    return f"Echo: {message}"


def test_single_lifecycle():
    """Test single start/stop cycle"""
    print("\n" + "="*60)
    print("Test 1: Single Lifecycle")
    print("="*60)

    tool = as_tool(test_tool)
    server = Tau2MCPServer([tool])

    print(f"  Port: {server.port}")
    print(f"  Starting server...")
    server.start()

    assert server.is_alive(), "Server should be alive after start"
    print(f"  ✓ Server alive: {server.is_alive()}")

    time.sleep(1)

    print(f"  Stopping server...")
    server.stop()

    time.sleep(1)
    assert not server.is_alive(), "Server should be dead after stop"
    print(f"  ✓ Server alive: {server.is_alive()}")

    print("  ✓ Test passed\n")


def test_port_reuse():
    """Test that port can be reused after server stops"""
    print("="*60)
    print("Test 2: Port Reuse")
    print("="*60)

    tool = as_tool(test_tool)

    # First server
    server1 = Tau2MCPServer([tool], port=8765)
    server1.start()
    print(f"  Server 1 started on port {server1.port}")
    assert server1.is_alive()

    server1.stop()
    time.sleep(2)  # Wait for cleanup
    assert not server1.is_alive()
    print(f"  Server 1 stopped")

    # Second server on same port
    server2 = Tau2MCPServer([tool], port=8765)
    server2.start()
    print(f"  Server 2 started on port {server2.port}")
    assert server2.is_alive()

    server2.stop()
    print(f"  Server 2 stopped")

    print("  ✓ Port successfully reused\n")


def test_context_manager():
    """Test context manager ensures cleanup"""
    print("="*60)
    print("Test 3: Context Manager")
    print("="*60)

    tool = as_tool(test_tool)

    with Tau2MCPServer([tool]) as server:
        print(f"  Inside context: server alive = {server.is_alive()}")
        assert server.is_alive()

    # Server should be stopped after exiting context
    time.sleep(1)
    print(f"  Outside context: server alive = {server.is_alive()}")
    assert not server.is_alive()

    print("  ✓ Context manager cleanup works\n")


def test_cleanup_on_error():
    """Test server stops even if error occurs during usage"""
    print("="*60)
    print("Test 4: Cleanup on Error")
    print("="*60)

    tool = as_tool(test_tool)
    server = Tau2MCPServer([tool])

    try:
        server.start()
        assert server.is_alive()
        print(f"  Server started")

        # Simulate error
        raise ValueError("Simulated error")
    except ValueError:
        print(f"  Error occurred (expected)")
    finally:
        server.stop()
        time.sleep(1)

    assert not server.is_alive()
    print(f"  Server stopped despite error")
    print("  ✓ Cleanup on error works\n")


def test_double_start():
    """Test that calling start() twice doesn't create duplicate servers"""
    print("="*60)
    print("Test 5: Double Start")
    print("="*60)

    tool = as_tool(test_tool)
    server = Tau2MCPServer([tool])

    server.start()
    print(f"  First start - port {server.port}")
    first_thread = server.server_thread

    # Try starting again
    server.start()
    print(f"  Second start attempt")

    # Should be the same thread
    assert server.server_thread is first_thread, "Should reuse existing thread"
    print(f"  ✓ Same thread reused")

    server.stop()
    print("  ✓ Double start handled correctly\n")


def test_stop_without_start():
    """Test that stop() without start() doesn't crash"""
    print("="*60)
    print("Test 6: Stop Without Start")
    print("="*60)

    tool = as_tool(test_tool)
    server = Tau2MCPServer([tool])

    # Should not crash
    server.stop()
    print("  ✓ Stop without start handled gracefully\n")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("  MCP Server Lifecycle Tests (Bug #3 Fix)")
    print("="*60 + "\n")

    test_single_lifecycle()
    test_port_reuse()
    test_context_manager()
    test_cleanup_on_error()
    test_double_start()
    test_stop_without_start()

    print("="*60)
    print("  All Tests Passed!")
    print("="*60 + "\n")
