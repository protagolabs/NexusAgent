#!/usr/bin/env python3
"""
测试 airline MCP server 是否正常工作

这个脚本会：
1. 启动临时的 airline MCP server
2. 列出所有可用的工具
3. 测试工具调用（如果可能）

使用方法：
    python scripts/test_airline_mcp_server.py
"""

import sys
from pathlib import Path

# 添加 tau2-bench/src 到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from loguru import logger
from tau2.domains.airline.environment import get_environment
from tau2.integrations.nexusagent.mcp_server import Tau2MCPServer


def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("  Airline MCP Server 测试")
    logger.info("=" * 60)
    logger.info("")

    # 1. 加载 airline 环境
    logger.info("步骤 1: 加载 airline 域环境")
    try:
        env = get_environment()
        logger.success(f"✓ Airline 环境已加载")
        logger.info(f"  域名: {env.domain_name}")
    except Exception as e:
        logger.error(f"✗ 加载 airline 环境失败: {e}")
        return False

    # 2. 获取所有工具
    logger.info("")
    logger.info("步骤 2: 获取 airline 工具")
    try:
        tools_dict = env.tools.get_tools()
        tools = list(tools_dict.values())
        logger.success(f"✓ 获取了 {len(tools)} 个工具")

        # 详细列出所有工具
        logger.info("")
        logger.info("  可用工具详情:")
        for i, tool in enumerate(tools, 1):
            logger.info(f"  {i}. {tool.name}")
            if tool.short_desc:
                logger.info(f"     描述: {tool.short_desc}")
            if tool.long_desc:
                logger.info(f"     详细: {tool.long_desc[:100]}...")

            # 显示参数
            if hasattr(tool.params, 'model_fields'):
                params = tool.params.model_fields
                if params:
                    logger.info(f"     参数:")
                    for param_name, field_info in params.items():
                        param_type = field_info.annotation
                        logger.info(f"       - {param_name}: {param_type}")
            logger.info("")
    except Exception as e:
        logger.error(f"✗ 获取工具失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

    # 3. 创建临时 MCP server
    logger.info("步骤 3: 创建临时 MCP server")
    try:
        server = Tau2MCPServer(tools)
        url = server.get_url()
        logger.success(f"✓ MCP server 已创建")
        logger.info(f"  URL: {url}")
    except Exception as e:
        logger.error(f"✗ 创建 MCP server 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

    # 4. 启动 MCP server (仅用于测试)
    logger.info("")
    logger.info("步骤 4: 启动 MCP server (测试)")
    try:
        server.start()
        logger.success(f"✓ MCP server 已启动并运行")
        logger.info(f"  服务器 URL: {url}")

        # 停止服务器
        import time
        time.sleep(1)
        server.stop()
        logger.info(f"✓ MCP server 已停止")
    except Exception as e:
        logger.error(f"✗ 启动/停止 MCP server 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

    # 总结
    logger.info("")
    logger.info("=" * 60)
    logger.success(f"✓ 所有测试通过!")
    logger.info("=" * 60)
    logger.info("")
    logger.info("你可以使用以下命令启动持久的 MCP server:")
    logger.info(f"  python scripts/start_airline_mcp_server.py --port 8765")
    logger.info("")
    logger.info("然后在 NexusAgent 中添加 MCP server:")
    logger.info(f"  名称: airline_tools")
    logger.info(f"  URL: http://127.0.0.1:8765/sse")
    logger.info(f"  类型: SSE")
    logger.info("")

    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
