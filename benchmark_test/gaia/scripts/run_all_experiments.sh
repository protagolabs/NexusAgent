#!/usr/bin/env bash
# ============================================================================
# GAIA Exploration Experiments — All 3 experiments, both agents, sequential
# Usage:  caffeinate bash scripts/run_all_experiments.sh
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."   # benchmark_test/gaia/

LOG="validation_explore_results/run_all_$(date +%Y%m%d_%H%M%S).log"
mkdir -p validation_explore_results

echo "=== GAIA Exploration Experiments ===" | tee "$LOG"
echo "Started: $(date)" | tee -a "$LOG"
echo "" | tee -a "$LOG"

# ── Experiment 1: Answer Validator (7 questions × 2 agents) ─────────────────

# echo ">>> [1/6] Exp1 Validator — Agent A" | tee -a "$LOG"
# PYTHONUNBUFFERED=1 uv run python scripts/run_gaia_explore.py --indices 22,25,108,116,157,158,159 \
#   --split validation --agent-id agent_2be97fdd0b81 --validate \
#   --experiment exp1_validator --output-name agent_A_results 2>&1 | tee -a "$LOG"

echo ">>> [2/6] Exp1 Validator — Agent B" | tee -a "$LOG"
PYTHONUNBUFFERED=1 uv run python scripts/run_gaia_explore.py --indices 22,25,108,116,157,158,159 \
  --split validation --agent-id agent_5fb1f0f97b2b --validate \
  --experiment exp1_validator --output-name agent_B_results 2>&1 | tee -a "$LOG"

# ── Experiment 2: Regression Retry (13 questions × 2 agents) ────────────────

echo ">>> [3/6] Exp2 Retry — Agent A" | tee -a "$LOG"
PYTHONUNBUFFERED=1 uv run python scripts/run_gaia_explore.py --indices 19,20,21,33,40,49,52,54,57,78,120,133,136 \
  --split validation --agent-id agent_2be97fdd0b81 \
  --experiment exp2_retry --output-name agent_A_results 2>&1 | tee -a "$LOG"

echo ">>> [4/6] Exp2 Retry — Agent B" | tee -a "$LOG"
PYTHONUNBUFFERED=1 uv run python scripts/run_gaia_explore.py --indices 19,20,21,33,40,49,52,54,57,78,120,133,136 \
  --split validation --agent-id agent_5fb1f0f97b2b \
  --experiment exp2_retry --output-name agent_B_results 2>&1 | tee -a "$LOG"

# ── Experiment 3: No-Answer Rerun (8 questions × 2 agents) ──────────────────

echo ">>> [5/6] Exp3 Rerun — Agent A" | tee -a "$LOG"
PYTHONUNBUFFERED=1 uv run python scripts/run_gaia_explore.py --indices 12,31,34,60,62,63,102,164 \
  --split validation --agent-id agent_2be97fdd0b81 \
  --experiment exp3_rerun --output-name agent_A_results 2>&1 | tee -a "$LOG"

echo ">>> [6/6] Exp3 Rerun — Agent B" | tee -a "$LOG"
PYTHONUNBUFFERED=1 uv run python scripts/run_gaia_explore.py --indices 12,31,34,60,62,63,102,164 \
  --split validation --agent-id agent_5fb1f0f97b2b \
  --experiment exp3_rerun --output-name agent_B_results 2>&1 | tee -a "$LOG"

# ── Summary ──────────────────────────────────────────────────────────────────

echo "" | tee -a "$LOG"
echo "=== ALL EXPERIMENTS COMPLETE ===" | tee -a "$LOG"
echo "Finished: $(date)" | tee -a "$LOG"
echo "" | tee -a "$LOG"
echo "Result files:" | tee -a "$LOG"
find validation_explore_results/ -name "*.json" | sort | tee -a "$LOG"
echo "" | tee -a "$LOG"
echo "Full log: $LOG"
