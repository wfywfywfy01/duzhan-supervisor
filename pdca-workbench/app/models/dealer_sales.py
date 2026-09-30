# -*- coding: utf-8 -*-
"""经销商业绩快照表。"""
from __future__ import annotations

from datetime import datetime
import math
from typing import Optional

from sqlmodel import Field, SQLModel


class DealerSales(SQLModel, table=True):
    """门店/经销商业绩（替代 data_raw JSON）。"""

    __tablename__ = "dealer_sales"

    id: Optional[int] = Field(default=None, primary_key=True)
    check_date: str = Field(index=True, max_length=10)
    dealer_name: str = Field(index=True, max_length=256)
    region: str = Field(default="", max_length=64)
    country: str = Field(default="", max_length=64)
    sell_in_wan: float = Field(default=0.0)
    sell_out_wan: float = Field(default=0.0)
    units: int = Field(default=0)
    phone_qty: int = Field(default=0)             # 手机台数（过滤商品大类=手机）
    activation_rate: float = Field(default=0.0)  # 累计激活率 %（activated/shipped）
    source_file: str = Field(default="", max_length=512)
    synced_at: datetime = Field(default_factory=datetime.utcnow)


def snapshot_amount_state(rows: list[DealerSales]) -> str:
    """Retain old raw snapshots but quarantine unverifiable zero amounts."""
    if not rows:
        return "missing"
    if any(row.sell_in_wan is None or not math.isfinite(row.sell_in_wan) for row in rows):
        return "suspect"
    legacy_sources = {"vertu-cli:sales-orders", "sync_from_vertu"}
    if (all(row.sell_in_wan == 0 for row in rows)
            and any(row.units != 0 for row in rows)
            and any(row.source_file in legacy_sources for row in rows)):
        return "suspect"
    return "available"
