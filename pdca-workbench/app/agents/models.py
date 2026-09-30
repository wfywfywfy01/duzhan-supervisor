# -*- coding: utf-8 -*-
"""多智能体运行时数据模型：agent_runs / agent_events / agent_outbox / meeting_asr_artifacts。

与 migrations/versions/011_multi_agent_runtime.py 保持一致；字段变更必须同时
更新迁移文件与 app/database.py 的轻量补丁（历史库由 init_db 补列）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """统一使用 UTC 感知时间戳。"""
    return datetime.now(timezone.utc)


class AgentRun(SQLModel, table=True):
    """一条用户任务、定时档位或部门汇总对应一个根运行。"""

    __tablename__ = "agent_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_key: str = Field(index=True, unique=True, max_length=128)
    thread_id: str = Field(default="", index=True, max_length=128)
    run_type: str = Field(default="user_task", index=True, max_length=32)
    source_type: str = Field(default="", max_length=32)
    source_ref: str = Field(default="", max_length=256)
    requested_by: str = Field(default="", max_length=128)
    input_text: str = Field(default="")
    input_json: str = Field(default="")
    status: str = Field(default="queued", index=True, max_length=32)
    current_node: str = Field(default="", max_length=64)
    result_text: str = Field(default="")
    result_json: str = Field(default="")
    error_code: str = Field(default="", max_length=64)
    error_detail: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)
    updated_at: datetime = Field(default_factory=utcnow)


class AgentEvent(SQLModel, table=True):
    """追加写审计与组件交接事件；event_key 唯一保证幂等。"""

    __tablename__ = "agent_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    event_key: str = Field(index=True, unique=True, max_length=160)
    run_id: Optional[int] = Field(default=None, index=True)
    task_id: Optional[int] = Field(default=None, index=True)
    event_type: str = Field(index=True, max_length=64)
    producer: str = Field(default="", max_length=64)
    group_channel_id: str = Field(default="", max_length=64)
    payload_json: str = Field(default="")
    occurred_at: datetime = Field(default_factory=utcnow)
    created_at: datetime = Field(default_factory=utcnow)


class AgentOutbox(SQLModel, table=True):
    """所有外发消息的唯一出口：先落库，按审批策略批准后发送。"""

    __tablename__ = "agent_outbox"

    id: Optional[int] = Field(default=None, primary_key=True)
    idempotency_key: str = Field(index=True, unique=True, max_length=160)
    run_id: Optional[int] = Field(default=None, index=True)
    task_id: Optional[int] = Field(default=None, index=True)
    channel_id: str = Field(default="", index=True, max_length=64)
    message_kind: str = Field(default="", max_length=32)
    body: str = Field(default="")
    evidence_json: str = Field(default="")
    approval_policy: str = Field(default="manual_required", max_length=32)
    approval_status: str = Field(default="draft", index=True, max_length=32)
    approved_by: str = Field(default="", max_length=128)
    approved_at: Optional[datetime] = Field(default=None)
    send_status: str = Field(default="pending", max_length=32)
    send_attempts: int = Field(default=0)
    last_error: str = Field(default="")
    sent_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AgentDraft(SQLModel, table=True):
    """群 Agent 每档草稿（影子与正式都落库）：后台可审、可追溯。

    draft_key = channel_id:day:slot 唯一，同档重跑覆盖（upsert）。
    """

    __tablename__ = "agent_drafts"

    id: Optional[int] = Field(default=None, primary_key=True)
    draft_key: str = Field(index=True, unique=True, max_length=160)
    run_id: Optional[int] = Field(default=None, index=True)
    channel_id: str = Field(default="", index=True, max_length=64)
    group_name: str = Field(default="", max_length=128)
    group_type: str = Field(default="", max_length=32)
    day: str = Field(default="", index=True, max_length=10)
    slot: str = Field(default="", max_length=8)
    body: str = Field(default="")
    approval_policy: str = Field(default="manual_required", max_length=32)
    shadow: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MeetingAsrArtifact(SQLModel, table=True):
    """会议语音转写产物；音频与客户原件永不落库、永不进 Git。"""

    __tablename__ = "meeting_asr_artifacts"

    id: Optional[int] = Field(default=None, primary_key=True)
    artifact_key: str = Field(index=True, unique=True, max_length=160)
    meeting_external_id: str = Field(default="", index=True, max_length=64)
    meeting_date: str = Field(default="", index=True, max_length=10)
    provider: str = Field(default="", max_length=32)
    provider_task_id: str = Field(default="", max_length=128)
    audio_source_ref: str = Field(default="", max_length=512)
    audio_sha256: str = Field(default="", max_length=64)
    duration_ms: Optional[int] = Field(default=None)
    language: str = Field(default="", max_length=32)
    status: str = Field(default="queued", max_length=32)
    transcript_text: str = Field(default="")
    segments_json: str = Field(default="")
    confidence: Optional[float] = Field(default=None)
    review_status: str = Field(default="unreviewed", max_length=32)
    reviewed_by: str = Field(default="", max_length=128)
    reviewed_at: Optional[datetime] = Field(default=None)
    error_code: str = Field(default="", max_length=64)
    error_detail: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
