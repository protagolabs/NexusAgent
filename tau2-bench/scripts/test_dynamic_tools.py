#!/usr/bin/env python3
"""Test script for dynamic tool injection

This script tests whether tau2 tools can be dynamically injected into NexusAgent
through the temporary MCP server mechanism.

Usage:
    python scripts/test_dynamic_tools.py
"""

import asyncio
from tau2.environment.tool import Tool
from tau2.data_model.message import UserMessage
from tau2.integrations.nexusagent.nexusagent_backend import NexusAgentClient


def create_test_tool():
    """Create a simple test tool for demonstration"""
    def get_weather(city: str) -> str:
        """Get the weather for a city

        Args:
            city: The city name

        Returns:
            Weather information
        """
        # Mock implementation
        weather_data = {
            "San Francisco": "Sunny, 72°F",
            "New York": "Cloudy, 65°F",
            "London": "Rainy, 58°F",
        }
        return weather_data.get(city, f"Weather data not available for {city}")

    return Tool(get_weather)


def create_calculator_tool():
    """Create a calculator tool"""
    def calculate(expression: str) -> str:
        """Calculate a mathematical expression

        Args:
            expression: Mathematical expression to evaluate (e.g., "2 + 2")

        Returns:
            Result of the calculation
        """
        try:
            result = eval(expression)
            return f"{expression} = {result}"
        except Exception as e:
            return f"Error: {str(e)}"

    return Tool(calculate)


async def test_dynamic_tools():
    """Test dynamic tool injection"""
    print("=" * 80)
    print("Testing Dynamic Tool Injection for NexusAgent")
    print("=" * 80)
    print()

    # Create test tools
    tools = [
        create_test_tool(),
        create_calculator_tool(),
    ]

    print(f"Created {len(tools)} test tools:")
    for tool in tools:
        print(f"  - {tool.name}: {tool.short_desc}")
    print()

    # Create NexusAgent client
    client = NexusAgentClient(
        backend_url="ws://localhost:8000",
        agent_id="default_agent",
        user_id="test_user",
    )

    # Test 1: Ask agent to use the weather tool
    print("-" * 80)
    print("Test 1: Weather Tool")
    print("-" * 80)
    print()

    messages = [
        UserMessage(
            role="user",
            content="What's the weather like in San Francisco? Please use the get_weather tool."
        )
    ]

    try:
        print("Sending request to NexusAgent...")
        response = await client.generate(messages=messages, tools=tools)
        print(f"\nAgent Response:")
        print(f"  Content: {response.content}")
        if response.tool_calls:
            print(f"  Tool Calls: {len(response.tool_calls)}")
            for tc in response.tool_calls:
                print(f"    - {tc.name}: {tc.arguments}")
        print()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

    # Test 2: Ask agent to use the calculator tool
    print("-" * 80)
    print("Test 2: Calculator Tool")
    print("-" * 80)
    print()

    messages = [
        UserMessage(
            role="user",
            content="What is 15 * 23? Please use the calculate tool."
        )
    ]

    try:
        print("Sending request to NexusAgent...")
        response = await client.generate(messages=messages, tools=tools)
        print(f"\nAgent Response:")
        print(f"  Content: {response.content}")
        if response.tool_calls:
            print(f"  Tool Calls: {len(response.tool_calls)}")
            for tc in response.tool_calls:
                print(f"    - {tc.name}: {tc.arguments}")
        print()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

    print("=" * 80)
    print("Test Complete")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_dynamic_tools())
