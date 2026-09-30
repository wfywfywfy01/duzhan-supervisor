# -*- coding: utf-8 -*-
"""Short-lived, single-use login tickets for the acquisition workspace."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class AcquisitionLoginTicket(SQLModel, table=True):
    __tablename__ = "acquisition_login_tickets"

    id: Optional[int] = Field(default=None, primary_key=True)
    token_digest: str = Field(index=True, unique=True, max_length=64)
    user_id: int = Field(index=True)
    username: str = Field(max_length=64)
    display_name: str = Field(default="", max_length=128)
    role: str = Field(max_length=32)
    data_scope: str = Field(default="none", max_length=16)
    owner_key: str = Field(default="", max_length=128)
    team_key: str = Field(default="", max_length=64)
    owner_keys_json: str = Field(default="[]")
    expires_at: datetime = Field(index=True)
    consumed_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
