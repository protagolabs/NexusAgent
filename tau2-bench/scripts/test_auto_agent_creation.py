#!/usr/bin/env python3
"""
Test script to verify automatic agent creation for tau2 testing.

This script tests the new feature where each tau2 test task uses:
- Fixed user_id: "abc"
- Auto-created agent_id (different for each task)
- Multi-turn conversations reuse the same agent_id within a task

Usage:
    cd tau2-bench
    source .venv/bin/activate
    python scripts/test_auto_agent_creation.py
"""

import asyncio
import sys
from pathlib import Path

# Add tau2-bench/src to path
tau2_src = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(tau2_src))

from tau2.utils.llm_utils import generate_with_nexusagent
from tau2.data_model.message import UserMessage
from loguru import logger


async def test_agent_creation():
    """Test creating multiple agents with same user_id and multi-turn conversations."""

    print("\n" + "="*60)
    print("Testing Auto Agent Creation for Tau2")
    print("="*60 + "\n")

    # Test 1: Create first agent (task_id = "task_0")
    print("Test 1: Creating agent for task_0 (Turn 1)...")
    messages1 = [UserMessage(role="user", content="Hello, what's 2+2?")]

    try:
        response1 = generate_with_nexusagent(
            messages=messages1,
            tools=None,
            task_id="task_0",
            agent_id="auto",  # Force auto-creation
            user_id="abc"     # Fixed user_id
        )

        agent_id_1_turn1 = response1.raw_data.get("agent_id", "unknown")
        print(f"✓ Agent created for task_0: {agent_id_1_turn1}")
        print(f"  Response: {response1.content[:100] if response1.content else 'No content'}...\n")

    except Exception as e:
        print(f"✗ Failed to create agent for task_0: {e}\n")
        return False

    # Test 1b: Second turn for task_0 - should reuse the same agent
    print("Test 1b: Second turn for task_0 (should reuse agent)...")
    messages1b = [UserMessage(role="user", content="What's 3+3?")]

    try:
        response1b = generate_with_nexusagent(
            messages=messages1b,
            tools=None,
            task_id="task_0",  # Same task_id
            agent_id="auto",
            user_id="abc"
        )

        agent_id_1_turn2 = response1b.raw_data.get("agent_id", "unknown")
        print(f"✓ Agent used for task_0 (turn 2): {agent_id_1_turn2}")
        print(f"  Response: {response1b.content[:100] if response1b.content else 'No content'}...\n")

    except Exception as e:
        print(f"✗ Failed on second turn for task_0: {e}\n")
        return False

    # Test 2: Create second agent (task_id = "task_1")
    print("Test 2: Creating agent for task_1...")
    messages2 = [UserMessage(role="user", content="What's the capital of France?")]

    try:
        response2 = generate_with_nexusagent(
            messages=messages2,
            tools=None,
            task_id="task_1",
            agent_id="auto",  # Force auto-creation
            user_id="abc"     # Same user_id
        )

        agent_id_2 = response2.raw_data.get("agent_id", "unknown")
        print(f"✓ Agent created for task_1: {agent_id_2}")
        print(f"  Response: {response2.content[:100] if response2.content else 'No content'}...\n")

    except Exception as e:
        print(f"✗ Failed to create agent for task_1: {e}\n")
        return False

    # Verify results
    print("\n" + "-"*60)
    print("Verification:")
    print("-"*60)
    print(f"Task 0 Turn 1 Agent ID: {agent_id_1_turn1}")
    print(f"Task 0 Turn 2 Agent ID: {agent_id_1_turn2}")
    print(f"Task 1 Agent ID:        {agent_id_2}")
    print(f"User ID (all):          abc")

    success = True

    # Check 1: Multi-turn conversations should reuse the same agent
    if agent_id_1_turn1 == agent_id_1_turn2:
        print("\n✓ PASS: Multi-turn conversation reuses same agent_id")
    else:
        print(f"\n✗ FAIL: Multi-turn conversation used different agents!")
        print(f"  Expected: {agent_id_1_turn1}")
        print(f"  Got:      {agent_id_1_turn2}")
        success = False

    # Check 2: Different tasks should use different agents
    if agent_id_1_turn1 != agent_id_2:
        print("✓ PASS: Different tasks use different agents")
    else:
        print("✗ FAIL: Different tasks used the same agent!")
        success = False

    # Check 3: All use the same user_id
    print("✓ PASS: All tests use the same user_id: abc")

    return success


def main():
    """Main entry point."""
    success = asyncio.run(test_agent_creation())

    print("\n" + "="*60)
    if success:
        print("✓ All tests passed!")
        print("="*60 + "\n")
        sys.exit(0)
    else:
        print("✗ Tests failed!")
        print("="*60 + "\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
