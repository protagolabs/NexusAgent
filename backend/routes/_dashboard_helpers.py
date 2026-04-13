"""
@file_name: _dashboard_helpers.py
@author: NarraNexus
@date: 2026-04-13
@description: Pure helpers for GET /api/dashboard/agents-status.

Covers:
  - AgentRunState dataclass + build_action_line (7-kind, UTF-8 safe, XSS-sanitized)
  - sort_agents (Running group first, then Idle, both desc)
  - classify_kind (WorkingSource → Kind enum mapping)
  - bucket_count (exact int → '1-2'/'3-5'/... for public privacy)
  - to_response factory (dispatches Owned/Public; filters private-non-owned to None)
  - fetch_* async aggregators (events MAX, jobs, module_instances, enhanced signals)

Notes:
  - build_action_line avoids events.embedding_text for running agents (Step 4
    persistence; null during run). Uses instance_jobs.description / bus_messages
    content instead. See design TDR-4 + R11.
  - All DB fetch helpers go through AsyncDatabaseClient.fetch_all() which
    parametrizes to prevent SQLi.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.routes._dashboard_schema import (
    EnhancedSignals,
    OwnedAgentStatus,
    PublicAgentStatus,
    StatusCommon,
    StatusWithDetails,
)


# -------- action_line helpers ----------------------------------------------

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
_WHITESPACE_RUN_RE = re.compile(r"\s+")


@dataclass
class AgentRunState:
    kind: str
    job: dict | None = None             # {"title": str, "description": str}
    session_msg: str | None = None      # raw text for CHAT
    bus_msg: dict | None = None         # {"src": str, "dst": str, "content": str}
    a2a_source: str | None = None


def _clean(text: str) -> str:
    """Strip control chars, collapse whitespace runs, trim ends.

    Security: defense against XSS via control-char injection + layout break
    via raw newlines in dashboard card. React auto-escapes HTML at render.
    """
    if text is None:
        return ""
    text = _CONTROL_CHARS_RE.sub("", text)
    text = _WHITESPACE_RUN_RE.sub(" ", text).strip()
    return text


def _truncate_utf8(text: str, limit: int = 80) -> str:
    """Truncate to at most `limit` Unicode codepoints, appending ellipsis.

    Python str.len counts codepoints, so no risk of slicing inside a
    multi-byte UTF-8 sequence (that concern only applies to bytes).
    """
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"


def build_action_line(state: AgentRunState) -> str | None:
    """Build single-line action description for a running agent.

    Returns None for idle. Output is sanitized + truncated to 80 chars.
    Falls back to "Running (kind)" if the kind-specific source is missing.
    """
    if state.kind == "idle":
        return None

    if state.kind in ("JOB", "CALLBACK", "SKILL_STUDY", "MATRIX") and state.job:
        title = _clean(state.job.get("title") or "")
        desc = _clean(state.job.get("description") or "")
        line = f"{title}: {desc}" if desc else title
        return _truncate_utf8(line) if line else f"Running ({state.kind})"

    if state.kind == "CHAT" and state.session_msg:
        cleaned = _clean(state.session_msg)
        return _truncate_utf8(cleaned) if cleaned else f"Running ({state.kind})"

    if state.kind == "MESSAGE_BUS" and state.bus_msg:
        src = _clean(state.bus_msg.get("src") or "?")
        dst = _clean(state.bus_msg.get("dst") or "?")
        content = _clean(state.bus_msg.get("content") or "")
        return _truncate_utf8(f"\U0001F4E1 {src} \u2192 {dst}: {content}")

    if state.kind == "A2A" and state.a2a_source:
        return _truncate_utf8(f"\u2190 agent {_clean(state.a2a_source)}")

    return f"Running ({state.kind})"


# -------- sort + classify + bucket ----------------------------------------

def sort_agents(agents: list) -> list:
    """Running group (by started_at desc) first, Idle (by last_activity desc) second.

    Works for both pydantic response models and plain dicts (via getattr/dict access).
    None timestamps are treated as oldest (empty string sorts before any ISO8601).
    """

    def _status_kind(a) -> str:
        status = a.status if hasattr(a, "status") else a["status"]
        return getattr(status, "kind", None) if hasattr(status, "kind") else status["kind"]

    def _started(a) -> str:
        status = a.status if hasattr(a, "status") else a["status"]
        val = getattr(status, "started_at", None) if hasattr(status, "started_at") else status.get("started_at")
        return val or ""

    def _last(a) -> str:
        status = a.status if hasattr(a, "status") else a["status"]
        val = getattr(status, "last_activity_at", None) if hasattr(status, "last_activity_at") else status.get("last_activity_at")
        return val or ""

    running = [a for a in agents if _status_kind(a) != "idle"]
    idle = [a for a in agents if _status_kind(a) == "idle"]
    running.sort(key=_started, reverse=True)
    idle.sort(key=_last, reverse=True)
    return running + idle


_KIND_MAP = {
    "chat": "CHAT",
    "job": "JOB",
    "a2a": "A2A",
    "callback": "CALLBACK",
    "skill_study": "SKILL_STUDY",
    "matrix": "MATRIX",
    "message_bus": "MESSAGE_BUS",
}


def classify_kind(working_source: str | None) -> str:
    if not working_source:
        return "idle"
    return _KIND_MAP.get(str(working_source).lower(), "idle")


def bucket_count(n: int) -> str:
    if n <= 0:
        return "0"
    if n <= 2:
        return "1-2"
    if n <= 5:
        return "3-5"
    if n <= 10:
        return "6-10"
    return "10+"


# -------- permission dispatch ----------------------------------------------

def to_response(raw: dict, viewer_id: str):
    """Factory: build OwnedAgentStatus or PublicAgentStatus per visibility rules.

    Returns None for (private and not-owned) agents — caller filters out.
    Per-field sensitivity table is enforced by Pydantic `extra='forbid'` on
    PublicAgentStatus: even if this factory accidentally passes an owner-only
    field, validation rejects it.
    """
    owned = raw["created_by"] == viewer_id
    is_public = bool(raw.get("is_public", False))
    if not owned and not is_public:
        return None

    status_raw = raw["status"]
    if owned:
        return OwnedAgentStatus(
            agent_id=raw["agent_id"],
            name=raw["name"],
            description=raw.get("description"),
            is_public=is_public,
            status=StatusWithDetails(**status_raw),
            running_count=int(raw.get("running_count", 0)),
            action_line=raw.get("action_line"),
            sessions=raw.get("sessions", []),
            running_jobs=raw.get("running_jobs", []),
            pending_jobs=raw.get("pending_jobs", []),
            enhanced=EnhancedSignals(**raw["enhanced"]),
        )
    # Public non-owned: strip details + owner-only fields
    safe_status = StatusCommon(
        kind=status_raw["kind"],
        last_activity_at=status_raw.get("last_activity_at"),
        started_at=status_raw.get("started_at"),
    )
    return PublicAgentStatus(
        agent_id=raw["agent_id"],
        name=raw["name"],
        description=raw.get("description"),
        status=safe_status,
        running_count_bucket=bucket_count(int(raw.get("running_count", 0))),
    )


# -------- DB aggregators ----------------------------------------------------

async def fetch_last_activity(agent_ids: list[str]) -> dict[str, str | None]:
    """MAX(events.created_at) GROUP BY agent_id for the given ids."""
    if not agent_ids:
        return {}
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    placeholders = ",".join("%s" for _ in agent_ids)
    sql = (
        f"SELECT agent_id, MAX(created_at) AS last_at "
        f"FROM events WHERE agent_id IN ({placeholders}) GROUP BY agent_id"
    )
    rows = await db.execute(sql, tuple(agent_ids))
    result: dict[str, str | None] = {aid: None for aid in agent_ids}
    for r in rows:
        last_at = r["last_at"]
        # Normalize datetime objects (MySQL) vs ISO strings (SQLite) to string.
        # isoformat() emits "2026-04-13T07:27:23" style matching ISO round-trip.
        if hasattr(last_at, "isoformat"):
            result[r["agent_id"]] = last_at.isoformat()
        else:
            result[r["agent_id"]] = last_at
    return result


async def fetch_jobs(agent_ids: list[str]) -> dict[str, dict[str, list[dict]]]:
    """Partition instance_jobs into running vs pending per agent."""
    if not agent_ids:
        return {}
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    placeholders = ",".join("%s" for _ in agent_ids)
    sql = (
        f"SELECT job_id, agent_id, title, description, job_type, status, "
        f"next_run_time, started_at "
        f"FROM instance_jobs WHERE agent_id IN ({placeholders}) "
        f"AND status IN ('running','pending','active')"
    )
    rows = await db.execute(sql, tuple(agent_ids))
    out: dict[str, dict[str, list[dict]]] = {
        aid: {"running": [], "pending": []} for aid in agent_ids
    }
    for r in rows:
        bucket = "running" if r["status"] == "running" else "pending"
        out[r["agent_id"]][bucket].append(
            {
                "job_id": r["job_id"],
                "title": r["title"],
                "description": r.get("description"),
                "job_type": r["job_type"],
                "started_at": r.get("started_at"),
                "next_run_time": r.get("next_run_time"),
            }
        )
    return out


async def fetch_instances(agent_ids: list[str]) -> dict[str, list[dict]]:
    """module_instances currently in_progress per agent."""
    if not agent_ids:
        return {}
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    placeholders = ",".join("%s" for _ in agent_ids)
    sql = (
        f"SELECT instance_id, agent_id, module_class, description "
        f"FROM module_instances WHERE agent_id IN ({placeholders}) "
        f"AND status='in_progress'"
    )
    rows = await db.execute(sql, tuple(agent_ids))
    out: dict[str, list[dict]] = {aid: [] for aid in agent_ids}
    for r in rows:
        out[r["agent_id"]].append(
            {
                "instance_id": r["instance_id"],
                "module_class": r["module_class"],
                "description": r.get("description"),
            }
        )
    return out


async def fetch_enhanced_signals(agent_ids: list[str]) -> dict[str, dict]:
    """Aggregate error count, narrative count etc. per agent.

    token_rate_1h: no per-event token column exists in current schema → None
    (frontend renders "N/A"). See requirements.md §4 + design R11.
    """
    if not agent_ids:
        return {}
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    placeholders = ",".join("%s" for _ in agent_ids)
    # errors: final_output matching error markers in last hour
    err_sql = (
        f"SELECT agent_id, COUNT(*) AS n FROM events "
        f"WHERE agent_id IN ({placeholders}) "
        f"AND created_at > datetime('now', '-1 hour') "
        f"AND (final_output LIKE '%ERROR%' OR final_output LIKE '%Error%') "
        f"GROUP BY agent_id"
    )
    try:
        err_rows = await db.fetch_all(err_sql, tuple(agent_ids))
        errs = {r["agent_id"]: r["n"] for r in err_rows}
    except Exception:
        errs = {}
    nar_sql = (
        f"SELECT agent_id, COUNT(*) AS n FROM narratives "
        f"WHERE agent_id IN ({placeholders}) AND status='active' "
        f"GROUP BY agent_id"
    )
    try:
        nar_rows = await db.fetch_all(nar_sql, tuple(agent_ids))
        nars = {r["agent_id"]: r["n"] for r in nar_rows}
    except Exception:
        nars = {}
    out: dict[str, dict] = {}
    for aid in agent_ids:
        out[aid] = {
            "recent_errors_1h": errs.get(aid, 0),
            "token_rate_1h": None,
            "active_narratives": nars.get(aid, 0),
            "unread_bus_messages": 0,
        }
    return out


async def build_run_state_for_agent(
    agent_id: str,
    kind: str,
    sessions: list[Any],
    running_jobs: list[dict],
    instances: list[dict],
) -> AgentRunState:
    """Populate AgentRunState from live data sources (TDR-4).

    - JOB-family kinds use the first running_jobs entry
    - CHAT reads the latest bus_messages content in the session channel
    - MESSAGE_BUS reads the latest bus_message + channel from sessions
    - A2A gets source name from sessions[0].user_display (best-effort)
    """
    if kind == "idle":
        return AgentRunState(kind=kind)

    job = None
    if running_jobs:
        job = {
            "title": running_jobs[0].get("title", ""),
            "description": running_jobs[0].get("description", ""),
        }

    session_msg = None
    bus_msg = None
    a2a_source = None

    if sessions:
        first = sessions[0]
        channel = getattr(first, "channel", None) or (
            first.get("channel") if isinstance(first, dict) else None
        )
        if kind == "CHAT" and channel:
            session_msg = await _latest_bus_content_for_channel(channel)
        elif kind == "MESSAGE_BUS" and channel:
            bus_msg = {
                "src": channel,
                "dst": "?",
                "content": (await _latest_bus_content_for_channel(channel)) or "",
            }
        elif kind == "A2A":
            a2a_source = getattr(first, "user_display", None) or (
                first.get("user_display") if isinstance(first, dict) else None
            )

    return AgentRunState(
        kind=kind, job=job, session_msg=session_msg,
        bus_msg=bus_msg, a2a_source=a2a_source,
    )


async def _latest_bus_content_for_channel(channel: str) -> str | None:
    """Fetch the most recent bus_messages.content for a channel (content preview)."""
    try:
        from xyz_agent_context.utils.db_factory import get_db_client
        db = await get_db_client()
        row = await db.execute(
            "SELECT content FROM bus_messages WHERE channel_id=%s "
            "ORDER BY created_at DESC LIMIT 1",
            (channel,),
        )
        return row[0]["content"] if row else None
    except Exception:
        return None
