#!/bin/bash
# Quick test for auto agent creation feature
# This script runs a simple tau2 test to verify that each task gets a unique agent_id

set -e

echo "========================================"
echo "Tau2 Auto Agent Creation Quick Test"
echo "========================================"
echo ""

# Check if we're in the right directory
if [ ! -d "src/tau2" ]; then
    echo "Error: Please run this script from the tau2-bench directory"
    echo "Usage: cd tau2-bench && bash scripts/quick_test_auto_agent.sh"
    exit 1
fi

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
else
    echo "Error: Virtual environment not found. Please create it first:"
    echo "  python -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -e ."
    exit 1
fi

# Check environment variables
echo "Checking configuration..."
echo "  NEXUSAGENT_BACKEND_URL: ${NEXUSAGENT_BACKEND_URL:-ws://localhost:8000}"
echo "  NEXUSAGENT_AGENT_ID: ${NEXUSAGENT_AGENT_ID:-auto (default)}"
echo "  NEXUSAGENT_USER_ID: ${NEXUSAGENT_USER_ID:-abc (default)}"
echo ""

# Ensure user 'abc' exists in NexusAgent
echo "Note: Make sure user 'abc' exists in NexusAgent database"
echo "      Run this if needed:"
echo "      curl -X POST http://localhost:8000/api/auth/create-user \\"
echo "           -H 'Content-Type: application/json' \\"
echo "           -d '{\"user_id\": \"abc\", \"secret_key\": \"YOUR_SECRET\"}'"
echo ""

# Run a simple test (2 tasks to verify different agents are created)
echo "Running tau2 test with 2 tasks..."
echo "This will create 2 different agents, both using user_id='abc'"
echo ""

tau2 run \
    --domain airline \
    --agent-llm nexusagent \
    --user-llm gpt-4.1 \
    --num-trials 1 \
    --max-concurrency 1 \
    --task-ids 0 1

echo ""
echo "========================================"
echo "Test completed!"
echo "========================================"
echo ""
echo "Check the logs above to verify:"
echo "  ✓ Each task created a new agent (different agent_id)"
echo "  ✓ Both tasks used user_id='abc'"
echo ""
