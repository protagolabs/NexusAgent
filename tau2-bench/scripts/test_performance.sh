#!/bin/bash
# Performance test script for tau2-bench with NexusAgent
# 测试 tau2-bench 与 NexusAgent 的性能

set -e

echo "🚀 Starting performance test..."
echo "================================"

# Clean up any lingering processes
echo "🧹 Cleaning up old processes..."
pkill -f "tau2.*nexusagent" 2>/dev/null || true
pkill -f "start_airline_mcp_server" 2>/dev/null || true
sleep 2

# Check current MCP servers
active_servers=$(lsof -i -P -n 2>/dev/null | grep LISTEN | grep -E ":(5[6-9][0-9]{3})" | wc -l || echo "0")
echo "📊 Active MCP servers before test: $active_servers"

# Run a quick test with timing
echo ""
echo "⏱️  Running timed test (1 trial, task 20)..."
echo "================================"

time tau2 run \
  --domain airline \
  --agent-llm nexusagent \
  --user-llm gpt-4.1 \
  --num-trials 1 \
  --max-concurrency 1 \
  --task-ids 20 \
  --log-level ERROR

echo ""
echo "================================"
echo "✅ Test complete!"

# Check for lingering processes
sleep 2
active_servers_after=$(lsof -i -P -n 2>/dev/null | grep LISTEN | grep -E ":(5[6-9][0-9]{3})" | wc -l || echo "0")
echo "📊 Active MCP servers after test: $active_servers_after"

if [ "$active_servers_after" -gt "$active_servers" ]; then
    echo "⚠️  WARNING: $((active_servers_after - active_servers)) new MCP server(s) not cleaned up!"
else
    echo "✅ No MCP server leaks detected"
fi
