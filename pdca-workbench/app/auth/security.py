# -*- coding: utf-8 -*-
"""密码哈希与 JWT 工具。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import secrets
import threading
import time

import bcrypt
from jose import JWTError, jwt
from starlette.requests import Request

from app.config import get_settings

KNOWLEDGE_REAUTH_COOKIE = "pdca_knowledge_reauth"


def request_access_token(request: Request) -> str | None:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return request.cookies.get("pdca_token")



_REVOKED_JTIS: dict[str, float] = {}
_REVOKED_LOCK = threading.Lock()
# 内存表只作热缓存；数据库 token_revocations 才是跨进程共享的事实来源。
# 数据库不可用时自动降级回纯内存实现（单机行为不变）。


def _revocation_db():
    """懒加载数据库会话工具，避免模块导入环与无数据库场景报错。"""
    try:
        from sqlmodel import Session

        from app.auth.security_state import TokenRevocation
        from app.database import get_engine
    except Exception:  # pragma: no cover - 环境缺依赖时降级
        return None, None, None
    return Session, TokenRevocation, get_engine


def _db_revoke_jti(jti: str, expires_at: float) -> None:
    """把 jti 写入共享吊销表（尽力而为，失败不影响内存吊销）。"""
    try:
        Session, TokenRevocation, get_engine = _revocation_db()
        if not Session:
            return
        with Session(get_engine()) as session:
            existing = session.get(TokenRevocation, jti)
            if existing is None:
                session.add(TokenRevocation(jti=jti, expires_at=expires_at))
            else:
                existing.expires_at = expires_at
            session.commit()
    except Exception:
        pass


def _db_is_revoked(jti: str) -> bool:
    """查询共享吊销表；异常时按未吊销处理（内存缓存兜底）。"""
    try:
        Session, TokenRevocation, get_engine = _revocation_db()
        if not Session:
            return False
        with Session(get_engine()) as session:
            record = session.get(TokenRevocation, jti)
            if record is None:
                return False
            if record.expires_at and record.expires_at <= time.time():
                session.delete(record)
                session.commit()
                return False
            return True
    except Exception:
        return False


def _prune_db_revocations() -> None:
    """清理已过期的吊销记录（尽力而为，避免表无限增长）。"""
    try:
        from sqlalchemy import delete
        from sqlmodel import Session

        from app.auth.security_state import TokenRevocation
        from app.database import get_engine

        with Session(get_engine()) as session:
            session.exec(
                delete(TokenRevocation).where(TokenRevocation.expires_at <= time.time())
            )
            session.commit()
    except Exception:
        pass





def hash_password(password: str) -> str:
    """哈希明文密码。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码。"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """签发 JWT。"""
    settings = get_settings()
    payload = data.copy()
    expire = datetime.utcnow() + (
        expires_delta
        or timedelta(minutes=settings.access_token_expire_minutes)
    )
    now = datetime.utcnow()
    payload["iat"] = now
    payload["exp"] = expire
    payload["jti"] = secrets.token_hex(16)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict[str, Any] | None:
    """解码 JWT，失败返回 None。"""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Step-up and other purpose-specific JWTs are never login credentials."""
    payload = decode_token(token)
    return payload if payload and "purpose" not in payload else None


def revoke_token(token: str) -> bool:
    """将 token 的 jti 加入吊销集合（内存 + 共享数据库）。"""
    payload = decode_token(token)
    jti = payload.get("jti") if payload else None
    if not isinstance(jti, str) or not jti:
        return False
    expires_at = payload.get("exp")
    try:
        expires_at = float(expires_at)
    except (TypeError, ValueError):
        expires_at = time.time() + 86400
    with _REVOKED_LOCK:
        _REVOKED_JTIS[jti] = expires_at
    _db_revoke_jti(jti, expires_at)
    # 1/64 概率顺带清理过期的共享吊销记录，避免表无限增长
    if secrets.randbelow(64) == 0:
        _prune_db_revocations()
    return True


def is_token_revoked(payload: dict[str, Any]) -> bool:
    """检查 jti 是否已被吊销：先查内存缓存，再查共享吊销表。"""
    jti = payload.get("jti")
    if not isinstance(jti, str):
        return False
    now = time.time()
    with _REVOKED_LOCK:
        for expired_jti in [
            key for key, exp in _REVOKED_JTIS.items() if exp <= now
        ]:
            _REVOKED_JTIS.pop(expired_jti, None)
        if jti in _REVOKED_JTIS:
            return True
    if _db_is_revoked(jti):
        # 其他 worker 吊销的 token：写入本进程缓存，后续请求不再查库
        with _REVOKED_LOCK:
            _REVOKED_JTIS[jti] = time.time() + 3600
        return True
    return False

