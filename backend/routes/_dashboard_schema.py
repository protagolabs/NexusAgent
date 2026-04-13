"""
@file_name: _dashboard_schema.py
@author: NarraNexus
@date: 2026-04-13
@description: Pydantic response types for GET /api/dashboard/agents-status.

Discriminated union via `owned_by_viewer` (Literal[True] / Literal[False])
ensures public-variant responses cannot accidentally include owner-only
fields — the type system enforces the permission boundary, not ad-hoc
masking. See design doc TDR-5 + security critic C-2.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


Kind = Literal[
    "idle", "CHAT", "JOB", "MESSAGE_BUS", "A2A", "CALLBACK", "SKILL_STUDY", "MATRIX"
]

CountBucket = Literal["0", "1-2", "3-5", "6-10", "10+"]


class MessageBusDetails(BaseModel):
    src_channel: str | None = None
    dst_channel: str | None = None


class StatusCommon(BaseModel):
    """Status shape visible to non-owners — no MessageBusDetails."""

    kind: Kind
    last_activity_at: str | None = None
    started_at: str | None = None  # None when kind == 'idle'


class StatusWithDetails(StatusCommon):
    """Owner-visible status — may include MESSAGE_BUS src/dst channel."""

    details: MessageBusDetails | None = None


class SessionInfoResp(BaseModel):
    session_id: str
    user_display: str
    channel: str
    started_at: str


class RunningJob(BaseModel):
    job_id: str
    title: str
    job_type: str
    started_at: str | None = None


class PendingJob(BaseModel):
    job_id: str
    title: str
    job_type: str
    next_run_time: str | None = None


class EnhancedSignals(BaseModel):
    recent_errors_1h: int = 0
    token_rate_1h: int | None = None  # None when source unavailable (frontend shows N/A)
    active_narratives: int = 0
    unread_bus_messages: int = 0


class PublicAgentStatus(BaseModel):
    """Shape returned for public agents where viewer != owner.

    extra='forbid' ensures that even if the factory accidentally tries to
    pass an owner-only field, Pydantic rejects it at construction time.
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    name: str
    description: str | None = None
    is_public: Literal[True] = True
    owned_by_viewer: Literal[False] = False
    status: StatusCommon  # no details field
    running_count_bucket: CountBucket


class OwnedAgentStatus(BaseModel):
    """Full shape returned for agents owned by the viewer."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    name: str
    description: str | None = None
    is_public: bool
    owned_by_viewer: Literal[True] = True
    status: StatusWithDetails
    running_count: int
    action_line: str | None = None  # None → frontend renders "—"
    sessions: list[SessionInfoResp] = Field(default_factory=list)
    running_jobs: list[RunningJob] = Field(default_factory=list)
    pending_jobs: list[PendingJob] = Field(default_factory=list)
    enhanced: EnhancedSignals


# FastAPI response_model + pydantic v2 discriminated union
AgentStatus = Annotated[
    Union[OwnedAgentStatus, PublicAgentStatus],
    Field(discriminator="owned_by_viewer"),
]


class DashboardResponse(BaseModel):
    success: bool = True
    error: str | None = None
    agents: list[AgentStatus] = Field(default_factory=list)
