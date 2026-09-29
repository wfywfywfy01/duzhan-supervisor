# -*- coding: utf-8 -*-
"""督战名单适配器（示例数据）。

生产里这份名单来自 HR / 组织架构 / 群成员表；这里用示例数据。
接自己的系统时，只要保证 roster_by_group() / roster_names() 返回真实名单即可，
校验逻辑（people 块必须在名单内）会自动生效。
"""
from __future__ import annotations

GROUP_A = "示例达标群 A"
GROUP_B = "示例达标群 B"

ROSTER: tuple[dict, ...] = (
    {"group": GROUP_A, "display": "示例成员A"},
    {"group": GROUP_A, "display": "示例成员B"},
    {"group": GROUP_A, "display": "示例成员C"},
    {"group": GROUP_B, "display": "示例成员D"},
    {"group": GROUP_B, "display": "示例成员E"},
)

#: 跟进群（一条线索一个跟进人）的群主：不在达标群名单里，但也在督战范围内
FOLLOW_OWNERS: tuple[dict, ...] = (
    {"group": "示例跟进群 1", "display": "示例跟进人1"},
    {"group": "示例跟进群 2", "display": "示例跟进人2"},
)


def roster_by_group() -> dict[str, list[str]]:
    """群名 → 该群成员。"""
    table: dict[str, list[str]] = {}
    for item in ROSTER:
        table.setdefault(item["group"], []).append(item["display"])
    return table


def roster_names() -> list[str]:
    """全体督战对象：达标群成员 + 跟进群主。"""
    names = [item["display"] for item in ROSTER]
    for item in FOLLOW_OWNERS:
        if item["display"] not in names:
            names.append(item["display"])
    return names
