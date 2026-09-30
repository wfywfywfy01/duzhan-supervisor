# -*- coding: utf-8 -*-
"""中国工作日日历：法定节假日 + 调休上班日 + 长假。

老板 2026-09-20 要求：工作日（含周末调休上班）都要推送；长假照样推送但不处罚。
数据源 app/holidays_cn.json（每年国务院通知发布后人工更新一次）；缺年份时退回
周一~周五近似处理并告警——调休绝不编造。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from loguru import logger

CALENDAR_FILE = Path(__file__).with_name("holidays_cn.json")
_WARNED_YEARS: set[str] = set()


def _payload() -> dict:
    try:
        data = json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("节假日日历读取失败（{}），按周一~周五处理: {}", CALENDAR_FILE, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _year_block(day: str) -> dict:
    payload = _payload()
    block = payload.get(day[:4])
    if block is None:
        if day[:4] not in _WARNED_YEARS:
            _WARNED_YEARS.add(day[:4])
            logger.warning("节假日日历缺 {} 年安排，调休/长假按周一~周五近似处理", day[:4])
        return {}
    return block if isinstance(block, dict) else {}


def _weekday(day: str) -> int | None:
    try:
        return datetime.strptime(day, "%Y-%m-%d").weekday()
    except ValueError:
        return None


def long_holiday_name(day: str) -> str:
    for item in _year_block(day).get("long_holidays") or []:
        if not isinstance(item, dict):
            continue
        start, end = str(item.get("start") or ""), str(item.get("end") or "")
        if start and end and start <= day <= end:
            return str(item.get("name") or "长假")
    return ""


def day_kind(day: str) -> str:
    weekday = _weekday(day)
    if weekday is None:
        return "rest"
    block = _year_block(day)
    if day in (block.get("workdays") or {}):
        return "makeup"
    if long_holiday_name(day):
        return "long_holiday"
    if day in (block.get("holidays") or {}):
        return "rest"
    return "workday" if weekday < 5 else "rest"


def is_workday(day: str) -> bool:
    return day_kind(day) in ("workday", "makeup", "long_holiday")


def penalty_exempt(day: str) -> bool:
    return bool(long_holiday_name(day))


def calendar_note(day: str) -> str:
    kind = day_kind(day)
    block = _year_block(day)
    if kind == "makeup":
        return "今日为调休上班日（" + str((block.get("workdays") or {}).get(day) or "调休") + "），按工作日执行"
    if kind == "long_holiday":
        return long_holiday_name(day) + "假期期间：照常推送进度，本档不处罚（不列黑榜、不记扣罚）"
    if kind == "rest" and day in (block.get("holidays") or {}):
        return "今日为法定假日（" + str((block.get("holidays") or {}).get(day)) + "），不推送"
    return ""
