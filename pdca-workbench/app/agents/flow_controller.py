# -*- coding: utf-8 -*-
"""流程控制器：Cron 与 Agent 共用的确定性业务入口（第 8 节）。

定位：确定性服务，不是 LLM Agent。第一版封装现有函数，不改变三追文案：
  prepare_performance_slot -> prepare_duzhan（09:45 采集组表）
  push_performance_slot   -> run_duzhan（整点推送）
  run_ctob_evening        -> run_ctob（工作日北京 20:00）
  build_daily_report      -> daily_report.build_report（08:30 日报）
  check_slot_health       -> 每档整点后 5 分钟健康检查（P0）

所有入口写 slot.*/meeting.* 事件；单数据源失败继续组表并写“待确认”，
缺快照、推送失败绝不静默，调用现有 notify 告警。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from loguru import logger

from app.alerting import notify
from app.config import get_settings
from app.duzhan import (
    cron_timezones,
    groups_for_tz,
    is_duzhan_workday,
    load_prepared,
    parse_hours,
    prepare_duzhan,
    run_duzhan,
)
from app.scheduler.run_ledger import claim_run, finish_run


def _slot_events_key(producer: str, event_type: str, tz_name: str, day: str, hour: int) -> str:
    return f"{producer}:{event_type}:{tz_name}:{day}:{hour:02d}"


def prepare_performance_slot(
    tz_name: str,
    hour: int,
    now: datetime | None = None,
    *,
    ledger_job: str = "duzhan_collect",
    channel_ids: list[str] | None = None,
) -> dict:
    """提前采集档位快照（P2 封装）：claim -> prepare_duzhan -> 事件 -> finish。

    `ledger_job` 按子 Agent 分档（db 源下同群同时刻互不抢 claim）；
    `channel_ids` 只组这些群的表。
    """
    from app.agents.events import write_event

    now = now or datetime.now(ZoneInfo(tz_name))
    if not is_duzhan_workday(tz_name, now):
        return {"tz": tz_name, "hour": hour, "skipped": "weekend"}
    day = now.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    bucket = f"{tz_name}:{hour:02d}:{day}"
    if not claim_run(ledger_job, bucket):
        return {"tz": tz_name, "hour": hour, "skipped": "already_claimed"}
    write_event("slot.collect_started", producer="flow_controller",
                event_key=_slot_events_key("flow_controller", "collect_started", tz_name, day, hour),
                payload={"tz": tz_name, "hour": hour, "day": day})
    try:
        payload = prepare_duzhan(tz_name, hour, now, channel_ids=channel_ids)
    except Exception as exc:  # noqa: BLE001
        logger.exception("督战官组表失败: {}", exc)
        finish_run(ledger_job, bucket, "failed", f"{type(exc).__name__}: {exc}")
        notify("督战官组表失败", f"{tz_name} {hour:02d}:00 {exc}"[:200])
        write_event("slot.collect_failed", producer="flow_controller",
                    event_key=_slot_events_key("flow_controller", "collect_failed", tz_name, day, hour),
                    payload={"error": str(exc)[:300]})
        return {"tz": tz_name, "hour": hour, "status": "failed", "error": str(exc)[:300]}
    write_event("slot.collect_completed", producer="flow_controller",
                event_key=_slot_events_key("flow_controller", "collect_completed", tz_name, day, hour),
                payload={"groups": len(payload.get("messages") or {})})
    finish_run(ledger_job, bucket, "sent")
    payload["status"] = "ok"
    return payload


def push_performance_slot(
    tz_name: str,
    hour: int,
    now: datetime | None = None,
    *,
    ledger_job: str = "duzhan",
    channel_ids: list[str] | None = None,
) -> dict:
    """整点推送（P2 封装）：claim -> run_duzhan -> 事件 -> finish；单群失败不阻断其他群。"""
    from app.agents.events import write_event

    now = now or datetime.now(ZoneInfo(tz_name))
    if not is_duzhan_workday(tz_name, now):
        return {"tz": tz_name, "hour": hour, "skipped": "weekend"}
    day = now.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    bucket = f"{tz_name}:{hour:02d}:{day}"
    if not claim_run(ledger_job, bucket):
        return {"tz": tz_name, "hour": hour, "skipped": "already_claimed"}
    write_event("slot.push_requested", producer="flow_controller",
                event_key=_slot_events_key("flow_controller", "push_requested", tz_name, day, hour),
                payload={"tz": tz_name, "hour": hour, "day": day})
    try:
        result = run_duzhan(tz_name, hour, now, channel_ids=channel_ids)
    except Exception as exc:  # noqa: BLE001
        logger.exception("督战官执行失败: {}", exc)
        finish_run(ledger_job, bucket, "failed", f"{type(exc).__name__}: {exc}")
        notify("督战官推送失败", f"{tz_name} {hour:02d}:00 {exc}"[:200])
        write_event("slot.push_failed", producer="flow_controller",
                    event_key=_slot_events_key("flow_controller", "push_failed", tz_name, day, hour),
                    payload={"error": str(exc)[:300]})
        return {"tz": tz_name, "hour": hour, "status": "failed", "error": str(exc)[:300]}
    failed = result.get("failed") or []
    sent = result.get("sent") or []
    # 对齐核心日报模式：部分失败 60 秒后整档重试一次（VPS 幂等键防重复推送）。
    if failed:
        logger.warning("督战官部分群失败，60 秒后重试一次: {} {} {}", tz_name, hour, failed)
        time.sleep(60)
        try:
            retry = run_duzhan(tz_name, hour, now, channel_ids=channel_ids)
        except Exception as exc:  # noqa: BLE001
            logger.exception("督战官重试失败: {}", exc)
            retry = {"sent": [], "failed": failed}
        retry_sent = retry.get("sent") or []
        retry_failed = retry.get("failed") or []
        sent = sorted(set(sent) | set(retry_sent))
        failed = retry_failed
    write_event("slot.push_succeeded" if not failed else "slot.push_failed",
                producer="flow_controller",
                event_key=_slot_events_key("flow_controller", "push_result", tz_name, day, hour),
                payload={"sent": sent, "failed": failed})
    if failed:
        finish_run(ledger_job, bucket, "failed", ",".join(failed)[:512])
        notify("督战官部分群失败", f"{tz_name} {hour:02d}:00 {failed}")
        return {"tz": tz_name, "hour": hour, "status": "partial", "sent": sent, "failed": failed}
    finish_run(ledger_job, bucket, "sent", ",".join(sent)[:512])
    return {"tz": tz_name, "hour": hour, "status": "ok", "sent": sent}


def run_ctob_slot(
    hour: int = 20,
    day: str | None = None,
    now: datetime | None = None,
    *,
    owners: tuple | None = None,
    ledger_job: str = "ctob",
) -> dict:
    """C转B 按档推送（P2 封装）：工作日北京 10:00 / 15:00 / 20:00，每档独立台账。

    `owners` 有值时只处理这些群（db 源下一个子 Agent 一次）；`ledger_job` 按子 Agent 分档。
    """
    from app.ctob import TZ_SHANGHAI as _SH, run_ctob, slot_title

    now = now or datetime.now(ZoneInfo(_SH))
    if not is_duzhan_workday(_SH, now):
        return {"skipped": "weekend"}
    day = day or now.astimezone(ZoneInfo(_SH)).strftime("%Y-%m-%d")
    bucket = f"{day}-{hour:02d}"
    if not claim_run(ledger_job, bucket):
        return {"skipped": "already_claimed"}
    try:
        result = run_ctob(day, now, hour=hour, owners=owners)
    except Exception as exc:  # noqa: BLE001
        logger.exception("C转B {}失败: {}", slot_title(hour), exc)
        finish_run(ledger_job, bucket, "failed", f"{type(exc).__name__}: {exc}")
        notify(f"C转B{slot_title(hour)}失败", str(exc)[:200])
        return {"status": "failed", "error": str(exc)[:300]}
    failed = result.get("failed") or []
    sent = result.get("sent") or []
    # 对齐核心日报模式：部分失败 60 秒后整档重试一次（幂等键 ctob-* 防重复推送）。
    if failed:
        logger.warning("C转B {}部分失败，60 秒后重试一次: {}", slot_title(hour), failed)
        time.sleep(60)
        try:
            retry = run_ctob(day, now, hour=hour, owners=owners)
        except Exception as exc:  # noqa: BLE001
            logger.exception("C转B {}重试失败: {}", slot_title(hour), exc)
            retry = {"sent": [], "failed": failed}
        sent = sorted(set(sent) | set(retry.get("sent") or []))
        failed = retry.get("failed") or []
    if failed:
        finish_run(ledger_job, bucket, "failed", ",".join(failed)[:512])
        notify(f"C转B{slot_title(hour)}部分失败", str(failed))
        return {"status": "partial", "sent": sent, "failed": failed}
    finish_run(ledger_job, bucket, "sent", ",".join(sent)[:512])
    return {"status": "ok", "sent": sent}


def run_ctob_evening(day: str | None = None, now: datetime | None = None) -> dict:
    """C转B 晚追（20:00 档，兼容旧调用）。"""
    return run_ctob_slot(20, day, now)


def build_daily_report(day: str) -> str:
    """经营日报生成（P2 封装）。"""
    from app.daily_report import build_report

    return build_report(day)


def _scheduled_run_status(job_name: str, bucket: str) -> dict:
    """读取 run_ledger 中某次运行的凭证状态；不存在返回 {"found": False}。"""
    from sqlmodel import Session, select

    from app.database import get_engine
    from app.models.scheduled_job_run import ScheduledJobRun

    run_key = f"{job_name}:{bucket}"
    try:
        with Session(get_engine()) as session:
            row = session.exec(
                select(ScheduledJobRun).where(ScheduledJobRun.run_key == run_key)
            ).first()
    except Exception as exc:  # noqa: BLE001 — 健康检查自身降级，不能抛
        logger.warning("run_ledger 查询失败: {}", exc)
        return {"found": False, "error": str(exc)[:200]}
    if row is None:
        return {"found": False}
    return {
        "found": True,
        "status": row.status,
        "detail": row.detail,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def check_slot_health(
    tz_name: str,
    hour: int,
    day: str,
    *,
    now: datetime | None = None,
    expect_push: bool = True,
    alert: bool = True,
) -> dict:
    """P0 档位健康检查：collect run / slot 快照 / push run 三项凭证。

    检查：
    1. duzhan_collect:{tz}:{hour}:{day} 是否 sent；
    2. 快照文件是否存在、JSON 可解析、prepared_at 属于当天该档、群数量正确；
    3. duzhan:{tz}:{hour}:{day} 推送凭证是否存在且 sent。
    缺失/失败时返回问题清单；alert=True 才调用 notify（后台展示传 False）。
    只读检查，绝不补发、不推真实群、不修改三追文案。
    """
    problems: list[dict] = []
    bucket = f"{tz_name}:{hour:02d}:{day}"
    slot = load_prepared(tz_name, hour, day)
    expected_groups = len(groups_for_tz(tz_name))

    collect = _scheduled_run_status("duzhan_collect", bucket)
    if not collect.get("found"):
        problems.append({"step": "collect_run", "detail": "未找到 duzhan_collect 运行凭证"})
    elif collect.get("status") != "sent":
        problems.append({"step": "collect_run", "detail": f"状态 {collect.get('status')} "
                         f"{collect.get('detail') or ''}"[:200]})

    if slot is None:
        problems.append({"step": "slot_snapshot", "detail": "快照文件缺失"})
    else:
        prepared = slot.get("prepared_at") or ""
        slot_day = (slot.get("day") or "")
        slot_hour = slot.get("hour")
        if slot_day != day or slot_hour != hour:
            problems.append({"step": "slot_snapshot", "detail": f"快照属于 {slot_day} {slot_hour}:00，与检查档不符"})
        elif not prepared:
            problems.append({"step": "slot_snapshot", "detail": "prepared_at 缺失"})
        else:
            try:
                parsed = datetime.fromisoformat(prepared)
                local = parsed.astimezone(ZoneInfo(tz_name))
                if local.strftime("%Y-%m-%d") != day or local.hour not in (hour, hour - 1):
                    problems.append({"step": "slot_snapshot", "detail": f"prepared_at={prepared} 不属于当天该档"})
            except (ValueError, TypeError):
                problems.append({"step": "slot_snapshot", "detail": f"prepared_at 不可解析: {prepared[:40]}"})
        groups = len(slot.get("messages") or {})
        if groups != expected_groups:
            problems.append({"step": "slot_snapshot", "detail": f"群消息 {groups} != 预期 {expected_groups}"})

    if expect_push:
        push = _scheduled_run_status("duzhan", bucket)
        if not push.get("found"):
            problems.append({"step": "push_run", "detail": "未找到 duzhan 推送凭证"})
        elif push.get("status") != "sent":
            problems.append({"step": "push_run", "detail": f"状态 {push.get('status')} "
                         f"{push.get('detail') or ''}"[:200]})

    report = {
        "date": day,
        "timezone": tz_name,
        "hour": f"{hour:02d}:00",
        "ok": not problems,
        "problems": problems,
        "suggested_action": "人工核对调度器与机器人配置，必要时手动补跑该档" if problems else "",
        "checked_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    }
    if problems:
        detail = "; ".join(f"{p['step']}:{p['detail']}" for p in problems)[:280]
        if alert:
            notify("督战档位健康检查失败", f"{tz_name} {hour:02d}:00 {day} → {detail}")
        logger.error("slot health FAILED {} {} {}: {}", tz_name, hour, day, detail)
    else:
        logger.info("slot health OK {} {} {}", tz_name, hour, day)
    return report


def run_health_checks_for(now: datetime | None = None) -> list[dict]:
    """每 5 分钟扫描：补扫当天已过档位 + 当前档（整点后 5 分钟起）。

    补扫语义：调度器/容器在整点前后重启也不会漏检——当天任何已到点的
    档位都会在下一个 5 分钟 tick 被检查。每个（时区, 日期, 档位）只告警
    一次（slot.health_checked 事件幂等去重）。只检查工作日档位。
    """
    from app.agents.events import write_event

    settings = get_settings()
    reports: list[dict] = []
    for tz_name in cron_timezones():
        try:
            zone = ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001
            continue
        local = (now or datetime.now(zone)).astimezone(zone)
        if not is_duzhan_workday(tz_name, local):
            continue
        day = local.strftime("%Y-%m-%d")
        for hour in parse_hours(settings.duzhan_times):
            if hour > local.hour:
                continue  # 未到点的档位
            if hour == local.hour and local.minute < 5:
                continue  # 整点后 5 分钟内推送可能尚未完成，等下一轮
            event_key = f"flow_controller:health_checked:{tz_name}:{day}:{hour:02d}"
            if not write_event("slot.health_checked", producer="flow_controller",
                               event_key=event_key,
                               payload={"tz": tz_name, "day": day, "hour": hour}):
                continue  # 本档今天已检查并告警过
            reports.append(check_slot_health(tz_name, hour, day, now=local, alert=True))
    return reports


def all_slot_health_today(day: str) -> list[dict]:
    """给定日期全部时区/档位的健康状态（供后台展示，不告警）。"""
    settings = get_settings()
    reports = []
    for tz_name in cron_timezones():
        for hour in parse_hours(settings.duzhan_times):
            reports.append(check_slot_health(tz_name, hour, day, expect_push=True, alert=False))
    return reports
