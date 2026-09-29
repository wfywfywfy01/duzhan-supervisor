# -*- coding: utf-8 -*-
"""督战官子 Agent 配置表：每条记录 = 一个子 Agent 的积木 JSON。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    """统一用带时区的 UTC，避免 SQLite/Postgres 差异。"""
    return datetime.now(timezone.utc)


class DuzhanAgent(SQLModel, table=True):
    """一个督战官子 Agent：追哪个群、一天几档、什么规则，全部写进 blocks_json。"""

    __tablename__ = "duzhan_agents"

    id: Optional[int] = Field(default=None, primary_key=True, description="主键")
    name: str = Field(unique=True, index=True, max_length=128, description="子 Agent 名称（唯一）")
    enabled: bool = Field(default=False, index=True, description="是否启用（启用才注册调度）")
    timezone: str = Field(default="Asia/Shanghai", max_length=64, description="推送时区")
    blocks_json: str = Field(default='{"blocks": []}', description="积木配置 JSON")
    note: str = Field(default="", max_length=256, description="备注")
    created_at: datetime = Field(default_factory=_utcnow, description="创建时间")
    updated_at: datetime = Field(default_factory=_utcnow, description="更新时间")
