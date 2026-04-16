#!/usr/bin/env python3
"""
调试 MCP server 连接问题

这个脚本会：
1. 启动一个测试 MCP server
2. 尝试连接并验证
3. 提供详细的诊断信息
"""

import asyncio
import sys
from pathlib import Path

# 添加路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
from loguru import logger


async def test_sse_connection(url: str, timeout: float = 10.0):
    """测试 SSE 连接"""
    logger.info(f"测试连接到: {url}")

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0)
        ) as client:
            logger.info(f"  → 发送 GET 请求...")
            async with client.stream(
                "GET",
                url,
                headers={
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                },
                follow_redirects=True
            ) as response:
                logger.info(f"  ✓ 收到响应")
                logger.info(f"    状态码: {response.status_code}")
                logger.info(f"    响应头:")
                for key, value in response.headers.items():
                    logger.info(f"      {key}: {value}")

                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "")
                    if "text/event-stream" in content_type:
                        logger.success("  ✓ Content-Type 正确 (text/event-stream)")

                        # 尝试读取第一个数据块
                        logger.info(f"  → 尝试读取数据...")
                        try:
                            chunk_count = 0
                            async for chunk in response.aiter_bytes():
                                if chunk:
                                    chunk_count += 1
                                    logger.info(f"  ✓ 收到数据块 #{chunk_count} ({len(chunk)} bytes)")
                                    logger.debug(f"    内容: {chunk[:200]}")
                                    if chunk_count >= 3:  # 只读前3个块
                                        break
                        except Exception as e:
                            logger.warning(f"  ⚠ 读取数据时出错: {e}")

                        return True, None
                    else:
                        msg = f"Content-Type 错误: {content_type} (期望 text/event-stream)"
                        logger.error(f"  ✗ {msg}")
                        return False, msg
                else:
                    # 读取错误响应
                    error_body = ""
                    try:
                        async for chunk in response.aiter_bytes():
                            error_body += chunk.decode("utf-8", errors="ignore")
                            if len(error_body) > 500:
                                break
                    except Exception:
                        pass

                    msg = f"HTTP {response.status_code}: {error_body[:500]}"
                    logger.error(f"  ✗ {msg}")
                    return False, msg

    except httpx.ConnectError as e:
        msg = f"连接失败: {e}"
        logger.error(f"  ✗ {msg}")
        return False, msg
    except httpx.TimeoutException as e:
        msg = f"连接超时: {e}"
        logger.error(f"  ✗ {msg}")
        return False, msg
    except Exception as e:
        msg = f"未知错误: {e}"
        logger.error(f"  ✗ {msg}")
        import traceback
        logger.error(traceback.format_exc())
        return False, msg


async def main():
    """主函数"""
    logger.info("=" * 80)
    logger.info("  MCP Server 连接调试工具")
    logger.info("=" * 80)
    logger.info("")

    # 测试 URL
    test_urls = [
        "http://127.0.0.1:8765/sse",  # 默认端口
    ]

    logger.info("步骤 1: 测试本地 MCP server")
    logger.info("请确保你已经启动了 MCP server:")
    logger.info("  python scripts/start_airline_mcp_server.py --port 8765")
    logger.info("")

    for url in test_urls:
        logger.info(f"测试 URL: {url}")
        success, error = await test_sse_connection(url)
        logger.info("")

        if success:
            logger.success(f"✓ 连接成功: {url}")
        else:
            logger.error(f"✗ 连接失败: {url}")
            logger.error(f"  错误: {error}")

        logger.info("-" * 80)
        logger.info("")

    logger.info("")
    logger.info("步骤 2: 常见问题排查")
    logger.info("")
    logger.info("如果连接失败，请检查：")
    logger.info("")
    logger.info("1. MCP server 是否正在运行？")
    logger.info("   → 运行: python scripts/start_airline_mcp_server.py")
    logger.info("")
    logger.info("2. 端口是否正确？")
    logger.info("   → 检查 MCP server 输出的 URL")
    logger.info("   → 默认端口是 8765")
    logger.info("")
    logger.info("3. 防火墙是否阻止连接？")
    logger.info("   → 检查防火墙设置")
    logger.info("")
    logger.info("4. URL 格式是否正确？")
    logger.info("   → 格式: http://127.0.0.1:PORT/sse")
    logger.info("   → 注意最后的 /sse 路径")
    logger.info("")
    logger.info("5. FastMCP 是否正确安装？")
    logger.info("   → pip install fastmcp>=2.14.1")
    logger.info("")
    logger.info("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
