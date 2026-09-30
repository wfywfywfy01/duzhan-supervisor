# -*- coding: utf-8 -*-
"""通过 vertu-cli 2.x 的业务快捷命令读取销售数据。"""
from __future__ import annotations

import asyncio
import math
import os
import time
from datetime import date as _date, datetime, timedelta

from app.vertu.client import run_vertu_json, run_vertu_sync_json


# Sell-in is requested on every workbench visit. Keep a short, explicitly
# timestamped cache so repeated visits do not start three identical CLI
# processes. Failed refreshes never overwrite the last successful fact.
_SELL_IN_CACHE: dict[tuple, dict] = {}
_SELL_IN_LOCKS: dict[tuple, asyncio.Lock] = {}


def _sell_in_cache_seconds() -> float:
    try:
        return max(float(os.environ.get("PDCA_SELL_IN_CACHE_SECONDS", "60")), 0.0)
    except ValueError:
        return 60.0


def _date_range(date_text: str, period: str) -> tuple[str, str]:
    today = _date.fromisoformat(date_text) if date_text else _date.today()
    if period in ("day", "today"):
        return str(today), str(today)
    if period == "week":
        return str(today - timedelta(days=today.weekday())), str(today)
    if period == "month":
        return str(today.replace(day=1)), str(today)
    if period == "quarter":
        quarter_month = ((today.month - 1) // 3) * 3 + 1
        return str(today.replace(month=quarter_month, day=1)), str(today)
    return str(today), str(today)


def _trend_months(month: str) -> list[dict[str, str]]:
    year, number = int(month[:4]), int(month[5:7])
    result: list[dict[str, str]] = []
    for offset in range(5, -1, -1):
        current = number - offset
        current_year = year
        while current <= 0:
            current += 12
            current_year -= 1
        next_month = current + 1 if current < 12 else 1
        next_year = current_year if current < 12 else current_year + 1
        result.append(
            {
                "label": f"{current_year:04d}-{current:02d}",
                "start": f"{current_year:04d}-{current:02d}-01",
                "end": str(_date(next_year, next_month, 1) - timedelta(days=1)),
            }
        )
    return result


_PERIOD_LABEL = {
    "day": "今日",
    "today": "今日",
    "week": "本周",
    "month": "本月",
    "quarter": "本季度",
}


async def _headline(start: str, end: str, department: str = "") -> dict:
    args = [
        "sales",
        "+headline-kpi",
        "--start-date",
        start,
        "--end-date",
        end,
    ]
    dept_l1 = os.environ.get("PDCA_VERTU_DEPT_L1", "海外渠道").strip()
    if dept_l1:
        args += ["--dept-l1", dept_l1]
    if department:
        args += ["--dept-l2", department]
    payload = await run_vertu_json(args, timeout=45.0)
    if not isinstance(payload, dict):
        raise RuntimeError("vertu-cli sales +headline-kpi 未返回 JSON")
    return payload


async def fetch_sell_in(date_text: str, period: str = "day") -> dict:
    """Sell-in KPI：按配置的经销商部门聚合 vertu-cli 销售口径。"""
    start, end = _date_range(date_text, period)
    configured = os.environ.get(
        "PDCA_VERTU_SELLIN_DEPARTMENTS",
        "经销商一部,经销商二部,经销商三部",
    )
    departments = list(dict.fromkeys(item.strip() for item in configured.split(",") if item.strip()))
    if not departments:
        raise RuntimeError("目标数据不完整：未配置经销商部门")
    dept_l1 = os.environ.get("PDCA_VERTU_DEPT_L1", "海外渠道").strip()
    cache_key = (start, end, period, dept_l1, tuple(departments))
    ttl = _sell_in_cache_seconds()

    def cached_value(now: float) -> dict | None:
        entry = _SELL_IN_CACHE.get(cache_key)
        if entry and now - float(entry["monotonic"]) < ttl:
            return {**entry["data"], "cached": True}
        return None

    cached = cached_value(time.monotonic())
    if cached is not None:
        return cached

    # Single-flight identical requests while still allowing the independent
    # department calls to run concurrently.
    lock = _SELL_IN_LOCKS.setdefault(cache_key, asyncio.Lock())
    async with lock:
        cached = cached_value(time.monotonic())
        if cached is not None:
            return cached

        if departments:
            payloads = await asyncio.gather(
                *(_headline(start, end, department) for department in departments)
            )
        else:
            payloads = [await _headline(start, end)]

        amounts, quantities = [], []
        for item in payloads:
            try:
                metrics = item["period"]
                amount_value = float(metrics["销额"])
                quantity_value = float(metrics["销量"])
                if not math.isfinite(amount_value) or not math.isfinite(quantity_value) or not quantity_value.is_integer():
                    raise ValueError("invalid numeric metric")
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise RuntimeError("vertu-cli Sell-in 缺少有效的销额/销量，拒绝发布为实时零值") from exc
            amounts.append(amount_value)
            quantities.append(int(quantity_value))
        amount = sum(amounts)
        quantity = sum(quantities)
        label = _PERIOD_LABEL.get(period, "当前区间")
        fetched_at = datetime.now().astimezone()
        result = {
            "amount": amount,
            "wan": round(amount / 10000, 2),
            "quantity": quantity,
            "note": f"{label}实时 · vertu-cli sales · {fetched_at:%H:%M:%S}更新",
            "as_of": fetched_at.isoformat(timespec="seconds"),
            "cached": False,
            "state": "live",
            "source": "vertu-cli sales +headline-kpi",
            "start_date": start,
            "end_date": end,
        }
        _SELL_IN_CACHE[cache_key] = {
            "monotonic": time.monotonic(),
            "data": result,
        }
        return dict(result)


async def fetch_sell_out(date_text: str, period: str = "day") -> dict:
    """vertu-cli 2.x 暂无代理商终端 Sell-out 快捷命令，交由本地实报数据兜底。"""
    del date_text, period
    raise RuntimeError("vertu-cli 暂未提供 dealer sell-out 数据源")


def _row_dict(row, columns: list[str]) -> dict:
    if isinstance(row, dict):
        return row
    if isinstance(row, list):
        return {columns[index]: value for index, value in enumerate(row) if index < len(columns)}
    return {}


def require_sales_number(value, field: str, *, integer: bool = False) -> float | int:
    """Missing/redacted metrics must fail the batch, never become zero."""
    try:
        if value is None or isinstance(value, bool):
            raise ValueError("missing metric")
        number = float(value)
        if not math.isfinite(number) or (integer and not number.is_integer()):
            raise ValueError("invalid metric")
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"销售数据缺少有效{field}，整批未保存，请检查上游权限与字段") from exc
    return int(number) if integer else number


def fetch_dealer_sales_orders_sync(start: str, end: str) -> dict:
    """通过 vertu-cli 订单快捷命令按客户聚合经销商销售数据。"""
    payload = run_vertu_sync_json(
        [
            "sales",
            "+orders",
            "--start-date",
            start,
            "--end-date",
            end,
            "--dept-l1",
            os.environ.get("PDCA_VERTU_DEPT_L1", "海外渠道"),
            "--limit",
            "5000",
        ],
        timeout=90.0,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("vertu-cli sales +orders 未返回数据")
    if not isinstance(payload.get("rows"), list):
        raise RuntimeError("vertu-cli sales +orders 缺少订单行，整批未保存")
    if (payload.get("pagination") or {}).get("has_more"):
        raise RuntimeError("vertu-cli sales +orders 分页未完整，整批未保存")
    columns = [str(item) for item in payload.get("columns") or []]
    grouped: dict[str, dict] = {}
    for raw in payload.get("rows") or []:
        row = _row_dict(raw, columns)
        amount = require_sales_number(row.get("金额"), "金额")
        quantity = require_sales_number(row.get("数量"), "数量", integer=True)
        name = str(row.get("客户名称") or row.get("客户") or "").strip()
        if not name:
            raise RuntimeError("销售订单缺少客户标识，整批未保存")
        item = grouped.setdefault(name, {"dealer_name": name, "sell_out_yuan": 0.0, "qty": 0})
        item["sell_out_yuan"] += amount
        item["qty"] += quantity
    dealers = sorted(grouped.values(), key=lambda item: -item["sell_out_yuan"])
    return {
        "ok": True,
        "start_date": start,
        "end_date": end,
        "month": end[:7],
        "total": round(sum(float(item["sell_out_yuan"]) for item in dealers), 2),
        "dealers": dealers,
        "source": "vertu-cli sales +orders",
        "source_metric": "dealer_sales",
    }


async def _orders(start: str, end: str) -> dict:
    payload = await run_vertu_json(
        [
            "sales",
            "+orders",
            "--start-date",
            start,
            "--end-date",
            end,
            "--limit",
            "5000",
        ],
        timeout=60.0,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("vertu-cli sales +orders 未返回 JSON")
    return payload


async def fetch_sellin_summary(month: str | None = None) -> dict:
    """按客户汇总当月 Sell-in，并给出最近六个月趋势。"""
    month = month or _date.today().strftime("%Y-%m")
    months = _trend_months(month)
    current = await _orders(months[-1]["start"], months[-1]["end"])
    columns = [str(item) for item in current.get("columns") or []]
    grouped: dict[str, dict] = {}
    for raw in current.get("rows") or []:
        row = _row_dict(raw, columns)
        name = str(row.get("客户名称") or row.get("客户") or "").strip()
        if not name:
            continue
        item = grouped.setdefault(name, {"name": name, "amount": 0.0, "quantity": 0})
        item["amount"] += float(row.get("金额") or 0)
        item["quantity"] += int(float(row.get("数量") or 0))
    ordered = sorted(grouped.values(), key=lambda item: -item["amount"])
    dealers = [
        {
            "rank": index + 1,
            "name": item["name"],
            "wan": round(item["amount"] / 10000, 2),
            "quantity": item["quantity"],
        }
        for index, item in enumerate(ordered[:50])
    ]

    trend: list[dict] = []
    for item in months:
        payload = current if item is months[-1] else await _orders(item["start"], item["end"])
        amount = float((payload.get("summary") or {}).get("amount") or 0)
        trend.append({"month": item["label"], "wan": round(amount / 10000, 2)})
    total = sum(float(item["amount"]) for item in ordered)
    return {
        "month": month,
        "total_wan": round(total / 10000, 2),
        "dealers": dealers,
        "has_data": bool(dealers),
        "trend": trend,
        "source": "vertu-cli sales +orders",
    }


async def _dept_target_async(month_start: str, month_end: str) -> float:
    """月度业绩目标合计（元）：按配置的经销商部门逐部求和（全月口径）。

    vertu-cli sales +target-achievement 的 target_amount 按窗口天数折算，
    因此这里传整月窗口（月初~月末）拿到全月目标。
    """
    configured = os.environ.get(
        "PDCA_VERTU_SELLIN_DEPARTMENTS",
        "经销商一部,经销商二部,经销商三部",
    )
    departments = list(dict.fromkeys(item.strip() for item in configured.split(",") if item.strip()))
    dept_l1 = os.environ.get("PDCA_VERTU_DEPT_L1", "海外渠道").strip()
    total = 0.0
    for department in departments:
        payload = await run_vertu_json(
            [
                "sales",
                "+target-achievement",
                "--start-date",
                month_start,
                "--end-date",
                month_end,
                "--dept-l1",
                dept_l1,
                "--dept-l2",
                department,
            ],
            timeout=45.0,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("vertu-cli sales +target-achievement 未返回 JSON")
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            raise RuntimeError(f"目标数据不完整：{department} 无有效记录")
        for row in rows:
            if not isinstance(row, dict) or row.get("target_amount") is None:
                raise RuntimeError(f"目标数据不完整：{department} 缺少 target_amount")
            try:
                value = float(row["target_amount"])
            except (TypeError, ValueError):
                raise RuntimeError(f"目标数据不完整：{department} target_amount 非数值") from None
            if not math.isfinite(value) or value < 0:
                raise RuntimeError(f"目标数据不完整：{department} target_amount 非法")
            total += value
    return total


def fetch_dept_monthly_target(month_start: str, month_end: str) -> float:
    """同步包装：查询本月业绩目标合计（元）。"""
    return asyncio.run(_dept_target_async(month_start, month_end))
