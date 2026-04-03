#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# NarraNexus — Local Development Server
# Starts all backend services + frontend dev server. Ctrl+C stops everything.
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# --- Platform-aware SQLite path ---
case "$(uname -s)" in
  Darwin) DB_DIR="$HOME/Library/Application Support/NarraNexus" ;;
  *)      DB_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/NarraNexus" ;;
esac
mkdir -p "$DB_DIR"
export DATABASE_URL="sqlite:///$DB_DIR/nexus.db"

# --- Colors ---
C="\033[36m"  # cyan
G="\033[32m"  # green
Y="\033[33m"  # yellow
R="\033[0m"   # reset

# --- Banner ---
echo ""
echo -e "${C}  _   _                    _   _                    ${R}"
echo -e "${C} | \ | | __ _ _ __ _ __ __|  \| | _____  ___   _ ___${R}"
echo -e "${C} |  \| |/ _\` | '__| '__/ _\` | |\` |/ _ \\ \\/ / | | / __|${R}"
echo -e "${C} | |\\ | (_| | |  | | | (_| | |\\ |  __/>  <| |_| \\__ \\${R}"
echo -e "${C} |_| \\_|\\__,_|_|  |_|  \\__,_|_| \\_|\\___/_/\\_\\\\__,_|___/${R}"
echo ""
echo -e "  ${G}Local Development Server${R}"
echo -e "  Database: ${Y}$DATABASE_URL${R}"
echo ""

# --- Cleanup on exit ---
PIDS=()
cleanup() {
  echo ""
  echo -e "${Y}Stopping all services...${R}"
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null
  done
  wait 2>/dev/null
  echo -e "${G}All services stopped.${R}"
}
trap cleanup EXIT INT TERM

# --- Start a service ---
start_service() {
  local label="$1"; shift
  echo -e "  ${C}>>>${R} ${label}"
  "$@" &
  PIDS+=($!)
}

# --- Backend services ---
echo -e "${G}Starting backend services...${R}"
echo ""
start_service "Backend API        :8000" uv run uvicorn backend.main:app --port 8000
start_service "MCP Server              " uv run python src/xyz_agent_context/module/module_runner.py mcp
start_service "Module Poller            " uv run python -m xyz_agent_context.services.module_poller
start_service "Job Trigger              " uv run python src/xyz_agent_context/module/job_module/job_trigger.py

# --- Frontend dev server ---
echo ""
echo -e "${G}Starting frontend...${R}"
echo ""
start_service "Frontend           :5173" bash -c "cd '$PROJECT_ROOT/frontend' && npm run dev"

echo ""
echo -e "${G}All services started.${R}"
echo ""
echo -e "  Frontend:  ${C}http://localhost:5173${R}"
echo -e "  Backend:   ${C}http://localhost:8000${R}"
echo -e "  API docs:  ${C}http://localhost:8000/docs${R}"
echo ""
echo -e "  Press ${Y}Ctrl+C${R} to stop all services."
echo ""

# Wait for any child to exit
wait
