# -*- coding: utf-8 -*-
"""物流单号自动查询状态缓存（UPS/FedEx/DHL 官网抓取结果）。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class TrackingAutoStatus(SQLModel, table=True):
    """按运单号缓存的自动查询结果，覆盖人工录入的 current_status。"""

    __tablename__ = "tracking_auto_status"

    id: Optional[int] = Field(default=None, primary_key=True)
    tracking_number: str = Field(index=True, unique=True, max_length=64)
    carrier: str = Field(default="", max_length=32)
    status_text: str = Field(default="", max_length=256)
    is_delivered: bool = Field(default=False)
    fetch_ok: bool = Field(default=False)
    error: str = Field(default="", max_length=256)
    fetched_at: datetime = Field(default_factory=datetime.utcnow, index=True)
