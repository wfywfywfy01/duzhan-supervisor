"""Persistent Omega records. All timestamps are aware UTC."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class OmegaCase(SQLModel, table=True):
    __tablename__ = "omega_cases"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    team_key: str = Field(index=True, max_length=64)
    owner_id: int
    title: str = Field(max_length=200)
    dealer_id: str = Field(default="", max_length=36)
    draft_json: str = Field(default="{}")
    revision: int = Field(default=1)
    current_version: int = Field(default=0)
    confirmed_revision: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class OmegaCaseVersion(SQLModel, table=True):
    __tablename__ = "omega_case_versions"
    __table_args__ = (UniqueConstraint("case_id", "version"),)

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    case_id: str = Field(foreign_key="omega_cases.id", index=True, max_length=36)
    version: int
    snapshot_json: str
    content_hash: str = Field(max_length=64)
    confirmed_by: int
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class OmegaSession(SQLModel, table=True):
    __tablename__ = "omega_sessions"
    __table_args__ = (UniqueConstraint("source_import_hash"),)

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    case_id: str = Field(foreign_key="omega_cases.id", index=True, max_length=36)
    case_version_id: str = Field(foreign_key="omega_case_versions.id", max_length=36)
    team_key: str = Field(index=True, max_length=64)
    owner_id: int
    assignment_id: str | None = Field(default=None, foreign_key="omega_assignments.id", index=True, max_length=36)
    mode: str = Field(default="rehearsal", max_length=20)
    source_meeting_id: str = Field(default="", max_length=120)
    source_import_hash: str | None = Field(default=None, max_length=64)
    source_access_keys_json: str = Field(default="[]")
    goal_timing: str = Field(default="pre", max_length=12)
    status: str = Field(default="active", max_length=16)
    revision: int = Field(default=1)
    transcript_hash: str = Field(default="", max_length=64)
    provider_dialog_id: str = Field(default="", max_length=120)
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    ended_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class OmegaSegment(SQLModel, table=True):
    __tablename__ = "omega_segments"
    __table_args__ = (UniqueConstraint("session_id", "seq"),)

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    session_id: str = Field(foreign_key="omega_sessions.id", index=True, max_length=36)
    seq: int
    speaker: str = Field(max_length=20)
    text: str
    source: str = Field(default="text", max_length=16)
    source_speaker: str = Field(default="", max_length=120)
    asr_original: str = Field(default="")
    request_key: str = Field(default="", max_length=120)
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class OmegaJob(SQLModel, table=True):
    __tablename__ = "omega_jobs"
    __table_args__ = (
        UniqueConstraint("session_id", "kind", "request_key"),
        Index(
            "uq_omega_one_pending_turn", "session_id", unique=True,
            postgresql_where=text("kind = 'turn' AND status IN ('queued', 'running')"),
            sqlite_where=text("kind = 'turn' AND status IN ('queued', 'running')"),
        ),
        Index(
            "uq_omega_active_report_input", "session_id", "input_hash", unique=True,
            postgresql_where=text("kind = 'report' AND status IN ('queued', 'running', 'succeeded')"),
            sqlite_where=text("kind = 'report' AND status IN ('queued', 'running', 'succeeded')"),
        ),
        Index(
            "uq_omega_one_realtime_stream", "session_id", unique=True,
            postgresql_where=text("kind = 'realtime' AND status = 'running'"),
            sqlite_where=text("kind = 'realtime' AND status = 'running'"),
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    session_id: str = Field(foreign_key="omega_sessions.id", index=True, max_length=36)
    kind: str = Field(max_length=20)
    request_key: str = Field(max_length=120)
    request_hash: str = Field(max_length=64)
    status: str = Field(default="queued", index=True, max_length=16)
    priority: int = Field(default=0)
    attempts: int = Field(default=0)
    available_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    lease_token: str = Field(default="", max_length=36)
    lease_until: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    input_hash: str = Field(default="", max_length=64)
    session_revision: int = Field(default=1)
    payload_json: str = Field(default="{}")
    result_id: str = Field(default="", max_length=36)
    error: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class OmegaReport(SQLModel, table=True):
    __tablename__ = "omega_reports"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    session_id: str = Field(foreign_key="omega_sessions.id", index=True, max_length=36)
    input_hash: str = Field(max_length=64)
    content_json: str
    model: str = Field(default="", max_length=128)
    prompt_version: str = Field(default="coach-v1", max_length=40)
    rubric_version: str = Field(default="rubric-v1", max_length=40)
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class OmegaReview(SQLModel, table=True):
    __tablename__ = "omega_reviews"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    report_id: str = Field(foreign_key="omega_reports.id", index=True, max_length=36)
    reviewer_id: int
    content_json: str
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class OmegaAssignment(SQLModel, table=True):
    __tablename__ = "omega_assignments"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    case_id: str = Field(foreign_key="omega_cases.id", index=True, max_length=36)
    case_version_id: str = Field(foreign_key="omega_case_versions.id", max_length=36)
    team_key: str = Field(index=True, max_length=64)
    assignee_id: int = Field(index=True)
    assigned_by: int
    source_report_id: str | None = Field(default=None, foreign_key="omega_reports.id", max_length=36)
    target_dimension: str = Field(max_length=32)
    pass_percent: int = Field(default=70)
    instructions: str = Field(default="", max_length=1000)
    due_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class OmegaWorkerHeartbeat(SQLModel, table=True):
    __tablename__ = "omega_worker_heartbeats"

    worker_id: str = Field(primary_key=True, max_length=36)
    started_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    last_seen: datetime = Field(default_factory=utcnow, index=True, sa_type=DateTime(timezone=True))
