# -*- coding: utf-8 -*-
"""操作审计日志。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, max_length=64)
    action: str = Field(index=True, max_length=64)    # login / change_password / submit_five_kit / ...
    resource: str = Field(default="", max_length=128)  # 被操作的资源（如 dealer_id + date）
    detail: str = Field(default="", max_length=2048)   # JSON 摘要
    ip: str = Field(default="", max_length=64)
    created_at: datetime = Field(default_factory=datetime.utcnow)
