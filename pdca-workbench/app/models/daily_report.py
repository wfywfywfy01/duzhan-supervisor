# -*- coding: utf-8 -*-
"""日报与检查报告表。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class DailyReport(SQLModel, table=True):
    """每日 PDCA 产出（替代 outputs/*.md）。"""

    __tablename__ = "daily_reports"

    id: Optional[int] = Field(default=None, primary_key=True)
    report_date: str = Field(index=True, max_length=10)
    report_type: str = Field(index=True, max_length=64)
    title: str = Field(default="", max_length=256)
    content: str = Field(default="")
    file_path: str = Field(default="", max_length=512)
    created_at: datetime = Field(default_factory=datetime.utcnow)
