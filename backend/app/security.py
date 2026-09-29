# -*- coding: utf-8 -*-
"""鉴权占位。

生产里这一层接你们自己的登录体系（本地账号 / SSO / 网关注入身份）。
本仓库为了让 demo 开箱能跑，默认放行；把 DUZHAN_REQUIRE_AUTH=1 打开后
就要求 `Authorization: Bearer <DUZHAN_ADMIN_TOKEN>`。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, status


@dataclass(frozen=True)
class CurrentUser:
    username: str
    role: str


def _auth_required() -> bool:
    return os.environ.get("DUZHAN_REQUIRE_AUTH", "0").strip().lower() not in {"", "0", "false", "no"}


async def require_admin(authorization: Annotated[Optional[str], Header()] = None) -> CurrentUser:
    """配置接口一律要求管理员。"""
    if not _auth_required():
        return CurrentUser(username="demo-admin", role="admin")
    expected = os.environ.get("DUZHAN_ADMIN_TOKEN", "").strip()
    scheme, _, credential = (authorization or "").partition(" ")
    if not expected or scheme.lower() != "bearer" or credential.strip() != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    return CurrentUser(username="admin", role="admin")
