# -*- coding: utf-8 -*-
"""物流运单记录。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class LogisticsShipment(SQLModel, table=True):
    """物流运单（inputs + 核查结果镜像）。"""

    __tablename__ = "logistics_shipments"

    id: Optional[int] = Field(default=None, primary_key=True)
    record_date: str = Field(index=True, max_length=10)
    tracking_number: str = Field(index=True, max_length=64)
    carrier: str = Field(default="", max_length=32)
    customer: str = Field(default="", max_length=256)
    salesperson: str = Field(default="", index=True, max_length=128)
    ship_date: str = Field(default="", max_length=10)
    expected_status: str = Field(default="", max_length=64)
    current_status: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=512)
    tracking_url: str = Field(default="", max_length=512)
    judgement: str = Field(default="待核查", max_length=32)
    reason: str = Field(default="", max_length=512)
    progress_pct: int = Field(default=30)
    synced_at: datetime = Field(default_factory=datetime.utcnow)
