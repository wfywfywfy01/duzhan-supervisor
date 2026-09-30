# -*- coding: utf-8 -*-
"""新人培训打卡进度。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class OnboardingProgress(SQLModel, table=True):
    """培训模块完成记录。"""

    __tablename__ = "onboarding_progress"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, max_length=64)
    day: int = Field(default=1)
    module_id: str = Field(index=True, max_length=64)
    score: int = Field(default=0)
    completed_at: datetime = Field(default_factory=datetime.utcnow)
