# -*- coding: utf-8 -*-
"""Stable user-to-dealer assignments used for online authorization."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class DealerAssignment(SQLModel, table=True):
    __tablename__ = "dealer_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "store_id", name="uq_dealer_assignment_user_store"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    store_id: str = Field(foreign_key="dealer_stores.store_id", index=True, max_length=64)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
