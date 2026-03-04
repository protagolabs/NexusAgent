#!/usr/bin/env bash
#
# clean-for-test.sh — 清理所有安装产物，模拟全新环境以测试三阶段安装流程
#
# 用法: bash desktop/scripts/clean-for-test.sh [--all]
#
#   默认:  清理项目内产物（.venv, node_modules, dist, .evermemos, Docker 容器, setupComplete）
#   --all: 额外卸载 uv、claude CLI、Docker（真正模拟什么都没装的状态）
#

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
skip()  { echo -e "[ ] $1 — skipped (not found)"; }

CLEAN_ALL=false
if [[ "${1:-}" == "--all" ]]; then
  CLEAN_ALL=true
fi

echo ""
echo "=========================================="
echo "  NarraNexus 安装产物清理脚本"
echo "  项目根目录: $PROJECT_ROOT"
echo "=========================================="
echo ""

# ─── 1. 停止并删除 Docker 容器 ──────────────────────

echo "── Docker 容器 ──"

# MySQL (项目根目录的 docker-compose.yaml)
if [ -f "$PROJECT_ROOT/docker-compose.yaml" ]; then
  echo "Stopping MySQL containers..."
  (cd "$PROJECT_ROOT" && docker compose down -v 2>/dev/null || docker-compose down -v 2>/dev/null || true)
  info "MySQL containers removed"
else
  skip "docker-compose.yaml"
fi

# EverMemOS
if [ -f "$PROJECT_ROOT/.evermemos/docker-compose.yaml" ]; then
  echo "Stopping EverMemOS containers..."
  (cd "$PROJECT_ROOT/.evermemos" && docker compose down -v 2>/dev/null || docker-compose down -v 2>/dev/null || true)
  info "EverMemOS containers removed"
else
  skip ".evermemos/docker-compose.yaml"
fi

echo ""

# ─── 2. 删除 Python 虚拟环境 ──────────────────────

echo "── Python 虚拟环境 ──"

if [ -d "$PROJECT_ROOT/.venv" ]; then
  rm -rf "$PROJECT_ROOT/.venv"
  info "Removed .venv"
else
  skip ".venv"
fi

if [ -d "$PROJECT_ROOT/.evermemos/.venv" ]; then
  rm -rf "$PROJECT_ROOT/.evermemos/.venv"
  info "Removed .evermemos/.venv"
else
  skip ".evermemos/.venv"
fi

echo ""

# ─── 3. 删除 EverMemOS 克隆 ──────────────────────

echo "── EverMemOS ──"

if [ -d "$PROJECT_ROOT/.evermemos" ]; then
  rm -rf "$PROJECT_ROOT/.evermemos"
  info "Removed .evermemos/"
else
  skip ".evermemos/"
fi

echo ""

# ─── 4. 删除前端构建产物 ──────────────────────────

echo "── 前端 ──"

if [ -d "$PROJECT_ROOT/frontend/node_modules" ]; then
  rm -rf "$PROJECT_ROOT/frontend/node_modules"
  info "Removed frontend/node_modules"
else
  skip "frontend/node_modules"
fi

if [ -d "$PROJECT_ROOT/frontend/dist" ]; then
  rm -rf "$PROJECT_ROOT/frontend/dist"
  info "Removed frontend/dist"
else
  skip "frontend/dist"
fi

echo ""

# ─── 5. 重置 Electron setupComplete 标记 ──────────

echo "── Electron 状态 ──"

# macOS
CONFIG_MAC="$HOME/Library/Application Support/NarraNexus/config.json"
# Linux
CONFIG_LINUX="$HOME/.config/NarraNexus/config.json"

reset_config() {
  local path="$1"
  if [ -f "$path" ]; then
    echo '{"setupComplete": false}' > "$path"
    info "Reset setupComplete in $path"
  fi
}

reset_config "$CONFIG_MAC"
reset_config "$CONFIG_LINUX"

# 也清理打包模式下的 project 副本（如果存在）
PACKAGED_PROJECT_MAC="$HOME/Library/Application Support/NarraNexus/project"
PACKAGED_PROJECT_LINUX="$HOME/.config/NarraNexus/project"

if [ -d "$PACKAGED_PROJECT_MAC" ]; then
  rm -rf "$PACKAGED_PROJECT_MAC"
  info "Removed packaged project copy (macOS)"
fi
if [ -d "$PACKAGED_PROJECT_LINUX" ]; then
  rm -rf "$PACKAGED_PROJECT_LINUX"
  info "Removed packaged project copy (Linux)"
fi

echo ""

# ─── 6. 可选：卸载 uv 和 claude CLI ─────────────

if $CLEAN_ALL; then
  echo "── 系统工具（--all 模式）──"

  # uv
  if command -v uv &>/dev/null; then
    UV_BIN="$(which uv)"
    rm -f "$UV_BIN" 2>/dev/null || true
    rm -rf "$HOME/.cargo/bin/uv" "$HOME/.cargo/bin/uvx" 2>/dev/null || true
    rm -rf "$HOME/.local/bin/uv" "$HOME/.local/bin/uvx" 2>/dev/null || true
    info "Removed uv"
  else
    skip "uv (not installed)"
  fi

  # claude CLI
  if command -v claude &>/dev/null; then
    CLAUDE_BIN="$(which claude)"
    rm -f "$CLAUDE_BIN" 2>/dev/null || true
    npm uninstall -g @anthropic-ai/claude-code 2>/dev/null || true
    info "Removed claude CLI"
  else
    skip "claude CLI (not installed)"
  fi

  # Docker
  echo ""
  echo "── Docker（--all 模式）──"

  if [[ "$(uname)" == "Darwin" ]]; then
    # ── macOS ──

    # 1) 停止 Colima（如果在跑）
    if command -v colima &>/dev/null; then
      colima stop 2>/dev/null || true
      info "Stopped Colima"
    fi

    # 2) 关闭 Docker Desktop（如果在跑）
    if pgrep -x "Docker" &>/dev/null || pgrep -f "Docker Desktop" &>/dev/null; then
      osascript -e 'quit app "Docker"' 2>/dev/null || true
      sleep 2
      info "Quit Docker Desktop"
    fi

    # 3) 卸载 Colima + Docker CLI（brew）
    if command -v brew &>/dev/null; then
      for pkg in colima docker docker-compose docker-credential-helper; do
        if brew list "$pkg" &>/dev/null; then
          brew uninstall --force "$pkg" 2>/dev/null || true
          info "brew uninstall $pkg"
        fi
      done
    fi

    # 4) 删除 Docker Desktop.app
    if [ -d "/Applications/Docker.app" ]; then
      rm -rf "/Applications/Docker.app" 2>/dev/null || sudo rm -rf "/Applications/Docker.app" 2>/dev/null || true
      info "Removed /Applications/Docker.app"
    fi

    # 5) 清理 Docker Desktop 数据
    rm -rf "$HOME/Library/Group Containers/group.com.docker" 2>/dev/null || true
    rm -rf "$HOME/Library/Containers/com.docker.docker" 2>/dev/null || true
    rm -rf "$HOME/Library/Application Support/Docker Desktop" 2>/dev/null || true
    rm -rf "$HOME/.docker" 2>/dev/null || true

    # 6) 清理 Colima 数据
    rm -rf "$HOME/.colima" 2>/dev/null || true

    info "Docker cleaned (macOS)"

  else
    # ── Linux ──

    # 1) 停止 Docker daemon
    sudo systemctl stop docker.socket docker.service 2>/dev/null || true

    # 2) 卸载 Docker 包
    if command -v apt-get &>/dev/null; then
      sudo apt-get purge -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin 2>/dev/null || true
      sudo apt-get autoremove -y 2>/dev/null || true
      info "apt purge docker"
    elif command -v yum &>/dev/null; then
      sudo yum remove -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin 2>/dev/null || true
      info "yum remove docker"
    elif command -v dnf &>/dev/null; then
      sudo dnf remove -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin 2>/dev/null || true
      info "dnf remove docker"
    else
      warn "Unknown package manager, please uninstall Docker manually"
    fi

    # 3) 清理 Docker 数据
    sudo rm -rf /var/lib/docker /var/lib/containerd 2>/dev/null || true
    rm -rf "$HOME/.docker" 2>/dev/null || true

    # 4) 删除独立 docker-compose（如果存在）
    if [ -f /usr/local/bin/docker-compose ]; then
      sudo rm -f /usr/local/bin/docker-compose
      info "Removed /usr/local/bin/docker-compose"
    fi

    info "Docker cleaned (Linux)"
  fi

  echo ""
fi

# ─── 7. 清理占用端口的残留进程 ──────────────────

echo "── 残留进程 ──"

PORTS=(8000 7801 7802 7803 7804 7805 1995 3306)
killed=false

for port in "${PORTS[@]}"; do
  pids=$(lsof -ti ":$port" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    for pid in $pids; do
      kill -9 "$pid" 2>/dev/null || true
      warn "Killed PID $pid on port $port"
      killed=true
    done
  fi
done

if ! $killed; then
  info "No stale processes on service ports"
fi

echo ""
echo "=========================================="
echo -e "  ${GREEN}清理完成！${NC}"
echo ""
echo "  现在可以启动 Desktop App 测试安装流程："
echo "    cd desktop && npm run dev"
echo ""
if ! $CLEAN_ALL; then
  echo "  提示: 加 --all 参数可以额外卸载 uv、claude CLI、Docker"
  echo "    bash desktop/scripts/clean-for-test.sh --all"
  echo ""
fi
echo "=========================================="
