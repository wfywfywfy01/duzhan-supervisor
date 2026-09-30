# -*- coding: utf-8 -*-
"""每日经营日报：只发布可直接验证的核心数据。"""
from __future__ import annotations

import asyncio
import calendar as _calendar
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger
from sqlmodel import Session, select

from app.database import get_engine
from app.models.walkin_daily_report import WalkinDailyReport, latest_walkin_reports
from app.models.dealer_store import DealerStore, is_demo_store
from app.models.logistics import LogisticsShipment
from app.vertu.sales import fetch_dept_monthly_target, fetch_sell_in

# 业务确认的每日必报五件套门店清单（store_id → 名称兜底；展示名以门店主数据为准）
REQUIRED_FIVE_KIT_STORES: dict[str, str] = {
    "me005": "Dar Al Sabaek",
    "me011": "Safiran Hamrah",
    "sea02a": "VMG Communication and Technology JSC · Dong Khoi",
    "sea02b": "VMG Communication and Technology JSC · Caravelle",
    "sea02c": "VMG Communication and Technology JSC · Majestic",
    "sea02d": "VMG Communication and Technology JSC · REX",
}

_SALES_TARGETS_FILE = Path(__file__).with_name("monthly_sales_targets.json")


async def _fetch_live_sales_async(yesterday: str, day: str) -> tuple[dict, dict]:
    return await asyncio.gather(
        fetch_sell_in(yesterday, "day"),
        fetch_sell_in(day, "month"),
    )


def _fetch_live_sales(yesterday: str, day: str) -> tuple[dict, dict]:
    """直接查询销售事实源；历史快照不作为日报事实。"""
    return asyncio.run(_fetch_live_sales_async(yesterday, day))


def _validated_sales(payload: dict, label: str) -> tuple[float, int]:
    if payload.get("state") != "live":
        raise RuntimeError(f"{label} Sell-in 数据源不是实时状态")
    try:
        return float(payload["wan"]), int(payload["quantity"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} Sell-in 返回缺少金额或销量") from exc


def _time_progress(day: str) -> float:
    """本月已过天数 / 本月总天数（0~1）。"""
    value = date.fromisoformat(day)
    total_days = _calendar.monthrange(value.year, value.month)[1]
    return value.day / total_days


def _configured_sales_target(month: str) -> tuple[float, list[str]] | None:
    """读取已确认月目标；新部保持部门合计，不擅自拆到个人。"""
    payload = json.loads(_SALES_TARGETS_FILE.read_text(encoding="utf-8"))
    plan = payload.get(month)
    if plan is None:
        return None
    entries = plan.get("entries")
    declared_total = float(plan.get("department_target_wan"))
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{month} 月目标明细为空")
    actual_total = sum(float(row["target_wan"]) for row in entries)
    if actual_total != declared_total:
        raise ValueError(f"{month} 月目标合计不一致：{actual_total} != {declared_total}")
    details = []
    for row in entries:
        members = row.get("members") or []
        suffix = f"（{'、'.join(members)}合计）" if members else ""
        details.append(f"{row['name']}{suffix} {float(row['target_wan']):g} 万")
    return declared_total * 10000, details


def _as_of(payloads: tuple[dict, dict]) -> str:
    latest = max(
        (str(item.get("as_of") or "") for item in payloads if item.get("as_of")),
        default="",
    )
    if not latest:
        return "N/A"
    try:
        value = datetime.fromisoformat(latest)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return "N/A"


def build_report(day: str) -> str:
    yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    week_ago = (date.fromisoformat(day) - timedelta(days=7)).isoformat()
    sales = _fetch_live_sales(yesterday, day)
    yesterday_wan, yesterday_units = _validated_sales(sales[0], "昨日")
    month_wan, month_units = _validated_sales(sales[1], "本月")

    # 月度目标：目标查询失败时不阻塞日报（省略目标板块，仅记日志）。
    month_target_yuan: float | None = None
    target_details: list[str] = []
    try:
        configured = _configured_sales_target(day[:7])
        if configured:
            month_target_yuan, target_details = configured
        else:
            year, month_number = int(day[:4]), int(day[5:7])
            month_end = f"{day[:7]}-{_calendar.monthrange(year, month_number)[1]:02d}"
            month_target_yuan = fetch_dept_monthly_target(f"{day[:7]}-01", month_end)
    except Exception as exc:  # noqa: BLE001 — 目标板块是增强项，失败可降级
        logger.warning("月度目标查询失败，日报省略目标板块: {}", exc)

    with Session(get_engine()) as session:
        reports = latest_walkin_reports(session.exec(
            select(WalkinDailyReport).where(WalkinDailyReport.report_date == yesterday)
        ).all())
        required_stores = session.exec(
            select(DealerStore).where(DealerStore.store_id.in_(REQUIRED_FIVE_KIT_STORES))
        ).all()
        logistics_rows = session.exec(
            select(LogisticsShipment).where(LogisticsShipment.record_date >= week_ago)
        ).all()

    reported_ids = {
        row.dealer_id
        for row in reports
        if row.dealer_id
        and not is_demo_store(row.dealer_id, row.dealer_name)
    }
    required_reported_ids = reported_ids.intersection(REQUIRED_FIVE_KIT_STORES)

    name_by_id = {store.store_id: store.name for store in required_stores}
    for sid, fallback_name in REQUIRED_FIVE_KIT_STORES.items():
        name_by_id.setdefault(sid, fallback_name)
    missing_ids = [sid for sid in REQUIRED_FIVE_KIT_STORES if sid not in required_reported_ids]
    missing_names = [name_by_id[sid] for sid in missing_ids]

    # 物流：近 7 天在途/异常（复用物流服务的统一判定）
    from app.logistics.service import _is_delivered, _judge_status, _load_settings

    transit = abnormal = 0
    settings_cfg = _load_settings()
    for row in logistics_rows:
        row_dict = {
            "current_status": row.current_status or "",
            "status": row.current_status or "",
            "ship_date": row.ship_date or day,
            "progress_pct": row.progress_pct,
        }
        judgement, _reason, _progress = _judge_status(row_dict, settings_cfg, day)
        if judgement == "异常":
            abnormal += 1
            continue
        if _is_delivered(row_dict):
            continue
        transit += 1

    target_lines: list[str] = []
    if month_target_yuan and month_target_yuan > 0:
        completion_pct = month_wan * 10000.0 / month_target_yuan * 100.0
        progress_pct = _time_progress(day) * 100.0
        gap_pp = completion_pct - progress_pct
        target_lines = [
            "【业绩目标（本月）】",
            f"· 目标 {month_target_yuan / 10000:,.1f} 万 · 实际 {month_wan:,.2f} 万 · 完成率 {completion_pct:.1f}%",
            (
                f"· 时间进度 {progress_pct:.1f}% · "
                f"{'领先' if gap_pp >= 0 else '落后'} {abs(gap_pp):.1f} 个百分点"
            ),
        ]
        if target_details:
            target_lines.append(f"· 明细：{'｜'.join(target_details)}")

    five_kit_lines = [
        f"【门店五件套回执（{yesterday[5:]}）】",
        f"· 系统收到 {len(required_reported_ids)} 家必报门店填报",
        f"· 应报 {len(REQUIRED_FIVE_KIT_STORES)} 家",
    ]
    if missing_ids:
        five_kit_lines.append(f"· 缺报 {len(missing_ids)} 家：{'、'.join(missing_names)}")
    else:
        five_kit_lines.append("· 应报门店已全部上报 ✅")

    return "\n".join(
        [
            f"📊 PDCA 核心日报 {day}",
            "",
            "【Sell-in｜Vertu 实时查询】",
            f"· 昨日（{yesterday[5:]}）：{yesterday_wan:,.2f} 万 · {yesterday_units} 台",
            f"· 本月累计：{month_wan:,.2f} 万 · {month_units} 台",
            "",
            *target_lines,
            "",
            *five_kit_lines,
            "",
            f"【物流】近 7 天在途 {transit} 单 · 异常 {abnormal} 单",
            "",
            f"数据截至：{_as_of(sales)}",
            "入口：https://pdca.example.com/app/",
        ]
    )
