#!/bin/bash
# Test script for retail domain with all fixes applied

set -e  # Exit on error

echo "=========================================="
echo "Tau2 Retail Test with All Fixes"
echo "=========================================="
echo ""

# Check if we're in tau2-bench directory
if [ ! -f "pyproject.toml" ]; then
    echo "❌ Error: Must run from tau2-bench directory"
    exit 1
fi

# Check if virtual environment is activated
if [ -z "$VIRTUAL_ENV" ]; then
    echo "⚠️  Virtual environment not activated"
    echo "   Activating .venv..."
    source .venv/bin/activate
fi

# Check if NEXUSAGENT_AGENT_ID is set
if [ -z "$NEXUSAGENT_AGENT_ID" ]; then
    echo "⚠️  NEXUSAGENT_AGENT_ID not set"
    echo "   Please export NEXUSAGENT_AGENT_ID='your_agent_id'"
    echo "   Example: export NEXUSAGENT_AGENT_ID='agent_4a93eaef5e44'"
    exit 1
fi

echo "✅ Environment checks passed"
echo "   Agent ID: $NEXUSAGENT_AGENT_ID"
echo "   Virtual env: $VIRTUAL_ENV"
echo ""

# Check if NexusAgent backend is running
echo "🔍 Checking NexusAgent backend..."
BACKEND_URL="${NEXUSAGENT_BACKEND_URL:-ws://localhost:8000}"
HTTP_URL=$(echo $BACKEND_URL | sed 's/ws:/http:/')

if curl -s -f -m 5 "$HTTP_URL/health" > /dev/null 2>&1; then
    echo "✅ NexusAgent backend is running at $BACKEND_URL"
else
    echo "❌ NexusAgent backend is NOT running at $BACKEND_URL"
    echo "   Please start the backend first:"
    echo "   cd /Users/zihengs/Desktop/NexusAgent/backend"
    echo "   python -m uvicorn main:app --reload --port 8000"
    exit 1
fi
echo ""

# Run the test
echo "🚀 Starting retail domain test..."
echo "   Domain: retail"
echo "   Task ID: 0"
echo "   Agent: nexusagent"
echo "   User simulator: gpt-4.1"
echo ""

tau2 run \
  --domain retail \
  --agent-llm nexusagent \
  --user-llm gpt-4.1 \
  --num-trials 1 \
  --max-concurrency 1 \
  --task-ids 0

echo ""
echo "=========================================="
echo "Test completed!"
echo "=========================================="
echo ""
echo "📊 Check the results in:"
echo "   data/simulations/"
echo ""
echo "🔍 If you see errors, check:"
echo "   1. NexusAgent backend logs for detailed ExceptionGroup info"
echo "   2. Order ID normalization in MCP server logs"
echo "   3. Trajectory recording in simulation output"
