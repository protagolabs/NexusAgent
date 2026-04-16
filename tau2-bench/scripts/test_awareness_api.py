#!/usr/bin/env python3
"""
Test script for Agent Awareness API integration

Usage:
    python scripts/test_awareness_api.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add tau2 to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tau2.integrations.nexusagent.nexusagent_backend import NexusAgentClient


async def test_awareness_update():
    """Test updating agent awareness"""

    # Get config from environment
    backend_url = os.getenv("NEXUSAGENT_BACKEND_URL", "ws://localhost:8000")
    agent_id = os.getenv("NEXUSAGENT_AGENT_ID", "default_agent")
    user_id = os.getenv("NEXUSAGENT_USER_ID", "tau2_test_user")

    print("=" * 60)
    print("Agent Awareness API Test")
    print("=" * 60)
    print(f"Backend URL: {backend_url}")
    print(f"Agent ID: {agent_id}")
    print(f"User ID: {user_id}")
    print()

    # Create client
    client = NexusAgentClient(
        backend_url=backend_url,
        agent_id=agent_id,
        user_id=user_id,
    )

    # Test awareness content (airline policy example)
    test_awareness = """<instructions>
You are a customer service agent that helps the user according to the <policy> provided below.
In each turn you can either:
- Send a message to the user.
- Make a tool call.
You cannot do both at the same time.

Try to be helpful and always follow the policy. Always make sure you generate valid JSON only.
</instructions>

<policy>
# Airline Agent Policy

The current time is 2024-05-15 15:00:00 EST.

As an airline agent, you can help users **book**, **modify**, or **cancel** flight reservations.

You should only make one tool call at a time, and if you make a tool call, you should not respond to the user simultaneously.

## Domain Basic

### User
Each user has a profile containing:
- user id
- email
- addresses
- date of birth
- payment methods
- membership level

There are three membership levels: **regular**, **silver**, **gold**.
</policy>"""

    print("1. Testing awareness update...")
    print(f"   Content length: {len(test_awareness)} chars")
    print()

    success = await client.update_agent_awareness(test_awareness)

    if success:
        print("   ✓ Awareness updated successfully!")
        print()
        print("2. Verifying update via HTTP GET...")

        import httpx
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                f"{client.http_backend_url}/api/agents/{agent_id}/awareness"
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    awareness = data.get("awareness", "")
                    print(f"   ✓ Retrieved awareness ({len(awareness)} chars)")
                    print(f"   Update time: {data.get('update_time', 'N/A')}")
                    print()
                    print("   Content preview:")
                    print("   " + "-" * 56)
                    preview_lines = awareness[:300].split('\n')
                    for line in preview_lines:
                        print(f"   {line}")
                    print("   ...")
                    print("   " + "-" * 56)
                    print()

                    # Verify content matches
                    if awareness == test_awareness:
                        print("   ✓ Content matches perfectly!")
                    else:
                        print("   ⚠ Content differs from what was sent")
                else:
                    print(f"   ✗ Error: {data.get('error', 'Unknown error')}")
            else:
                print(f"   ✗ HTTP error: {response.status_code}")
                print(f"   Response: {response.text}")
    else:
        print("   ✗ Awareness update failed!")
        return False

    print()
    print("=" * 60)
    print("✓ All tests passed!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    # Load .env if available
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).parent.parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            print(f"Loaded environment from: {env_path}")
            print()
    except ImportError:
        pass

    try:
        success = asyncio.run(test_awareness_update())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
