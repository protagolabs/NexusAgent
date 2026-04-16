#!/bin/bash
# Test script for Agent Awareness integration

set -e

echo "========================================="
echo "Agent Awareness Integration Test"
echo "========================================="
echo ""

# Check if NexusAgent backend is running
echo "1. Checking NexusAgent backend..."
if curl -s http://localhost:8000/api/agents/default_agent/awareness > /dev/null 2>&1; then
    echo "   ✓ NexusAgent backend is running"
else
    echo "   ✗ NexusAgent backend is not running"
    echo "   Please start the backend first: cd .. && ./run.sh start"
    exit 1
fi

echo ""
echo "2. Checking current agent awareness..."
CURRENT_AWARENESS=$(curl -s http://localhost:8000/api/agents/default_agent/awareness | jq -r '.awareness // "null"')
if [ "$CURRENT_AWARENESS" = "null" ]; then
    echo "   ℹ No awareness set yet"
else
    echo "   ℹ Current awareness length: ${#CURRENT_AWARENESS} chars"
    echo "   Preview: ${CURRENT_AWARENESS:0:100}..."
fi

echo ""
echo "3. Running tau2 test (this will update awareness)..."
cd "$(dirname "$0")/.."
source .venv/bin/activate

tau2 run --domain airline \
    --agent-llm nexusagent \
    --user-llm gpt-4.1 \
    --num-trials 1 \
    --max-concurrency 1

echo ""
echo "4. Verifying awareness after test..."
NEW_AWARENESS=$(curl -s http://localhost:8000/api/agents/default_agent/awareness | jq -r '.awareness // "null"')
if [ "$NEW_AWARENESS" = "null" ]; then
    echo "   ✗ Awareness still not set!"
    exit 1
else
    echo "   ✓ Awareness has been updated"
    echo "   Length: ${#NEW_AWARENESS} chars"
    echo ""
    echo "   Content preview:"
    echo "   ----------------------------------------"
    echo "${NEW_AWARENESS:0:500}"
    echo "   ..."
    echo "   ----------------------------------------"
fi

echo ""
echo "========================================="
echo "✓ Test completed successfully!"
echo "========================================="
