"""
@file_name: test_dashboard_stale_instances.py
@date: 2026-04-13
@description: G3 — verify fetch_instances buckets into active/stale with longrun whitelist.
"""
import os
import pytest
from datetime import datetime, timedelta, timezone
from backend.routes._dashboard_helpers import fetch_instances, LONGRUN_MODULE_WHITELIST


@pytest.mark.asyncio
async def test_fresh_instance_is_active(seed_instance):
    old = datetime.now(timezone.utc)  # just seeded = fresh
    await seed_instance(agent_id="agent_stale_a", module_class="AwarenessModule",
                        status="in_progress", updated_at=old)
    result = await fetch_instances(["agent_stale_a"])
    assert len(result["agent_stale_a"]["active"]) == 1
    assert result["agent_stale_a"]["stale"] == []


@pytest.mark.asyncio
async def test_stale_instance_goes_to_stale_bucket(seed_instance):
    old = datetime.now(timezone.utc) - timedelta(hours=1)  # well past 600s default
    await seed_instance(agent_id="agent_stale_b", module_class="AwarenessModule",
                        status="in_progress", updated_at=old)
    result = await fetch_instances(["agent_stale_b"])
    assert result["agent_stale_b"]["active"] == []
    assert len(result["agent_stale_b"]["stale"]) == 1


@pytest.mark.asyncio
async def test_stale_longrun_module_stays_active(seed_instance):
    """SkillModule / GeminiRagModule are whitelisted — stale updated_at still counts as active."""
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    await seed_instance(agent_id="agent_stale_c", module_class="SkillModule",
                        status="in_progress", updated_at=old)
    result = await fetch_instances(["agent_stale_c"])
    assert len(result["agent_stale_c"]["active"]) == 1, "SkillModule must be whitelisted"
    assert result["agent_stale_c"]["stale"] == []


def test_longrun_whitelist_contains_expected_modules():
    assert "SkillModule" in LONGRUN_MODULE_WHITELIST
    assert "GeminiRagModule" in LONGRUN_MODULE_WHITELIST
