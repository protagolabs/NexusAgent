#!/usr/bin/env python3
"""
启动一个独立的 MCP server 来暴露 airline 域的所有工具

这个脚本会：
1. 加载 airline 域的数据库和工具
2. 启动一个持久的 MCP server
3. 暴露所有 airline 工具通过 MCP 协议

使用方法：
    python scripts/start_airline_mcp_server.py --port 8765

然后你可以在 NexusAgent 中配置这个 MCP server：
    MCP URL: http://127.0.0.1:8765/sse
"""

import argparse
import asyncio
import signal
import sys
from pathlib import Path

# 添加 tau2-bench/src 到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from loguru import logger
from tau2.domains.airline.environment import get_environment
from tau2.integrations.nexusagent.mcp_server import Tau2MCPServer


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="启动 airline 域工具的 MCP server"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="MCP server 端口 (默认: 8765)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="MCP server 主机 (默认: 127.0.0.1)"
    )
    return parser.parse_args()


def setup_signal_handlers(server: Tau2MCPServer):
    """设置信号处理器，用于优雅关闭"""
    def signal_handler(sig, frame):
        logger.info("\n收到关闭信号，正在停止 MCP server...")
        server.stop()
        logger.info("MCP server 已停止")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def main():
    """主函数"""
    args = parse_args()

    logger.info("=" * 60)
    logger.info("  Airline MCP Server 启动器")
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
        sys.exit(1)

    # 2. 获取所有工具
    logger.info("")
    logger.info("步骤 2: 获取 airline 工具")
    try:
        tools_dict = env.tools.get_tools()
        tools = list(tools_dict.values())
        logger.success(f"✓ 获取了 {len(tools)} 个工具")

        # 列出所有工具
        logger.info("  可用工具:")
        for tool in tools:
            logger.info(f"    - {tool.name}: {tool.short_desc or '(无描述)'}")
    except Exception as e:
        logger.error(f"✗ 获取工具失败: {e}")
        sys.exit(1)

    # 3. 创建 MCP server
    logger.info("")
    logger.info("步骤 3: 创建 MCP server")
    try:
        server = Tau2MCPServer(tools, port=args.port)
        url = server.get_url()
        logger.success(f"✓ MCP server 已创建")
        logger.info(f"  URL: {url}")
    except Exception as e:
        logger.error(f"✗ 创建 MCP server 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

    # 4. 启动 MCP server
    logger.info("")
    logger.info("步骤 4: 启动 MCP server")
    try:
        server.start()
        logger.success(f"✓ MCP server 已启动")
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"🚀 Airline MCP Server 正在运行")
        logger.info("=" * 60)
        logger.info("")
        logger.info(f"MCP URL: {url}")
        logger.info(f"工具数量: {len(tools)}")
        logger.info("")
        logger.info("在 NexusAgent 中使用此 MCP server:")
        logger.info(f"  1. 打开 NexusAgent 前端")
        logger.info(f"  2. 进入 Agent 设置 -> MCP Servers")
        logger.info(f"  3. 添加新的 MCP server:")
        logger.info(f"     名称: airline_tools")
        logger.info(f"     URL: {url}")
        logger.info(f"     类型: SSE")
        logger.info("")
        logger.info("按 Ctrl+C 停止服务器")
        logger.info("=" * 60)

        # 设置信号处理器
        setup_signal_handlers(server)

        # 保持运行
        try:
            while True:
                asyncio.get_event_loop().run_until_complete(asyncio.sleep(1))
        except KeyboardInterrupt:
            pass

    except Exception as e:
        logger.error(f"✗ 启动 MCP server 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)
    finally:
        logger.info("正在清理...")
        server.stop()


if __name__ == "__main__":
    main()
