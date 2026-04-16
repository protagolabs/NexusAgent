#!/usr/bin/env python3
"""
验证 MCP 依赖和集成是否正确配置

这个脚本会检查：
1. 必需的 Python 包是否已安装
2. NexusAgent backend 是否运行
3. 环境变量是否正确配置
4. MCP server 是否可以启动
"""

import sys
import importlib
import os
from pathlib import Path

# 颜色输出
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def print_status(status, message):
    """打印状态消息"""
    if status == "ok":
        print(f"{Colors.GREEN}✓{Colors.ENDC} {message}")
        return True
    elif status == "error":
        print(f"{Colors.RED}✗{Colors.ENDC} {message}")
        return False
    elif status == "warning":
        print(f"{Colors.YELLOW}⚠{Colors.ENDC} {message}")
        return True
    elif status == "info":
        print(f"{Colors.BLUE}ℹ{Colors.ENDC} {message}")
        return True

def check_package(package_name, import_path=None):
    """检查 Python 包是否已安装"""
    try:
        if import_path:
            module = importlib.import_module(import_path)
        else:
            module = importlib.import_module(package_name)
        return print_status("ok", f"{package_name} 已安装")
    except ImportError as e:
        return print_status("error", f"{package_name} 未安装: {e}")

def check_environment():
    """检查环境变量"""
    required_vars = {
        "NEXUSAGENT_BACKEND_URL": "ws://localhost:8000",
        "NEXUSAGENT_AGENT_ID": "(需要配置)",
        "NEXUSAGENT_USER_ID": "(需要配置)",
    }

    all_ok = True
    for var, default in required_vars.items():
        value = os.getenv(var)
        if value:
            print_status("ok", f"{var}={value}")
        else:
            print_status("warning", f"{var} 未设置 (默认: {default})")
            all_ok = False

    return all_ok

def check_nexusagent_backend():
    """检查 NexusAgent backend 是否运行"""
    try:
        import httpx
        response = httpx.get("http://localhost:8000/health", timeout=2.0)
        if response.status_code == 200:
            return print_status("ok", "NexusAgent backend 正在运行")
        else:
            return print_status("error", f"NexusAgent backend 返回状态码 {response.status_code}")
    except Exception as e:
        return print_status("error", f"无法连接到 NexusAgent backend: {e}")

def test_mcp_server():
    """测试 MCP server 是否可以启动"""
    try:
        from tau2.integrations.nexusagent.mcp_server import Tau2MCPServer
        from tau2.environment.tool import as_tool
        from pydantic import BaseModel

        # 创建一个简单的测试函数
        def test_tool(message: str) -> str:
            """A simple test tool that echoes the message.

            Args:
                message: The message to echo

            Returns:
                The echoed message
            """
            return f"Echo: {message}"

        # 转换为 Tool 对象
        tool = as_tool(test_tool)

        # 尝试创建 MCP server
        server = Tau2MCPServer([tool])
        url = server.get_url()

        print_status("ok", f"MCP server 可以创建 (URL: {url})")
        return True
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        return print_status("error", f"无法创建 MCP server: {e}\n{error_details}")

def main():
    print(f"\n{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.BOLD}  Tau2 + NexusAgent 集成验证{Colors.ENDC}")
    print(f"{Colors.BOLD}{'='*60}{Colors.ENDC}\n")

    all_checks_passed = True

    # 1. 检查 Python 包
    print(f"{Colors.BOLD}1. 检查 Python 包{Colors.ENDC}")
    packages = [
        ("websockets", None),
        ("mcp", "mcp.server.fastmcp"),
        ("fastmcp", None),
        ("tau2", None),
        ("httpx", None),
    ]

    for package, import_path in packages:
        if not check_package(package, import_path):
            all_checks_passed = False
    print()

    # 2. 检查环境变量
    print(f"{Colors.BOLD}2. 检查环境变量{Colors.ENDC}")
    if not check_environment():
        print_status("warning", "请在 .env 文件中配置缺失的环境变量")
    print()

    # 3. 检查 NexusAgent backend
    print(f"{Colors.BOLD}3. 检查 NexusAgent Backend{Colors.ENDC}")
    if not check_nexusagent_backend():
        all_checks_passed = False
        print_status("info", "请先启动 NexusAgent backend:")
        print(f"  cd /Users/zihengs/Desktop/NexusAgent")
        print(f"  uvicorn backend.main:app --host 0.0.0.0 --port 8000")
    print()

    # 4. 测试 MCP server
    print(f"{Colors.BOLD}4. 测试 MCP Server{Colors.ENDC}")
    if not test_mcp_server():
        all_checks_passed = False
    print()

    # 总结
    print(f"{Colors.BOLD}{'='*60}{Colors.ENDC}")
    if all_checks_passed:
        print(f"{Colors.GREEN}{Colors.BOLD}✓ 所有检查通过！{Colors.ENDC}")
        print(f"\n你现在可以运行 tau2 测试：")
        print(f"  tau2 run --domain airline --agent-llm nexusagent --user-llm gpt-4.1 --num-trials 1 --task-id 1")
    else:
        print(f"{Colors.RED}{Colors.BOLD}✗ 部分检查失败{Colors.ENDC}")
        print(f"\n请解决上述问题后重试。")
        print(f"\n安装缺失的依赖：")
        print(f"  cd tau2-bench")
        print(f"  pip install -e .")
    print(f"{Colors.BOLD}{'='*60}{Colors.ENDC}\n")

    sys.exit(0 if all_checks_passed else 1)

if __name__ == "__main__":
    main()
