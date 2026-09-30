# -*- coding: utf-8 -*-
"""外发定时任务的 at-most-once 运行凭证。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.database import get_engine
from app.models.scheduled_job_run import ScheduledJobRun

# claim 后进程异常退出（被杀/断电/重启）时，超时的 sending 记录按失败回收，
# 允许后续触发（如补发兜底轮）重新执行；正常执行时长（<10 分钟）远小于该窗口。
_STALE_SENDING_SECONDS = 30 * 60


def claim_run(job_name: str, bucket: str) -> bool:
    run_key = f"{job_name}:{bucket}"
    # 本机进程连生产库时拒绝占档：避免本地跑任务把生产当天的档位吃掉、甚至真外发。
    try:
        from app.config import get_settings
        import os as _os
        if getattr(get_settings(), "remote_db_from_host", False) and _os.environ.get("PDCA_ALLOW_REMOTE_DB", "") != "1":
            logger.error("本机直连生产库，拒绝占用档位 {}（放行请设 PDCA_ALLOW_REMOTE_DB=1）", run_key)
            return False
    except Exception as _exc:  # 护栏本身绝不阻断生产
        logger.debug("远程库护栏检查跳过: {}", _exc)
    now = datetime.now(timezone.utc)
    with Session(get_engine()) as session:
        existing = session.exec(
            select(ScheduledJobRun).where(ScheduledJobRun.run_key == run_key)
        ).first()
        if existing is not None:
            if existing.status == "sending" and existing.started_at is not None:
                started = existing.started_at
                if started.tzinfo is None:
                    # SQLite 回读丢失时区；本表只写 UTC，按 UTC 处理。
                    started = started.replace(tzinfo=timezone.utc)
                if now - started <= timedelta(seconds=_STALE_SENDING_SECONDS):
                    return False
                # 超时回收：复用同一行重新认领（新建行会撞 run_key 唯一约束）。
                existing.status = "sending"
                existing.detail = ""
                existing.started_at = now
                existing.finished_at = None
                session.add(existing)
                session.commit()
                return True
            if existing.status == "failed":
                # 失败允许兜底触发重试一次（+30 分钟备份、崩溃补跑）。
                # 重复外发由各任务的 VPS 幂等键兜住，不会真的发两条。
                existing.status = "sending"
                existing.detail = ""
                existing.started_at = now
                existing.finished_at = None
                session.add(existing)
                session.commit()
                return True
            return False
        session.add(ScheduledJobRun(
            run_key=run_key, job_name=job_name, bucket=bucket, status="sending"
        ))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return False
    return True


def finish_run(job_name: str, bucket: str, status: str, detail: str = "") -> None:
    run_key = f"{job_name}:{bucket}"
    with Session(get_engine()) as session:
        row = session.exec(
            select(ScheduledJobRun).where(ScheduledJobRun.run_key == run_key)
        ).first()
        if row is None:
            return
        row.status = status[:16]
        row.detail = detail[:512]
        row.finished_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()
