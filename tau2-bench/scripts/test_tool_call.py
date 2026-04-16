#!/usr/bin/env python3
"""
测试 MCP 工具调用

模拟 Claude Agent SDK 调用工具的方式
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tau2.domains.airline.environment import get_environment
from tau2.integrations.nexusagent.mcp_server import Tau2MCPServer
from loguru import logger


async def test_tool_directly():
    """直接测试工具执行"""
    logger.info("=" * 80)
    logger.info("  测试 1: 直接调用工具")
    logger.info("=" * 80)

    # 获取工具
    env = get_environment()
    tools_dict = env.tools.get_tools()
    tools = list(tools_dict.values())

    # 测试 get_user_details 工具
    tool = [t for t in tools if t.name == 'get_user_details'][0]

    logger.info(f"测试工具: {tool.name}")

    # Test 1: 正常参数
    logger.info("Test 1: 正常参数格式")
    try:
        params_obj = tool.params(user_id='raj_sanchez_7340')
        result = tool._call(**params_obj.model_dump())
        logger.success(f"✓ 成功: {str(result)[:100]}")
    except Exception as e:
        logger.error(f"✗ 失败: {e}")

    # Test 2: 嵌套 kwargs 格式 (Claude SDK 的格式)
    logger.info("Test 2: 嵌套 kwargs 格式")
    try:
        # 模拟 kwargs = {'kwargs': {'user_id': 'raj_sanchez_7340'}}
        kwargs = {'kwargs': {'user_id': 'raj_sanchez_7340'}}

        # 应用 unwrap 逻辑
        if len(kwargs) == 1 and 'kwargs' in kwargs:
            actual_kwargs = kwargs['kwargs']
            if isinstance(actual_kwargs, dict):
                kwargs = actual_kwargs

        params_obj = tool.params(**kwargs)
        result = tool._call(**params_obj.model_dump())
        logger.success(f"✓ 成功 (unwrapped): {str(result)[:100]}")
    except Exception as e:
        logger.error(f"✗ 失败: {e}")


async def test_mcp_server_tool():
    """测试通过 MCP server 的工具调用"""
    logger.info("")
    logger.info("=" * 80)
    logger.info("  测试 2: 通过 MCP Server 调用工具")
    logger.info("=" * 80)

    # 获取工具
    env = get_environment()
    tools_dict = env.tools.get_tools()
    tools = list(tools_dict.values())

    # 创建 MCP server (不启动)
    server = Tau2MCPServer(tools, port=9999)

    # 获取注册的工具函数
    tool_func = server.mcp._tool_manager._tools['get_user_details']

    logger.info("测试 MCP server 工具函数")

    # Test 1: 正常调用
    logger.info("Test 1: 正常参数")
    try:
        result = await tool_func.fn(user_id='raj_sanchez_7340')
        logger.success(f"✓ 成功: {result[:100]}")
    except Exception as e:
        logger.error(f"✗ 失败: {e}")

    # Test 2: 嵌套 kwargs
    logger.info("Test 2: 嵌套 kwargs (Claude SDK 格式)")
    try:
        result = await tool_func.fn(kwargs={'user_id': 'raj_sanchez_7340'})
        logger.success(f"✓ 成功 (with unwrap): {result[:100]}")
    except Exception as e:
        logger.error(f"✗ 失败: {e}")


async def main():
    """主函数"""
    await test_tool_directly()
    await test_mcp_server_tool()

    logger.info("")
    logger.info("=" * 80)
    logger.info("✓ 测试完成")
    logger.info("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
