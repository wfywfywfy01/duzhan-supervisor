# -*- coding: utf-8 -*-
"""Vertu / vertu-cli hr +me / 反向代理 Header 身份解析与本地用户映射。"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from typing import Any

from fastapi import HTTPException
from loguru import logger
from fastapi import HTTPException
from sqlmodel import Session, select

from app.auth.models import User
from app.auth.odoo_login_map import resolve_pdca_username
from app.auth.security import hash_password
from app.config import get_settings
from app.vertu.client import resolve_vertu_command

_VPS_CACHE: dict[str, Any] = {"ts": 0.0, "payload": None, "key": ""}
_CACHE_SECONDS = 30


def fetch_vps_me_payload() -> dict | None:
    """
    读取 `vertu-cli hr +me` 当前登录用户（服务端会话或 App Key）。

    注意：多用户共享同一 PDCA 进程时，此身份为服务器 vertu 登录态，
    生产多用户应优先使用反向代理注入的 Header（见 identity_from_headers）。

    @returns Odoo 用户 dict，失败返回 None
    """
    now = time.time()
    cached = _VPS_CACHE.get("payload")
    if cached and now - float(_VPS_CACHE.get("ts") or 0) < _CACHE_SECONDS:
        return cached
    try:
        completed = subprocess.run(
            [resolve_vertu_command(), "hr", "+me"],
            cwd=str(get_settings().repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        result = json.loads(completed.stdout.strip()) if completed.returncode == 0 else None
        if isinstance(result, dict) and result.get("user_id"):
            _VPS_CACHE["ts"] = now
            _VPS_CACHE["payload"] = result
            _VPS_CACHE["key"] = "vertu-cli-hr-me"
            return result
    except Exception as exc:
        logger.warning("vertu-cli hr +me 失败: {}", exc)
    return None


def identity_from_headers(headers: dict[str, str]) -> dict | None:
    """
    从反向代理注入的 Header 解析用户（多用户生产推荐）。

    支持：
    - X-VPS-User-Login / X-Forwarded-User
    - X-VPS-User-Name
    - X-VPS-Job-Title
    - X-VPS-Department
    - X-VPS-User-Role（可选：admin|manager|sales|dealer|viewer）

    @param headers 小写 key 的 header 字典
    """
    login = (
        headers.get("x-vps-user-login")
        or headers.get("x-forwarded-user")
        or headers.get("x-remote-user")
        or ""
    ).strip()
    if not login:
        return None
    name = (headers.get("x-vps-user-name") or headers.get("x-forwarded-preferred-username") or "").strip()
    job = (headers.get("x-vps-job-title") or "").strip()
    dept = (headers.get("x-vps-department") or "").strip()
    role_hint = (
        (headers.get("x-vps-user-role") or "").strip().lower()
        if get_settings().trust_proxy_role_header
        else ""
    )
    owner_key = (headers.get("x-vps-owner-key") or "").strip()
    team_key = (headers.get("x-vps-team-key") or "").strip()
    return {
        "login": login,
        "name": name or login,
        "employee_name": name or login,
        "display_name": name or login,
        "job_title": job,
        "department_name": dept,
        "role_hint": role_hint,
        "owner_key": owner_key,
        "team_key": team_key,
        "_source": "proxy-header",
    }


def _nested(row: dict, *keys: str) -> str:
    for key in keys:
        val = row.get(key)
        if val not in (None, ""):
            return str(val).strip()
    return ""


def infer_pdca_role(vps: dict) -> str:
    """
    从 VPS 用户信息推断 PDCA 角色。未匹配时默认 viewer（最小权限）。

    @param vps odoo me / header 身份
    """
    hint = _nested(vps, "role_hint").lower()
    if hint in ("admin", "manager", "sales", "dealer", "viewer"):
        return hint

    groups = " ".join(
        str(vps.get(k) or "")
        for k in ("groups", "group_names", "roles", "role")
    ).lower()
    job = _nested(vps, "job_title", "role", "title").lower()
    dept = _nested(vps, "department_name", "department", "dept_name").lower()
    login = _nested(vps, "login").lower()

    if any(k in groups for k in ("admin", "settings")) or vps.get("is_admin") in (True, 1, "1"):
        return "admin"
    if login in {"admin", "root"}:
        return "admin"
    if "manager" in groups or "主管" in job or "总监" in job:
        return "manager"
    if "中台" in job or "中台" in dept or "数据分析" in job:
        return "manager"
    if "经销商" in dept or "dealer" in groups:
        return "dealer"
    if "销售" in job or "sales" in groups:
        return "sales"
    return "viewer"


def vps_display_name(vps: dict) -> str:
    """@returns 真实姓名"""
    return _nested(vps, "employee_name", "name", "display_name", "login") or "VPS 用户"


def vps_username(vps: dict) -> str:
    """@returns 本地 users.username（Odoo login，经销商走对照表）"""
    login = _nested(vps, "login")
    if login:
        return resolve_pdca_username(login)
    uid = vps.get("user_id") or vps.get("id")
    if uid:
        return f"vps-{uid}"
    return f"vps-{secrets.token_hex(4)}"


def _sync_role_enabled() -> bool:
    """是否每次同步覆盖本地 role（默认否，避免冲掉手工调权）。"""
    return os.environ.get("PDCA_VPS_SYNC_ROLE", "0").strip() == "1"


def ensure_vps_user(session: Session, vps: dict) -> User:
    """
    将 VPS 用户同步到本地 users 表（仅用于权限与展示，密码随机）。

    新建用户时写入推断角色；已存在用户默认只更新姓名，不覆盖 role。

    @param session SQLModel session
    @param vps odoo me / header payload
    """
    username = vps_username(vps)
    name = vps_display_name(vps)
    role = infer_pdca_role(vps)
    # A display name is not an ownership binding. Only explicitly configured
    # source mappings may grant access to salesperson-keyed business rows.
    sales_name = _nested(vps, "sales_name") if role == "sales" else ""
    owner_key = _nested(vps, "owner_key") if role == "sales" else ""
    team_key = _nested(vps, "team_key") if role == "manager" else ""
    data_scope = {
        "admin": "all",
        "manager": "team",
        "sales": "self",
        "dealer": "self",
        "viewer": "none",
    }.get(role, "none")

    user = session.exec(select(User).where(User.username == username)).first()
    if user is not None and not user.is_active:
        # SSO proves identity, but cannot undo a local administrator's ban.
        raise HTTPException(status_code=403, detail="账号已停用")
    if not user:
        user = User(
            username=username,
            hashed_password=hash_password(secrets.token_urlsafe(32)),
            role=role,
            display_name=name,
            sales_name=sales_name,
            owner_key=owner_key,
            team_key=team_key,
            data_scope=data_scope,
            must_change_password=False,
            is_active=True,
        )
    else:
        user.display_name = name
        if user.must_change_password:
            # SSO/VPS 已完成身份验证；保留“首次改密”会让随机本地密码账号
            # 永久卡在 403。清除标记时同时轮换本地密码并撤销旧 JWT，避免
            # 已知的初始化密码因 SSO 激活而重新获得访问权。
            user.hashed_password = hash_password(secrets.token_urlsafe(32))
            user.pwd_version = (user.pwd_version or 0) + 1
            user.must_change_password = False
        if _sync_role_enabled():
            user.role = role
        if role == "sales" and not (getattr(user, "sales_name", "") or ""):
            user.sales_name = sales_name
        if role == "sales" and not (getattr(user, "owner_key", "") or ""):
            user.owner_key = owner_key
        if role == "manager" and not (getattr(user, "team_key", "") or ""):
            user.team_key = team_key
        if not (getattr(user, "data_scope", "") or ""):
            user.data_scope = data_scope

    session.add(user)
    session.commit()
    session.refresh(user)
    if user.role == "sales" and (getattr(user, "owner_key", "") or "").strip():
        from app.auth.scope import sync_user_dealer_assignments
        sync_user_dealer_assignments(user, session)
        session.commit()
    return user


def vps_profile(vps: dict) -> dict:
    """@returns 前端展示用 profile"""
    name = vps_display_name(vps)
    job = _nested(vps, "job_title", "role", "title") or _nested(vps, "department_name", "department")
    role = infer_pdca_role(vps)
    return {
        "username": vps_username(vps),
        "display_name": name,
        "sales_name": _nested(vps, "sales_name") if role == "sales" else "",
        "owner_key": _nested(vps, "owner_key") if role == "sales" else "",
        "team_key": _nested(vps, "team_key") if role == "manager" else "",
        "data_scope": {"admin": "all", "manager": "team", "sales": "self", "dealer": "self", "viewer": "none"}.get(role, "none"),
        "role": role,
        "job_title": job,
        "vps_user_id": vps.get("user_id") or vps.get("id"),
        "login": vps.get("login"),
        "source": vps.get("_source") or "vertu-cli-hr-me",
    }
