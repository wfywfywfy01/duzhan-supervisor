# -*- coding: utf-8 -*-
"""定时外发任务的持久化运行凭证。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class ScheduledJobRun(SQLModel, table=True):
    __tablename__ = "scheduled_job_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_key: str = Field(unique=True, index=True, max_length=128)
    job_name: str = Field(index=True, max_length=64)
    bucket: str = Field(max_length=64)
    status: str = Field(default="sending", max_length=16)
    detail: str = Field(default="", max_length=512)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
