#!/usr/bin/env python3
"""
Diagnostic script for NexusAgent integration with tau2-bench
诊断 NexusAgent 与 tau2-bench 集成的问题
"""

import asyncio
import sys
import json
from pathlib import Path

# Add tau2-bench src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tau2.data_model.message import UserMessage, SystemMessage
from tau2.integrations.nexusagent.nexusagent_backend import NexusAgentClient
from tau2.domains.airline.tools import AirlineTools
from tau2.domains.airline.data_model import FlightDB
from tau2.domains.airline.utils import AIRLINE_DB_PATH
from loguru import logger

async def test_basic_connection():
    """Test 1: Basic WebSocket connection"""
    print("\n" + "="*60)
    print("🔌 测试 1: WebSocket 连接")
    print("="*60)

    client = NexusAgentClient(
        backend_url="ws://localhost:8000",
        agent_id="default_agent",
        user_id="test_user_123",
    )

    messages = [
        SystemMessage(role="system", content="You are a helpful assistant."),
        UserMessage(role="user", content="Hello, can you hear me?")
    ]

    try:
        print("📡 发送简单消息...")
        response = await client.generate(messages=messages, tools=None)
        print(f"✅ 连接成功！")
        print(f"📝 响应内容: {response.content[:200] if response.content else '(无内容)'}")
        print(f"🔧 工具调用: {len(response.tool_calls) if response.tool_calls else 0}")
        return True
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_with_airline_policy():
    """Test 2: With airline policy (tau2-style system message)"""
    print("\n" + "="*60)
    print("✈️  测试 2: 带 Airline Policy 的消息")
    print("="*60)

    # Load airline policy
    policy_path = Path(__file__).parent.parent / "data/tau2/domains/airline/policy.md"
    with open(policy_path, 'r') as f:
        policy = f.read()

    client = NexusAgentClient(
        backend_url="ws://localhost:8000",
        agent_id="default_agent",
        user_id="test_user_456",
    )

    messages = [
        SystemMessage(role="system", content=policy),
        UserMessage(role="user", content="Hi, I want to book a flight from New York to Seattle.")
    ]

    try:
        print(f"📡 发送消息（policy 长度: {len(policy)} 字符）...")
        response = await client.generate(messages=messages, tools=None)
        print(f"✅ 成功！")
        print(f"📝 响应内容: {response.content[:200] if response.content else '(无内容)'}")
        return True
    except Exception as e:
        print(f"❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_with_tools():
    """Test 3: With airline tools (MCP server)"""
    print("\n" + "="*60)
    print("🔧 测试 3: 带工具调用（MCP Server）")
    print("="*60)

    # Load airline tools
    db = FlightDB.load(AIRLINE_DB_PATH)
    toolkit = AirlineTools(db)
    tools_dict = toolkit.get_tools()

    # Get a few key tools
    tools = [
        tools_dict['get_user_details'],
        tools_dict['search_flights'],
    ]

    client = NexusAgentClient(
        backend_url="ws://localhost:8000",
        agent_id="default_agent",
        user_id="test_user_789",
    )

    messages = [
        SystemMessage(role="system", content="You are an airline customer service agent. When users ask about flights, use the tools to help them."),
        UserMessage(role="user", content="My user ID is mia_li_3668. Can you check my profile?")
    ]

    try:
        print(f"📡 发送消息（{len(tools)} 个工具）...")
        response = await client.generate(messages=messages, tools=tools)
        print(f"✅ 成功！")
        print(f"📝 响应内容: {response.content[:200] if response.content else '(无内容)'}")
        print(f"🔧 工具调用: {len(response.tool_calls) if response.tool_calls else 0}")
        if response.tool_calls:
            for tc in response.tool_calls:
                print(f"   - {tc.name}: {tc.arguments}")

        # Check raw_data
        raw_data = getattr(response, 'raw_data', {})
        print(f"🔍 tools_already_executed: {raw_data.get('tools_already_executed', False)}")
        print(f"🔍 tool_results count: {len(raw_data.get('tool_results', {}))}")

        return True
    except Exception as e:
        print(f"❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_full_conversation():
    """Test 4: Full multi-turn conversation"""
    print("\n" + "="*60)
    print("💬 测试 4: 多轮对话")
    print("="*60)

    # Load airline tools
    db = FlightDB.load(AIRLINE_DB_PATH)
    toolkit = AirlineTools(db)
    tools_dict = toolkit.get_tools()
    tools = list(tools_dict.values())[:5]  # Get first 5 tools

    client = NexusAgentClient(
        backend_url="ws://localhost:8000",
        agent_id="default_agent",
        user_id="test_user_multi",
    )

    # Simulate a conversation
    messages = [
        SystemMessage(role="system", content="You are an airline agent."),
        UserMessage(role="user", content="Hi!"),
    ]

    try:
        print(f"📡 第一轮...")
        response1 = await client.generate(messages=messages, tools=tools)
        print(f"✅ 第一轮成功: {response1.content[:100] if response1.content else '(无内容)'}")

        # Add response and continue
        messages.append(response1)
        messages.append(UserMessage(role="user", content="My user ID is mia_li_3668"))

        print(f"📡 第二轮...")
        response2 = await client.generate(messages=messages, tools=tools)
        print(f"✅ 第二轮成功: {response2.content[:100] if response2.content else '(无内容)'}")

        return True
    except Exception as e:
        print(f"❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    print("\n" + "🔍 NexusAgent 诊断工具" + "\n")
    print("检查 NexusAgent 与 tau2-bench 的集成状态\n")

    results = {}

    # Run all tests
    results['basic_connection'] = await test_basic_connection()
    results['with_policy'] = await test_with_airline_policy()
    results['with_tools'] = await test_with_tools()
    results['multi_turn'] = await test_full_conversation()

    # Summary
    print("\n" + "="*60)
    print("📊 诊断总结")
    print("="*60)

    for test_name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{status} - {test_name}")

    total_passed = sum(results.values())
    total_tests = len(results)
    print(f"\n总计: {total_passed}/{total_tests} 测试通过")

    if total_passed < total_tests:
        print("\n⚠️  存在失败的测试，请检查 NexusAgent 配置和日志")
        return 1
    else:
        print("\n🎉 所有测试通过！NexusAgent 集成正常")
        return 0

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
