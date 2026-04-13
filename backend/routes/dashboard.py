"""
@file_name: dashboard.py
@author: NarraNexus
@date: 2026-04-13
@description: Agent Dashboard v2 route.

GET /api/dashboard/agents-status — returns per-viewer aggregated view of all
visible agents (owned + public). Enforces permission boundary via Pydantic
discriminated union (owner-only fields cannot appear on public variant) and
2 req/s per-viewer sliding-window rate limit.

Cloud mode: viewer_id from request.state.user_id (JWT middleware populated).
Local mode: viewer_id from backend.auth.get_local_user_id() — NEVER from
`?user_id=` query param (that would be an impersonation vector; see TDR-12).

See design doc rev-2 for the full flow + threat model.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from backend.auth import _is_cloud_mode, get_local_user_id
from backend.routes._dashboard_helpers import (
    bucket_count,
    build_action_line,
    build_run_state_for_agent,
    classify_kind,
    fetch_enhanced_signals,
    fetch_instances,
    fetch_jobs,
    fetch_last_activity,
    sort_agents,
    to_response,
)
from backend.routes._dashboard_schema import DashboardResponse
from backend.routes._rate_limiter import SlidingWindowRateLimiter
from backend.state.active_sessions import get_session_registry

router = APIRouter()

# Per-viewer rate limit: dashboard legitimate rate is <1 req/s (3s polling);
# 2 req/s gives headroom for manual refresh while blocking 100-tab DoS.
_rate_limiter = SlidingWindowRateLimiter(limit=2, window_sec=1.0)


@router.get("/agents-status", response_model=DashboardResponse)
async def agents_status(request: Request, response: Response):
    # 1. Reject ?user_id= query param (TDR-12: impersonation vector)
    if "user_id" in request.query_params:
        raise HTTPException(
            status_code=400,
            detail="user_id query param not accepted; viewer identified by session",
        )

    # 2. Identify viewer
    if _is_cloud_mode():
        viewer_id = getattr(request.state, "user_id", None)
        if not viewer_id:
            raise HTTPException(status_code=401, detail="Authentication required")
    else:
        viewer_id = await get_local_user_id()

    # 3. Rate limit
    if not _rate_limiter.allow(viewer_id):
        raise HTTPException(
            status_code=429,
            detail="Too Many Requests",
            headers={"Retry-After": "1"},
        )

    # 4. Fetch visible agents (owned OR public)
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    agent_rows = await db.execute(
        "SELECT agent_id, agent_name, agent_description, created_by, is_public "
        "FROM agents WHERE created_by=%s OR is_public=1",
        (viewer_id,),
    )
    agent_ids = [r["agent_id"] for r in agent_rows]

    # 5. Parallel aggregation (fail-fast — no partial degradation)
    if agent_ids:
        last_act, jobs_map, inst_map, enh_map = await asyncio.gather(
            fetch_last_activity(agent_ids),
            fetch_jobs(agent_ids),
            fetch_instances(agent_ids),
            fetch_enhanced_signals(agent_ids),
            return_exceptions=False,
        )
        sessions_map = await get_session_registry().snapshot(agent_ids)
    else:
        last_act, jobs_map, inst_map, enh_map, sessions_map = {}, {}, {}, {}, {}

    # 6. Build response per agent
    statuses: list[Any] = []
    for r in agent_rows:
        aid = r["agent_id"]
        sessions = sessions_map.get(aid, [])
        running_jobs_raw = jobs_map.get(aid, {"running": [], "pending": []})["running"]
        pending_jobs_raw = jobs_map.get(aid, {"running": [], "pending": []})["pending"]
        instances = inst_map.get(aid, [])

        running_count = len(sessions) + len(running_jobs_raw) + len(instances)
        kind = _derive_kind(sessions, running_jobs_raw, instances)
        started_at = (
            _earliest_started_at(sessions, running_jobs_raw)
            if kind != "idle"
            else None
        )

        run_state = await build_run_state_for_agent(
            agent_id=aid, kind=kind,
            sessions=sessions, running_jobs=running_jobs_raw, instances=instances,
        )
        action_line = build_action_line(run_state)

        raw = {
            "agent_id": aid,
            "name": r["agent_name"],
            "description": r.get("agent_description"),
            "created_by": r["created_by"],
            "is_public": bool(r["is_public"]),
            "status": {
                "kind": kind,
                "last_activity_at": last_act.get(aid),
                "started_at": started_at,
            },
            "running_count": running_count,
            "action_line": action_line,
            "sessions": [
                {
                    "session_id": s.session_id,
                    "user_display": s.user_display,
                    "channel": s.channel,
                    "started_at": s.started_at,
                }
                for s in sessions
            ],
            "running_jobs": [
                {
                    "job_id": j["job_id"],
                    "title": j["title"],
                    "job_type": j["job_type"],
                    "started_at": j.get("started_at"),
                }
                for j in running_jobs_raw
            ],
            "pending_jobs": [
                {
                    "job_id": j["job_id"],
                    "title": j["title"],
                    "job_type": j["job_type"],
                    "next_run_time": j.get("next_run_time"),
                }
                for j in pending_jobs_raw
            ],
            "enhanced": enh_map.get(aid)
            or {
                "recent_errors_1h": 0,
                "token_rate_1h": None,
                "active_narratives": 0,
                "unread_bus_messages": 0,
            },
        }
        resp = to_response(raw, viewer_id=viewer_id)
        if resp is not None:
            statuses.append(resp)

    statuses = sort_agents(statuses)
    return {"success": True, "agents": statuses}


def _derive_kind(sessions, running_jobs, instances) -> str:
    """Heuristic: prefer running_jobs > sessions > instances > idle.

    For sessions we distinguish MESSAGE_BUS (non-web channels) from CHAT (web).
    """
    if running_jobs:
        return "JOB"
    if sessions:
        ch = (sessions[0].channel or "").lower()
        if ch.startswith(("lark", "slack", "matrix", "message_bus", "bus")):
            return "MESSAGE_BUS"
        return "CHAT"
    if instances:
        return "CALLBACK"
    return "idle"


def _earliest_started_at(sessions, running_jobs) -> str | None:
    """TDR-11: started_at = min(active session/job start) across concurrent work."""
    candidates = [s.started_at for s in sessions if getattr(s, "started_at", None)]
    candidates.extend(j["started_at"] for j in running_jobs if j.get("started_at"))
    if not candidates:
        return None
    return min(candidates)
