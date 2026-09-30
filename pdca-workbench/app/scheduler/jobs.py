# -*- coding: utf-8 -*-
"""督战官调度：达标群三追、C转B 跟进群、小黑屋祝贺、策略核查、@轮询。

公开版只保留督战官相关任务；原项目里还有日报、待办、物流、训练等任务，
与督战官无关，已按最小可运行原则裁掉。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from app.alerting import notify
from app.config import get_settings

_scheduler: BackgroundScheduler | None = None
TZ_SHANGHAI = "Asia/Shanghai"


def _tz_slug(tz_name: str) -> str:
    return tz_name.replace("/", "_").replace("+", "p").lower()


def duzhan_collect_job(
    tz_name: str,
    hour: int,
    *,
    ledger_job: str = "duzhan_collect",
    channel_ids: list[str] | None = None,
) -> None:
    """每轮提前 N 分钟踩点采集（群/MCP/Vemory/VPS），不发群。"""
    from app.agents.flow_controller import prepare_performance_slot

    result = prepare_performance_slot(tz_name, hour, ledger_job=ledger_job, channel_ids=channel_ids)
    if result.get("skipped"):
        logger.info("督战官本档跳过组表 {} {} {}", tz_name, hour, result["skipped"])
    elif result.get("status") == "failed":
        logger.error("督战官组表失败 {} {}: {}", tz_name, hour, result.get("error"))
    else:
        logger.info("督战官组表完成 {} {}", tz_name, hour)


def duzhan_job(
    tz_name: str,
    hour: int,
    *,
    ledger_job: str = "duzhan",
    channel_ids: list[str] | None = None,
) -> None:
    """按群时区推送：北京群 10/15/20，巴黎群按当地 10/15/20。"""
    from app.agents.flow_controller import push_performance_slot

    result = push_performance_slot(tz_name, hour, ledger_job=ledger_job, channel_ids=channel_ids)
    if result.get("skipped"):
        logger.info("督战官本档跳过推送 {} {} {}", tz_name, hour, result["skipped"])
    elif result.get("status") == "failed":
        logger.error("督战官推送失败 {} {}: {}", tz_name, hour, result.get("error"))
    elif result.get("status") == "partial":
        logger.error("督战官部分群失败 {} {}: {}", tz_name, hour, result.get("failed"))
    else:
        logger.info("督战官已推送 {} {}", tz_name, hour)


def ctob_job(hour: int = 20, *, owners: tuple | None = None, ledger_job: str = "ctob") -> None:
    """工作日 10:00 / 15:00 / 20:00：C转B 跟进群按档推送。

    db 源下每个子 Agent 注册一条，`owners` 只带自己那个群、台账也各记各的。
    """
    from app.agents.flow_controller import run_ctob_slot

    result = run_ctob_slot(hour, owners=owners, ledger_job=ledger_job)
    label = f"{hour:02d}:00 C转B"
    if result.get("skipped"):
        logger.info("{}跳过 {}", label, result["skipped"])
    elif result.get("status") == "failed":
        logger.error("{}失败: {}", label, result.get("error"))
    elif result.get("status") == "partial":
        logger.error("{}部分失败: {}", label, result.get("failed"))
    else:
        logger.info("{}已推送 {}", label, result.get("sent"))


def heiwu_poll_job() -> None:
    """扫小黑屋群：有新的开单晒单就回一条祝贺（机器人身份，同一条只回一次）。"""
    from app.heiwu import poll_once

    result = poll_once()
    if result.get("skipped"):
        logger.info("小黑屋本轮跳过 {}", result["skipped"])
    elif result.get("replied"):
        logger.info("小黑屋本轮已祝贺 {}", result["replied"])


def duzhan_at_poll_job() -> None:
    """每分钟扫达标群：只有 @督战官机器人 才回复。"""
    from app.duzhan import poll_at_mentions

    try:
        result = poll_at_mentions()
    except Exception as exc:  # noqa: BLE001 — 轮询异常要告警，不能静默
        logger.exception("督战官 @轮询失败: {}", exc)
        notify("督战官@轮询失败", str(exc)[:200])
        return
    replied = result.get("replied") or []
    if replied:
        logger.info("督战官已回 @ {}", replied)


def mto_temp_cleanup_job() -> None:
    """每日 03:30 — MTO 图片下载残留隔日清理（只清理 temp/mto-ocr-* 前缀）。"""
    from app.mto_ocr import cleanup_temp_files

    settings = get_settings()
    if not getattr(settings, "mto_temp_cleanup_enabled", True):
        logger.info("MTO 临时文件清理已关闭 (PDCA_MTO_TEMP_CLEANUP_ENABLED=0)")
        return
    try:
        result = cleanup_temp_files(getattr(settings, "mto_temp_max_age_hours", 6.0))
        logger.info(
            "MTO 临时文件清理完成 removed={} freed_bytes={}",
            result.get("removed"),
            result.get("freed_bytes"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("MTO 临时文件清理异常: {}", exc)
        notify("MTO 临时文件清理失败", str(exc)[:200])


def strategy_wa_brief_job() -> None:
    """每天 08:00：按 wa_strategies.json 核查前 24 小时群聊里的策略口径。

    换策略改 data/runtime/wa_strategies.json（没有则用 app/wa_strategies.json），不改代码。
    生成 HTML → 发共享名单（app/im_files）。失败不静默。
    """
    from app.im_files import resolve_channel, resolve_user_ids, send_files
    from app.scheduler.run_ledger import claim_run, finish_run
    from app.strategy_wa_brief import run_report

    settings = get_settings()
    day = datetime.now(ZoneInfo(TZ_SHANGHAI)).strftime("%Y-%m-%d")
    if not claim_run("campaign_wa_check", day):
        logger.info("策略核查本日已出，跳过 {}", day)
        return
    try:
        result = run_report(day)
    except Exception as exc:  # noqa: BLE001 — 采集/渲染失败要留痕并告警
        logger.exception("策略核查生成失败: {}", exc)
        finish_run("campaign_wa_check", day, "failed", f"{type(exc).__name__}: {exc}")
        notify("策略核查生成失败", str(exc)[:200])
        return
    try:
        channel = resolve_channel(settings, "strategy_wa_brief_channel_id")
        delivery = send_files(
            html_path=result["html"],
            user_ids=[] if channel else resolve_user_ids(settings, "strategy_wa_brief_user_ids"),
            channel_id=channel,
            caption=result.get("body") or "",
            idempotency_key="strategy-wa-" + day,
        )
    except Exception as exc:  # noqa: BLE001 — 发送异常必须记失败，下一轮才能补
        logger.exception("策略核查发送异常: {}", exc)
        finish_run("campaign_wa_check", day, "failed", f"{type(exc).__name__}: {exc}")
        notify("策略核查发送异常", str(exc)[:200])
        return
    failed = delivery.get("failed") or []
    sent = delivery.get("sent") or []
    if failed or not sent:
        detail = ",".join(failed) if failed else "无人收到"
        logger.warning("策略核查发送失败: {}", detail)
        finish_run("campaign_wa_check", day, "failed", detail[:200])
        notify("策略核查发送失败", detail[:200])
        return
    finish_run("campaign_wa_check", day, "sent", ",".join(sent)[:200])
    logger.info("策略核查完成 {}｜发送 {}", result.get("html"), sent)


#: 兼容旧任务名（原项目里这条任务叫 campaign_wa_check）
campaign_wa_check_job = strategy_wa_brief_job


def _cron_parts(times: list[str]) -> list[tuple[int, int]]:
    """把 ["10:00", "15:30"] 变成 [(10, 0), (15, 30)]。"""
    out: list[tuple[int, int]] = []
    for raw in times or []:
        parts = str(raw).split(":")
        try:
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
        except (TypeError, ValueError):
            logger.warning("档位写法无法解析，已跳过: {}", raw)
            continue
        out.append((hour % 24, minute % 60))
    return out


def start_scheduler() -> BackgroundScheduler | None:
    """启动后台调度器（只注册督战官相关任务）。"""
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("调度器已禁用 (PDCA_SCHEDULER_ENABLED=0)")
        return None
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler()

    from app.duzhan_admin import runtime as duzhan_runtime

    config_from_db = duzhan_runtime.using_db()

    if getattr(settings, "duzhan_enabled", False) and config_from_db:
        registered = duzhan_runtime.register_agent_jobs(_scheduler)
        logger.info("督战官配置源=db，注册子 Agent {} 个", len(registered))

    if getattr(settings, "heiwu_enabled", False):
        _scheduler.add_job(
            heiwu_poll_job,
            trigger="interval",
            minutes=int(getattr(settings, "heiwu_poll_minutes", 5) or 5),
            id="heiwu_poll",
        )
        logger.info("小黑屋祝贺机器人已注册：每 {} 分钟扫一轮", getattr(settings, "heiwu_poll_minutes", 5))

    if getattr(settings, "duzhan_enabled", False) and not config_from_db:
        from app.duzhan import collect_clock, cron_timezones, parse_hours

        lead = int(getattr(settings, "duzhan_lead_minutes", 15) or 15)
        times = _cron_parts(list(getattr(settings, "duzhan_times", ["10:00", "15:00", "20:00"])))
        for tz_name in cron_timezones():
            zone = ZoneInfo(tz_name)
            for hour, minute in times:
                collect_hour, collect_minute = collect_clock(hour, lead + minute)
                backup_hour, backup_minute = collect_clock(hour, -30 + minute)
                slug = _tz_slug(tz_name)
                _scheduler.add_job(
                    duzhan_collect_job,
                    args=[tz_name, hour],
                    trigger="cron",
                    hour=collect_hour,
                    minute=collect_minute,
                    day_of_week="mon-sun",
                    timezone=zone,
                    id=f"duzhan_collect_{slug}_{hour:02d}",
                )
                _scheduler.add_job(
                    duzhan_job,
                    args=[tz_name, hour],
                    trigger="cron",
                    hour=hour,
                    minute=minute,
                    day_of_week="mon-sun",
                    timezone=zone,
                    id=f"duzhan_{slug}_{hour:02d}",
                )
                _scheduler.add_job(
                    duzhan_job,
                    args=[tz_name, hour],
                    trigger="cron",
                    hour=backup_hour,
                    minute=backup_minute,
                    day_of_week="mon-sun",
                    timezone=zone,
                    id=f"duzhan_backup_{slug}_{hour:02d}",
                )
        if getattr(settings, "duzhan_reply_enabled", False):
            _scheduler.add_job(
                duzhan_at_poll_job,
                trigger="interval",
                minutes=1,
                id="duzhan_at_poll",
            )

    if getattr(settings, "ctob_enabled", False) and not config_from_db:
        follow_times = _cron_parts(list(getattr(settings, "ctob_times", ["10:00", "15:00", "20:00"])))
        for hour, minute in follow_times:
            backup = hour * 60 + minute + 30
            _scheduler.add_job(
                ctob_job,
                args=[hour],
                trigger="cron",
                hour=hour,
                minute=minute,
                day_of_week="mon-sun",
                timezone=ZoneInfo(TZ_SHANGHAI),
                id=f"ctob_{hour:02d}{minute:02d}",
            )
            _scheduler.add_job(
                ctob_job,
                args=[hour],
                trigger="cron",
                hour=(backup // 60) % 24,
                minute=backup % 60,
                day_of_week="mon-sun",
                timezone=ZoneInfo(TZ_SHANGHAI),
                id=f"ctob_backup_{(backup // 60) % 24:02d}{backup % 60:02d}",
            )

    if getattr(settings, "strategy_wa_brief_enabled", False):
        at = str(getattr(settings, "strategy_wa_brief_time", "08:00"))
        slots = _cron_parts([at]) or [(8, 0)]
        _scheduler.add_job(
            strategy_wa_brief_job,
            trigger="cron",
            hour=slots[0][0],
            minute=slots[0][1],
            day_of_week="mon-sun",
            timezone=ZoneInfo(TZ_SHANGHAI),
            id="campaign_wa_check",
        )

    _scheduler.add_job(
        mto_temp_cleanup_job,
        trigger="cron",
        hour=3,
        minute=30,
        day_of_week="mon-sun",
        timezone=ZoneInfo(TZ_SHANGHAI),
        id="mto_temp_cleanup",
    )

    _scheduler.start()
    logger.info(
        "调度器已启动：duzhan={}(+30m兜底) collect=-{}m at_poll={} ctob={}(+30m兜底) heiwu={} strategy_wa={}",
        getattr(settings, "duzhan_times", []) if getattr(settings, "duzhan_enabled", False) else "停用",
        getattr(settings, "duzhan_lead_minutes", 0) if getattr(settings, "duzhan_enabled", False) else 0,
        "1m" if getattr(settings, "duzhan_enabled", False) and getattr(settings, "duzhan_reply_enabled", False) else "停用",
        getattr(settings, "ctob_times", []) if getattr(settings, "ctob_enabled", False) else "停用",
        "开" if getattr(settings, "heiwu_enabled", False) else "停用",
        getattr(settings, "strategy_wa_brief_time", "") if getattr(settings, "strategy_wa_brief_enabled", False) else "停用",
    )
    return _scheduler


def stop_scheduler() -> None:
    """退出时停调度器，避免后台线程拖着进程不结束。"""
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    finally:
        _scheduler = None
