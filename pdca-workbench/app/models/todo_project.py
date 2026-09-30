# -*- coding: utf-8 -*-
"""待办项目（事项）：会议待办按项目收敛后的跟踪实体。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

PROJECT_STATUSES = ("新建", "跟进中", "阻塞", "待验收", "已闭环")
PROJECT_KINDS = ("keyword", "meeting", "manual")


class TodoProject(SQLModel, table=True):
    """项目（事项）——多条会议待办的收敛载体。

    kind ∈ {keyword, meeting, manual}：关键词业务项目 / 会议主题自动收敛 /
    管理员手工创建；executors 为 JSON 数组字符串，如 ["赵六","李四"]；
    coordinator 为协调负责人（默认海外经销商主管），v1 仅存不催。
    """

    __tablename__ = "todo_projects"

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(unique=True, max_length=64)
    name: str = Field(max_length=256)
    kind: str = Field(default="keyword", max_length=16)
    status: str = Field(default="新建", max_length=32)
    executors: str = Field(default="[]", max_length=512)
    coordinator: str = Field(default="", max_length=128)
    last_reminded_at: Optional[datetime] = Field(default=None)
    last_reminded_round: str = Field(default="", max_length=32)
    remind_count: int = Field(default=0)
    # IM 回复采集：最近一次回复原文与时间
    reply_text: str = Field(default="", max_length=1024)
    replied_at: Optional[datetime] = Field(default=None)
    # OKR 归属：项目对应的个人月度 OKR 条目标题（sync_todo_okr 收敛时写入）
    okr_title: str = Field(default="", max_length=256)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
