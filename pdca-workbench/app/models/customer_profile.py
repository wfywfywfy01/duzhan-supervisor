# -*- coding: utf-8 -*-
"""客户画像表（P3）：customers.csv 的数据库事实源。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class CustomerProfile(SQLModel, table=True):
    """经销商客户主数据 + SignalSeller 画像（ABCD/评分/跟进状态）。

    替代 teams/*/customers.csv 作为查询事实源；CSV 保留为导入素材与
    legacy 8787 的兼容输入。
    """

    __tablename__ = "customer_profiles"

    id: Optional[int] = Field(default=None, primary_key=True)
    team: str = Field(default="", index=True, max_length=64)
    dealer_name: str = Field(index=True, max_length=256)
    dealer_nickname: str = Field(default="", max_length=256)
    region: str = Field(default="", max_length=64)
    country: str = Field(default="", max_length=64)
    owner: str = Field(default="", index=True, max_length=128)
    priority: str = Field(default="", max_length=16)
    status: str = Field(default="active", max_length=32)
    # 空串 = 未分级，展示层按 priority 推导；导入器/更新接口负责落 A-D
    abcd_grade: str = Field(default="", max_length=4)
    value_score: Optional[int] = Field(default=None)
    intent_score: Optional[int] = Field(default=None)
    lead_source: str = Field(default="", max_length=64)
    followup_round: str = Field(default="1", max_length=16)
    referral_from: str = Field(default="", max_length=256)
    last_followup_date: str = Field(default="", max_length=10)
    next_action: str = Field(default="", max_length=512)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
