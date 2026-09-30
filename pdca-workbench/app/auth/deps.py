# -*- coding: utf-8 -*-
"""认证依赖注入与角色校验。"""
from __future__ import annotations

import asyncio
from typing import Annotated, Callable

from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from app.auth.models import ROLE_LEVELS, User
from app.auth.security import decode_access_token, is_token_revoked
from app.auth.vps_identity import (
    ensure_vps_user,
    fetch_vps_me_payload,
    identity_from_headers,
)
from app.config import get_settings
from app.database import get_session

bearer_scheme = HTTPBearer(auto_error=False)


def _role_level(role: str) -> int:
    return ROLE_LEVELS.get(role, -1)


async def _user_from_jwt(
    session: Session,
    token: str | None,
) -> User | None:
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload or "sub" not in payload or is_token_revoked(payload):
        return None
    username = payload["sub"]
    user = session.exec(select(User).where(User.username == username)).first()
    if not user or not user.is_active:
        return None
    token_pwd_v = payload.get("pwd_v", 0)
    user_pwd_v = getattr(user, "pwd_version", 0) or 0
    if token_pwd_v != user_pwd_v:
        return None
    return user


async def _user_from_proxy_headers(session: Session, request: Request) -> User | None:
    """反向代理注入 Header → 本地用户。"""
    settings = get_settings()
    if not settings.trust_proxy_headers:
        return None
    client_host = request.client.host if request.client else None
    if not settings.trusted_proxy_ips or client_host not in settings.trusted_proxy_ips:
        return None
    headers = {k.lower(): v for k, v in request.headers.items()}
    identity = identity_from_headers(headers)
    if not identity:
        return None
    return await asyncio.to_thread(ensure_vps_user, session, identity)


_LOCALHOST_ADDRS = {"127.0.0.1", "::1"}


async def _user_from_vps(session: Session, request: Request) -> User | None:
    """通过 vertu-cli hr +me 解析当前 Vertu 登录用户（服务端会话）。

    这是"本机免登录"的便利兜底，本质是把跑这个进程的机器上的 vertu 会话
    当成请求方身份，和实际发请求的人没有任何绑定关系。只允许来自
    127.0.0.1 的请求走这条路径，否则局域网内任何能连上这个端口的人
    都能白嫖服务器本机的 VPS 登录身份（曾经确认过：不带任何 cookie/token
    直接 curl /api/auth/me 也能拿到 manager 身份）。
    """
    client_host = request.client.host if request.client else None
    if client_host not in _LOCALHOST_ADDRS:
        return None
    vps = await asyncio.to_thread(fetch_vps_me_payload)
    if not vps:
        return None
    return await asyncio.to_thread(ensure_vps_user, session, vps)


# 强制改密期间仍需放行的路径（改密本身、查身份、登出、判断认证模式）；
# 其余所有 API/页面在 must_change_password=True 时一律 403，
# 之前这条只在 login.html 的弹窗里前端拦截，直接调 API 或换个 URL 就能绕过。
_MUST_CHANGE_PW_ALLOWLIST = {
    "/api/auth/change-password",
    "/api/auth/me",
    "/api/auth/logout",
    "/api/auth/config",
    "/api/auth/login",
    "/api/auth/vps-check",
    "/api/auth/vps-bootstrap",
}


def _check_must_change_password(user: User, request: Request) -> None:
    if not getattr(user, "must_change_password", False):
        return
    if request.url.path in _MUST_CHANGE_PW_ALLOWLIST:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="请先修改初始密码后再继续操作",
    )


def ensure_portal_access(user: User) -> None:
    """经销商（dealer）账号只允许在门店五件套门户登录，禁止进入内部工作台。

    两个部署共用同一套代码和同一个 Postgres 库，内部工作台无法从数据层面
    排除 dealer 账号，必须在认证层拦截。walkin 门户用 PDCA_PORTAL_MODE=walkin
    标识，其余（默认 workbench）一律拒绝 dealer 角色。
    """
    if getattr(get_settings(), "portal_mode", "workbench") == "walkin":
        return
    if user.role == "dealer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="经销商账号请使用门店五件套录入门户登录",
        )


async def get_current_user(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
    pdca_token: Annotated[str | None, Cookie()] = None,
) -> User:
    """
    解析当前用户，优先级：
    1. 反向代理 Header（多用户生产）
    2. JWT Cookie / Bearer（包括 Odoo SSO 签发的 VPS 会话）
    3. 服务端 vertu-cli hr +me（vps / hybrid 兜底）
    """
    settings = get_settings()
    mode = settings.auth_mode
    token = None
    if credentials and credentials.credentials:
        token = credentials.credentials
    elif pdca_token:
        token = pdca_token

    # 1) 代理 Header（所有模式均可，需显式开启）
    proxy_user = await _user_from_proxy_headers(session, request)
    if proxy_user:
        _check_must_change_password(proxy_user, request)
        ensure_portal_access(proxy_user)
        return proxy_user

    # 2) JWT
    if mode in ("hybrid", "local", "vps"):
        user = await _user_from_jwt(session, token)
        if user:
            _check_must_change_password(user, request)
            ensure_portal_access(user)
            return user
        if mode == "local":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")

    # 3) 服务端 vertu 会话（仅限本机请求，见 _user_from_vps 注释）
    if mode in ("vps", "hybrid"):
        user = await _user_from_vps(session, request)
        if user:
            _check_must_change_password(user, request)
            ensure_portal_access(user)
            return user

    if mode == "vps":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未检测到 Vertu 身份，请联系管理员检查 vertu-cli 服务凭据",
        )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")


def require_role(min_role: str) -> Callable:
    """返回要求最低角色的依赖。"""

    async def _checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        if _role_level(user.role) < _role_level(min_role):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return user

    return _checker


def can_write(user: User) -> bool:
    return _role_level(user.role) >= _role_level("sales")


def can_manage(user: User) -> bool:
    return _role_level(user.role) >= _role_level("manager")


def is_admin(user: User) -> bool:
    return user.role == "admin"
