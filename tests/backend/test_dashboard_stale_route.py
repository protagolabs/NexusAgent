"""
@file_name: test_dashboard_stale_route.py
@date: 2026-04-13
@description: G3 route-level integration for stale instances:
  - agent A (true running job) → kind != idle
  - agent B (stale AwarenessModule) → kind == idle, health == healthy_idle, stale_instances non-empty
  - agent C (stale SkillModule whitelist) → kind != idle (exempt)
"""
import pytest
from datetime import datetime, timedelta, timezone


def test_true_running_job_agent_is_not_idle(local_client_stale, seed_instance_sync):
    """Agent A has a running job → kind != idle regardless of stale instances."""
    ctx = local_client_stale
    r = ctx["client"].get("/api/dashboard/agents-status")
    assert r.status_code == 200, r.text
    agents = r.json()["agents"]
    a = next((ag for ag in agents if ag["agent_id"] == ctx["agent_a"]), None)
    assert a is not None, "agent_a not found in response"
    assert a["status"]["kind"] != "idle", f"running job agent should not be idle, got {a['status']['kind']}"


def test_stale_awareness_agent_shows_idle_with_stale_instances(local_client_stale, seed_instance_sync):
    """Agent B has only a stale AwarenessModule instance → kind==idle, stale_instances non-empty."""
    ctx = local_client_stale
    r = ctx["client"].get("/api/dashboard/agents-status")
    assert r.status_code == 200, r.text
    agents = r.json()["agents"]
    b = next((ag for ag in agents if ag["agent_id"] == ctx["agent_b"]), None)
    assert b is not None, "agent_b not found in response"
    # Stale instances do not count as running — agent should be idle
    assert b["status"]["kind"] == "idle", f"expected idle, got {b['status']['kind']}"
    assert b["health"] == "healthy_idle", f"expected healthy_idle, got {b['health']}"
    assert len(b["stale_instances"]) >= 1, "stale_instances should surface the zombie AwarenessModule"


def test_stale_skill_module_agent_not_idle_due_to_whitelist(local_client_stale, seed_instance_sync):
    """Agent C has a stale SkillModule instance (whitelist) → still counts as active → not idle."""
    ctx = local_client_stale
    r = ctx["client"].get("/api/dashboard/agents-status")
    assert r.status_code == 200, r.text
    agents = r.json()["agents"]
    c = next((ag for ag in agents if ag["agent_id"] == ctx["agent_c"]), None)
    assert c is not None, "agent_c not found in response"
    # SkillModule is whitelisted → treated as active → kind != idle
    assert c["status"]["kind"] != "idle", f"whitelisted SkillModule should make agent non-idle, got {c['status']['kind']}"
    assert c["stale_instances"] == [], f"SkillModule is whitelisted so should NOT appear in stale_instances"
