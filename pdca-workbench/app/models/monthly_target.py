# -*- coding: utf-8 -*-
"""月度目标（按门店或全局）。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class MonthlyTarget(SQLModel, table=True):
    __tablename__ = "monthly_targets"

    id: Optional[int] = Field(default=None, primary_key=True)
    month: str = Field(index=True, max_length=7)          # YYYY-MM
    dealer_id: str = Field(index=True, max_length=64)     # "" = 全局目标
    sell_out_target_yuan: float = Field(default=0.0)       # 成交金额目标（元）
    visit_target: int = Field(default=0)                   # 进店组数目标
    deal_target: int = Field(default=0)                    # 成交组数目标
    add_rate_target: float = Field(default=0.35)           # 留资率目标
    created_by: str = Field(default="", max_length=64)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
