# -*- coding: utf-8 -*-
"""跨进程共享的安全状态（JWT 吊销、登录失败计数）。

进程内内存只作热缓存；数据库是唯一事实来源，多 worker / 多实例部署下
吊销与限速仍然一致。数据库不可用时自动降级回内存实现，保证单机可用。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class TokenRevocation(SQLModel, table=True):
    """已吊销 JWT 的 jti，expires_at 用于定期清理。"""

    __tablename__ = "token_revocations"

    jti: str = Field(primary_key=True, max_length=64)
    expires_at: float = Field(default=0.0)
    revoked_at: datetime = Field(default_factory=datetime.utcnow)


class LoginFailRecord(SQLModel, table=True):
    """登录失败记录：key 为 ``客户端IP:用户名``，failed_at 为 epoch 秒。"""

    __tablename__ = "login_fail_records"

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, max_length=256)
    failed_at: float = Field(default=0.0)
