# -*- coding: utf-8 -*-
"""追加写事件服务：组件交接与审计的统一出口。

事件写入幂等（event_key 唯一）；写入失败不抛出，避免影响主流程，
但会记录告警日志（审计链缺失必须可见）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.database import get_engine
from app.agents.models import AgentEvent


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def write_event(
    event_type: str,
    *,
    event_key: str = "",
    run_id: int | None = None,
    task_id: int | None = None,
    producer: str = "",
    group_channel_id: str = "",
    payload: dict | None = None,
) -> bool:
    """写入一条事件；event_key 缺省按内容生成（sha1 前 40 位）。

    同一 event_key 只落库一次，返回 True 表示本次写入成功（重复返回 False）。
    幂等键建议：producer:event_type:run_id:slot:channel。
    """
    if not event_key:
        import hashlib

        raw = json.dumps([producer, event_type, run_id, task_id, group_channel_id], ensure_ascii=False)
        event_key = "gen:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:40]
    try:
        with Session(get_engine()) as session:
            from sqlmodel import select

            already = session.exec(
                select(AgentEvent).where(AgentEvent.event_key == event_key)
            ).first()
            if already is not None:
                return False
            session.add(AgentEvent(
                event_key=event_key,
                run_id=run_id,
                task_id=task_id,
                event_type=event_type[:64],
                producer=producer[:64],
                group_channel_id=group_channel_id[:64],
                payload_json=json.dumps(payload or {}, ensure_ascii=False)[:65536],
                occurred_at=utcnow(),
            ))
            session.commit()
            return True
    except IntegrityError:
        return False
    except Exception as exc:  # noqa: BLE001 — 审计失败不阻断主流程，但必须可见
        logger.warning("事件写入失败 type={} key={}: {}", event_type, event_key, exc)
        return False


def latest_events(
    event_type: str | None = None,
    *,
    group_channel_id: str = "",
    limit: int = 50,
) -> list[dict]:
    """按时间倒序读取事件（供群 Agent 上下文装配与后台展示）。"""
    from sqlmodel import select

    try:
        with Session(get_engine()) as session:
            statement = select(AgentEvent).order_by(AgentEvent.id.desc()).limit(min(limit, 200))
            if event_type:
                statement = statement.where(AgentEvent.event_type == event_type)
            if group_channel_id:
                statement = statement.where(AgentEvent.group_channel_id == group_channel_id)
            rows = session.exec(statement).all()
            return [
                {
                    "id": row.id,
                    "event_key": row.event_key,
                    "event_type": row.event_type,
                    "run_id": row.run_id,
                    "task_id": row.task_id,
                    "producer": row.producer,
                    "group_channel_id": row.group_channel_id,
                    "payload": json.loads(row.payload_json) if row.payload_json else {},
                    "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
                }
                for row in rows
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("事件读取失败: {}", exc)
        return []
