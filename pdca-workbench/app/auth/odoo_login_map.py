# -*- coding: utf-8 -*-
"""Odoo 经销商 login → 已有工作台 username（脱敏示例）。

真实项目里这张表来自《用户 (res.users).xlsx》：能唯一对上本地门店账号的才进表，
对不上的一律拒绝自动建号（避免产生孤儿账号）。开源版只留两条示例。

对接你自己的 Odoo 时，把这张表换成你自己的映射即可；
`resolve_pdca_username` 与 `should_refuse_odoo_sso_create` 的语义不变。
"""
from __future__ import annotations

#: Odoo login -> 工作台 username（须已存在且绑定了经销商）
ODOO_LOGIN_TO_USERNAME: dict[str, str] = {
    "demo-store@example.com": "EXAMPLE_STORE_MSK",
    "Demo Boutique": "ExampleBoutique",
}

#: 表里有、但不能唯一对上门店的 login：免登拒绝，禁止新建账号
ODOO_LOGIN_REFUSE_CREATE: frozenset[str] = frozenset(
    {
        "another-demo@example.com",
        "Example Holding",
    }
)


def resolve_pdca_username(odoo_login: str) -> str:
    """把 Odoo login 收成工作台 username；无映射则原样返回。"""
    login = (odoo_login or "").strip()
    if not login:
        return ""
    return ODOO_LOGIN_TO_USERNAME.get(login) or login


def should_refuse_odoo_sso_create(odoo_login: str) -> bool:
    """未映射经销商：禁止免登时新建账号。"""
    login = (odoo_login or "").strip()
    return login in ODOO_LOGIN_REFUSE_CREATE
