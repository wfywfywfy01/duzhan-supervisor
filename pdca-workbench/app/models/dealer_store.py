# -*- coding: utf-8 -*-
"""门店/经销商主数据（动态维护，替代硬编码下拉列表）。"""
from __future__ import annotations

from datetime import datetime
import re
from typing import Optional

from sqlmodel import Field, SQLModel


def is_demo_store(store_id: str, name: str = "") -> bool:
    """Only explicit test labels; real names containing 'demo' stay visible."""
    return any(
        re.match(r"^(?:qa|test|demo)(?:[-_\s]|测试|演示|$)|^(?:测试|演示)", str(value or "").strip(), re.IGNORECASE)
        for value in (store_id, name)
    )


class DealerStore(SQLModel, table=True):
    __tablename__ = "dealer_stores"

    id: Optional[int] = Field(default=None, primary_key=True)
    store_id: str = Field(unique=True, index=True, max_length=64)
    name: str = Field(max_length=256)
    region: str = Field(default="", max_length=64)      # 中东 / 欧洲 / 南亚 / 东南亚 / 中亚
    country: str = Field(default="", max_length=64)
    dealer_level: str = Field(default="L1", max_length=8)   # L1 / L2
    sales_owner: str = Field(default="", max_length=128, index=True)  # 稳定负责人 owner_key
    team_key: str = Field(default="overseas", max_length=64, index=True)
    knowledge_dealer_id: str = Field(
        default="", max_length=36, index=True,
        description="经销商资料库 dealer UUID；空值表示尚未映射",
    )
    is_active: bool = Field(default=True)
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
