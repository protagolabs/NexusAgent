#!/usr/bin/env bash
# ============================================================================
# NarraNexus 桌面应用一键打包脚本
#
# 用法：
#   bash build-desktop.sh          # 自动检测平台打包
#   bash build-desktop.sh mac      # 打包 macOS DMG
#   bash build-desktop.sh linux    # 打包 Linux AppImage/deb
#   bash build-desktop.sh all      # 打包所有平台（需在 Mac 上执行）
#
# 产物位置：desktop/dist/
# ============================================================================

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_DIR="${PROJECT_ROOT}/desktop"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail()  { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

# ─── 前置检查 ───────────────────────────────────────

check_prerequisites() {
  info "检查前置依赖..."

  if ! command -v node &>/dev/null; then
    fail "未找到 Node.js，请先安装 Node.js >= 20"
  fi

  NODE_MAJOR=$(node -v | sed 's/v//' | cut -d. -f1)
  if [ "$NODE_MAJOR" -lt 20 ]; then
    fail "Node.js 版本过低 ($(node -v))，需要 >= 20"
  fi

  if ! command -v npm &>/dev/null; then
    fail "未找到 npm"
  fi

  ok "Node.js $(node -v) / npm $(npm -v)"
}

# ─── 安装依赖 ───────────────────────────────────────

install_deps() {
  info "安装 desktop 依赖..."
  cd "$DESKTOP_DIR"

  if [ ! -d "node_modules" ] || [ "package.json" -nt "node_modules/.package-lock.json" ]; then
    npm install --no-audit --no-fund
    ok "依赖安装完成"
  else
    ok "依赖已是最新"
  fi
}

# ─── 构建前端 ───────────────────────────────────────

build_frontend() {
  info "构建项目前端 (frontend/)..."
  cd "$PROJECT_ROOT/frontend"

  if [ ! -d "node_modules" ]; then
    npm install --no-audit --no-fund
  fi

  npm run build
  ok "前端构建完成 → frontend/dist/"
}

# ─── 编译 Electron ──────────────────────────────────

compile_electron() {
  info "编译 Electron 源码..."
  cd "$DESKTOP_DIR"
  npx electron-vite build
  ok "Electron 编译完成 → desktop/out/"
}

# ─── 打包 ───────────────────────────────────────────

package_app() {
  local target="$1"
  cd "$DESKTOP_DIR"

  case "$target" in
    mac)
      info "打包 macOS DMG..."
      npx electron-builder --mac
      ;;
    linux)
      info "打包 Linux AppImage/deb..."
      npx electron-builder --linux
      ;;
    all)
      info "打包所有平台..."
      npx electron-builder --mac --linux
      ;;
    *)
      fail "未知的打包目标: $target"
      ;;
  esac

  ok "打包完成！"
  echo ""
  info "产物位置："
  ls -lh "$DESKTOP_DIR/dist/"*.{dmg,AppImage,deb,snap} 2>/dev/null || true
  echo ""
}

# ─── 清理旧产物 ─────────────────────────────────────

clean() {
  info "清理旧的打包产物..."
  rm -rf "$DESKTOP_DIR/dist" "$DESKTOP_DIR/out"
  ok "清理完成"
}

# ─── 自动检测平台 ───────────────────────────────────

detect_platform() {
  case "$(uname -s)" in
    Darwin) echo "mac" ;;
    Linux)  echo "linux" ;;
    *)      fail "不支持的操作系统: $(uname -s)" ;;
  esac
}

# ─── 主流程 ─────────────────────────────────────────

main() {
  local target="${1:-}"

  echo ""
  echo "============================================"
  echo "  NarraNexus 桌面应用打包"
  echo "============================================"
  echo ""

  # 确定打包目标
  if [ -z "$target" ]; then
    target=$(detect_platform)
    info "自动检测平台: $target"
  fi

  check_prerequisites
  clean
  install_deps
  build_frontend
  compile_electron
  package_app "$target"

  echo "============================================"
  ok "全部完成！将 dist/ 中的安装包发给用户即可。"
  echo "============================================"
}

main "$@"
