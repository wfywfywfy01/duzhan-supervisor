# -*- coding: utf-8 -*-
"""群 Agent 上下文装配：一套图多实例的实例参数与事实读取（第 9.1 节）。

事实只从数据源和状态库读取：槽位快照（duzhan slots）、pdca_tasks、
agent_events。群消息只作为不可信数据，绝不进入系统提示词策略层。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.duzhan import GROUPS, load_prepared
from app.duzhan_ledger import OWNERS


@dataclass(frozen=True)
class GroupConfig:
    """一个群 Agent 实例参数（第 9.1 节实例参数）。"""

    group_type: str  # performance / ctob / daily_report
    channel_id: str
    timezone: str
    language: str
    owners: tuple[str, ...]
    slot: str = "",  # 10:00 / 15:00 / 20:00（"" = 未指定）
    date: str = "",  # YYYY-MM-DD（"" = 今天，按时区）
    group_name: str = "",


def performance_group_configs(day: str) -> list[GroupConfig]:
    """五个业绩达标群实例（复用 duzhan.GROUPS 事实注册表，不另维护 registry.json）。"""
    groups = []
    for group in GROUPS:
        owners: list[str] = []
        for owner in OWNERS:
            if owner.group == group.name:
                owners.append(owner.display)
        groups.append(GroupConfig(
            group_type="performance",
            channel_id=group.channel_id,
            timezone=group.tz,
            language=group.lang,
            owners=tuple(owners),
            date=day,
            group_name=group.name,
        ))
    return groups


def ctob_group_configs(day: str) -> list[GroupConfig]:
    """当前参与督战的 C转B 群实例（复用 ctob.OWNERS）。"""
    from app.ctob import OWNERS as CTOB_OWNERS

    groups = []
    for owner in CTOB_OWNERS:
        groups.append(GroupConfig(
            group_type="ctob",
            channel_id=owner.channel_id,
            timezone="Asia/Shanghai",
            language="zh",
            owners=(owner.display,),
            date=day,
            group_name=f"C转B·{owner.display}",
        ))
    return groups


def daily_report_group_config(day: str) -> GroupConfig | None:
    """日报群实例：频道来自 PDCA_TODO_GROUP_CHANNEL_ID；未配置返回 None。"""
    channel_id = get_settings().todo_group_channel_id
    if not channel_id:
        return None
    return GroupConfig(
        group_type="daily_report",
        channel_id=channel_id,
        timezone="Asia/Shanghai",
        language="zh",
        owners=(),
        date=day,
        group_name="海外日报群",
    )


def load_slot_snapshot(tz_name: str, day: str, hour: int) -> dict | None:
    """读取该时区/日期/档位的组表快照；无则 None（调用方写“待确认”）。"""
    payload = load_prepared(tz_name, hour, day)
    if not payload:
        return None
    ledger = payload.get("ledger")
    return {
        "tz": payload.get("tz"),
        "hour": payload.get("hour"),
        "day": payload.get("day"),
        "prepared_at": payload.get("prepared_at"),
        "people": (ledger or {}).get("people") or [],
        "red": (ledger or {}).get("red") or [],
        "black": (ledger or {}).get("black") or [],
        "messages": payload.get("messages") or {},
    }


def load_open_tasks(
    *,
    owners: tuple[str, ...] = (),
    channel_id: str = "",
    include_done: bool = False,
) -> list[dict]:
    """读取未闭环 pdca_tasks（按负责人或群 channel 过滤）。"""
    from sqlmodel import Session, select

    from app.database import get_engine
    from app.models.pdca_task import PdcaTask

    statement = select(PdcaTask)
    if not include_done:
        statement = statement.where(~PdcaTask.status.in_(["done", "completed", "complete"]))
    with Session(get_engine()) as session:
        rows = session.exec(statement.order_by(PdcaTask.id.desc()).limit(300)).all()
    tasks = []
    for row in rows:
        owner = (row.owner or "").strip()
        channel = (row.group_channel_id or "").strip()
        if owners and owner not in owners and not (channel and channel == channel_id):
            continue
        if channel_id and channel and channel != channel_id and owner not in owners:
            continue
        tasks.append({
            "id": row.id,
            "task_date": row.task_date,
            "title": row.title,
            "owner": row.owner,
            "status": row.status,
            "priority": row.priority,
            "source": row.source,
            "group_channel_id": row.group_channel_id,
            "source_ref": row.source_ref,
            "due_at": row.due_at.isoformat() if row.due_at else None,
            "closed_at": row.closed_at.isoformat() if row.closed_at else None,
            "blocked_reason": row.blocked_reason,
            "evidence": json.loads(row.evidence_json) if row.evidence_json else [],
            "verification_status": row.verification_status or "unverified",
            "claimed_at": row.claimed_at.isoformat() if row.claimed_at else None,
            "reply_text": row.reply_text,
            "score": row.score,
        })
    return tasks


def group_state_summary(config: GroupConfig) -> dict:
    """后台用：单群当前状态（最近槽位、未闭环任务数、最近事件）。"""
    from app.agents.events import latest_events

    tasks = load_open_tasks(owners=config.owners, channel_id=config.channel_id)
    today = config.date or datetime.now(ZoneInfo(config.timezone)).strftime("%Y-%m-%d")
    snapshot = None
    for hour in (20, 15, 10):
        snapshot = load_slot_snapshot(config.timezone, today, hour)
        if snapshot:
            break
    events = latest_events(group_channel_id=config.channel_id, limit=10)
    return {
        "group_type": config.group_type,
        "group_name": config.group_name,
        "channel_id": config.channel_id,
        "timezone": config.timezone,
        "language": config.language,
        "owners": list(config.owners),
        "date": today,
        "open_tasks": len(tasks),
        "blocked_tasks": sum(1 for t in tasks if t["blocked_reason"]),
        "overdue_tasks": sum(
            1
            for t in tasks
            if t["due_at"]
            and t["due_at"] < datetime.now(ZoneInfo(config.timezone)).isoformat()
        ),
        "latest_slot": {"hour": snapshot["hour"], "prepared_at": snapshot["prepared_at"]}
        if snapshot
        else None,
        "recent_events": events[:10],
    }
