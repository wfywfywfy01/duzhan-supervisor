# -*- coding: utf-8 -*-
"""群知会/认领/台账的轻量状态存储（key-value，替代散落 outbox）。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class TodoGroupState(SQLModel, table=True):
    """todo_group_state：群知会游标、认领游标、台账文档/工作表 ID 等。"""

    __tablename__ = "todo_group_state"

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(unique=True, max_length=64)
    value: str = Field(default="", max_length=512)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
