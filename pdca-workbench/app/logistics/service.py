# -*- coding: utf-8 -*-
"""物流数据加载、判断与按销售过滤。"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.auth.scope import normalize_scope_key
from app.config import get_settings
from app.legacy import bridge

JUDGEMENT_ORDER = {
    "异常": 0,
    "待关注": 1,
    "待核查": 2,
    "运输中": 3,
    "正常": 4,
}

STATUS_GROUPS = {
    "attention": {"异常", "待关注"},
    "transit": {"运输中", "待核查"},
    "delivered": set(),
}

_PLACEHOLDER_TRACKING_NUMBERS = {"1Z0000000000000000"}


def _is_demo_record(row: dict) -> bool:
    """识别明确的演示/占位运单，避免测试数据进入生产看板。"""
    tracking = str(row.get("tracking_number") or "").strip().upper()
    if tracking in _PLACEHOLDER_TRACKING_NUMBERS:
        return True
    labels = " ".join(
        str(row.get(key) or "")
        for key in ("customer", "note", "salesperson")
    ).casefold()
    return bool(re.search(r"(?:演示运单|演示客户|测试客户)|\b(?:demo|mock)[-_ ]?(?:shipment|customer|tracking)\b", labels))


def _read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _load_carriers() -> dict:
    path = get_settings().config_dir / "carriers.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _tracking_url(carriers: dict, carrier: str, tracking_number: str) -> str:
    info = carriers.get(carrier) or carriers.get((carrier or "").upper()) or {}
    template = info.get("tracking_url", "")
    return template.replace("{tracking_number}", tracking_number) if template else ""


def _load_settings() -> dict:
    path = get_settings().config_dir / "settings.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _status_is_delivered(status: str) -> bool:
    value = (status or "").strip().lower()
    if not value:
        return False
    if re.search(r"\b(?:not\s+delivered|undelivered)\b|未(?:签收|送达)", value):
        return False
    return (
        "delivered" in value
        or "已签收" in value
        or "签收完成" in value
        or "已送达" in value
    )


def _is_delivered(row: dict) -> bool:
    """是否视为已签收。"""
    status = (row.get("current_status") or row.get("status") or "").lower()
    if row.get("progress_pct", 0) >= 100:
        return True
    return _status_is_delivered(status)


def _days_since_ship(ship_date: str, ref_date: str) -> int | None:
    """发货至参考日的天数。"""
    try:
        ship_dt = datetime.strptime((ship_date or "")[:10], "%Y-%m-%d")
        ref_dt = datetime.strptime((ref_date or bridge.today_text())[:10], "%Y-%m-%d")
        return max(0, (ref_dt - ship_dt).days)
    except ValueError:
        return None


def _judge_status(row: dict, settings: dict, date_text: str) -> tuple[str, str, int]:
    """返回 judgement, reason, progress_pct。"""
    status = (row.get("current_status") or row.get("status") or "").strip()
    status_lower = status.lower()
    logistics_cfg = settings.get("logistics", {})
    for keyword in logistics_cfg.get("abnormal_keywords", []):
        if keyword.lower() in status_lower:
            return "异常", f"状态包含异常关键词：{keyword}", 20
    if _status_is_delivered(status):
        return "正常", "已签收或送达", 100
    ship_date = (row.get("ship_date") or date_text or "")[:10]
    try:
        ship_dt = datetime.strptime(ship_date, "%Y-%m-%d")
        run_dt = datetime.strptime(date_text, "%Y-%m-%d")
        if (run_dt - ship_dt).days >= 7:
            return "待关注", "发货超过 7 天，且未标记签收", 45
    except ValueError:
        pass
    for keyword in logistics_cfg.get("normal_keywords", []):
        if keyword.lower() in status_lower:
            return "正常", f"状态包含正常关键词：{keyword}", 70
    if status:
        return "运输中", "在途更新", 65
    return "待核查", "未填写当前状态", 30


def _sales_aliases() -> dict[str, str]:
    path = get_settings().config_dir / "sales_aliases.csv"
    aliases: dict[str, str] = {}
    if not path.is_file():
        return aliases
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                raw = (row.get("raw_sales") or "").strip()
                canonical = (row.get("canonical_sales") or "").strip()
                if raw and canonical:
                    aliases[raw.lower()] = canonical
                    aliases[canonical.lower()] = canonical
    except OSError:
        pass
    return aliases


def canonical_sales_name(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    aliases = _sales_aliases()
    return aliases.get(text.lower(), text)


def resolve_sales_filter(
    role: str,
    sales_name: str,
    display_name: str,
    username: str,
    salesperson_query: str = "",
) -> str | None:
    """
    解析销售过滤名。

    sales 角色只能看自己的；manager/admin 可看全部或指定销售。
    """
    if role == "sales":
        return canonical_sales_name(sales_name or display_name or username)
    if salesperson_query.strip():
        return canonical_sales_name(salesperson_query.strip())
    return None


def _match_sales(row_sales: str, filter_sales: str | None) -> bool:
    if not filter_sales:
        return True
    row_canon = canonical_sales_name(row_sales)
    return row_canon == filter_sales or (row_sales or "").strip() == filter_sales


def _match_search(row: dict, query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    haystack = " ".join(
        [
            row.get("tracking_number", ""),
            row.get("carrier", ""),
            row.get("customer", ""),
            row.get("salesperson", ""),
            row.get("note", ""),
            row.get("current_status", ""),
        ],
    ).lower()
    return q in haystack


def _match_status_group(row: dict, status_group: str) -> bool:
    group = (status_group or "all").strip().lower()
    if group in ("", "all"):
        return True
    if group == "delivered":
        return _is_delivered(row)
    if group == "attention":
        return row.get("judgement") in STATUS_GROUPS["attention"]
    if group == "transit":
        return not _is_delivered(row) and row.get("judgement") not in STATUS_GROUPS["attention"]
    return row.get("judgement") == status_group


def _sort_shipments(rows: list[dict]) -> list[dict]:
    """异常优先，其次发货日倒序。"""
    return sorted(
        rows,
        key=lambda r: (
            JUDGEMENT_ORDER.get(r.get("judgement", "待核查"), 9),
            0 if _is_delivered(r) else -1,
            -(r.get("days_in_transit") or 0),
            r.get("ship_date", ""),
            r.get("record_date", ""),
        ),
    )


def _apply_auto_status(enriched: dict, auto_map: dict[str, "object"]) -> None:
    """用官网自动抓取结果覆盖人工录入的 current_status（仅 UPS/FedEx/DHL）。"""
    tracking = (enriched.get("tracking_number") or "").strip()
    auto = auto_map.get(tracking) if tracking else None
    if not auto or not getattr(auto, "fetch_ok", False):
        return
    enriched["current_status"] = auto.status_text
    enriched["status_source"] = "auto"
    enriched["status_fetched_at"] = auto.fetched_at.isoformat() if auto.fetched_at else ""
    enriched.pop("judgement", None)  # 强制按最新抓取状态重新判定
    if auto.is_delivered:
        enriched["is_delivered_override"] = True


def _enrich_row(
    row: dict,
    carriers: dict,
    cfg: dict,
    file_date: str,
    ref_date: str,
    auto_map: dict[str, "object"] | None = None,
) -> dict:
    enriched = dict(row)
    tracking = (enriched.get("tracking_number") or "").strip()
    enriched["record_date"] = file_date
    enriched["tracking_url"] = enriched.get("tracking_url") or _tracking_url(
        carriers,
        enriched.get("carrier", ""),
        tracking,
    )
    enriched.setdefault("status_source", "manual")
    if auto_map:
        _apply_auto_status(enriched, auto_map)
    # 判定永远按真实今天重新算，不能只在"没有已存判定"时才算一次——
    # 否则录入批次当天算出的判定（比如 7 天预警）会被永久冻结在 outputs 里的旧结果 CSV 中，
    # 不会因为时间推移而重新升级/降级
    judgement, reason, progress = _judge_status(enriched, cfg, ref_date)
    enriched["judgement"] = judgement
    enriched["reason"] = reason
    enriched["progress_pct"] = progress
    enriched["is_delivered"] = _is_delivered(enriched) or bool(enriched.get("is_delivered_override"))
    enriched["days_in_transit"] = _days_since_ship(enriched.get("ship_date", ""), ref_date)
    enriched["check_report_path"] = check_report_relative_path(file_date)
    enriched["check_report_exists"] = bool(enriched["check_report_path"])
    return enriched


def create_shipment(
    date_text: str,
    fields: dict,
    salesperson: str = "",
    allowed_salespeople: set[str] | None = None,
) -> str:
    """P2：物流单号录入统一入口（新 JSON API 与旧表单 POST 共用）。

    - 写 DB（logistics_shipments）：读取侧事实源；
    - 同步追加 CSV（inputs/logistics）：保留 legacy 日流水线兼容；
    - sales 角色由调用方锁定 salesperson。
    返回运单号；非法输入抛 ValueError。
    fields 值兼容两种形态：标量（JSON API）与 list（form_to_legacy 表单）。
    """
    form: dict[str, list[str]] = {}
    for key, value in (fields or {}).items():
        if isinstance(value, (list, tuple)):
            form[str(key)] = [str(item or "") for item in value]
        else:
            form[str(key)] = [str(value or "")]
    tracking = (form.get("tracking_number", [""])[0] or "").strip()
    if not tracking:
        raise ValueError("物流单号不能为空")
    if allowed_salespeople is not None:
        allowed_salespeople = {
            normalize_scope_key(canonical_sales_name(value))
            for value in allowed_salespeople
            if normalize_scope_key(value)
        }
    from app.legacy import bridge
    from app.models import writes as db_writes

    if allowed_salespeople is not None:
        inputs_dir = get_settings().mvp_root / "inputs" / "logistics"
        if inputs_dir.is_dir():
            historical = None
            for csv_path in sorted(inputs_dir.glob("*_tracking.csv"), reverse=True):
                historical = next((
                    row for row in _read_csv(csv_path)
                    if (row.get("tracking_number") or "").strip() == tracking
                ), None)
                if historical is not None:
                    break
            if historical is not None:
                historical_owner = normalize_scope_key(
                    canonical_sales_name(historical.get("salesperson", ""))
                )
                if not historical_owner or historical_owner not in allowed_salespeople:
                    raise PermissionError("该运单不在当前账号权限范围内")

    db_writes.upsert_logistics_shipment(
        date_text,
        form,
        canonical_sales_name(
            salesperson or (form.get("salesperson", [""])[0] or "").strip()
        ),
        allowed_salespeople=allowed_salespeople,
    )
    try:
        bridge.append_logistics(date_text, form)
    except Exception as exc:
        # The committed DB row remains authoritative if legacy export is unavailable.
        logger.warning("物流已保存到数据库，CSV 兼容导出失败: {}", exc)
    return tracking


def list_available_dates() -> list[str]:
    """有物流录入数据的日期列表（新→旧）：DB record_date ∪ CSV 批次。"""
    dates: set[str] = set()
    try:
        from sqlmodel import Session, select
        from app.database import get_engine
        from app.models.logistics import LogisticsShipment

        with Session(get_engine()) as session:
            rows = session.exec(select(LogisticsShipment.record_date)).all()
            dates.update(str(d) for d in rows if d)
    except Exception as exc:
        logger.warning("读取物流 DB 日期失败: {}", exc)

    inputs_dir = get_settings().mvp_root / "inputs" / "logistics"
    if inputs_dir.is_dir():
        for csv_path in inputs_dir.glob("*_tracking.csv"):
            rows = _read_csv(csv_path)
            if any(
                (r.get("tracking_number") or "").strip()
                and (get_settings().include_demo_data or not _is_demo_record(r))
                for r in rows
            ):
                dates.add(csv_path.stem.replace("_tracking", ""))
    return sorted(dates, reverse=True)


def check_report_relative_path(date_text: str) -> str:
    """核查报告相对 MVP 根路径。"""
    path = get_settings().mvp_root / "outputs" / date_text / "logistics_check_report.md"
    if path.is_file():
        return f"outputs/{date_text}/logistics_check_report.md"
    return ""


def load_auto_status_map() -> dict[str, object]:
    """读取 tracking_auto_status 表，按运单号返回最新抓取记录。"""
    try:
        from sqlmodel import Session, select
        from app.database import get_engine
        from app.models.tracking_status import TrackingAutoStatus

        with Session(get_engine()) as session:
            rows = session.exec(select(TrackingAutoStatus)).all()
            return {r.tracking_number: r for r in rows}
    except Exception as exc:
        logger.warning("读取自动物流状态失败: {}", exc)
        return {}


def _load_db_rows(date_text: str | None = None) -> list[dict] | None:
    """P2：从 logistics_shipments 表读取运单（DB 唯一事实源）。

    date_text 为 None/"all" 时返回全量；否则只返回该录入批次日。行结构
    与 CSV 路径一致（_enrich_row 统一补充判定/URL/在途天数）。
    """
    try:
        from sqlmodel import Session, select
        from app.database import get_engine
        from app.models.logistics import LogisticsShipment

        stmt = select(LogisticsShipment)
        if date_text and date_text != "all":
            stmt = stmt.where(LogisticsShipment.record_date == date_text)
        with Session(get_engine()) as session:
            rows = list(session.exec(stmt).all())
    except Exception as exc:
        logger.warning("读取 logistics_shipments 失败: {}", exc)
        return None

    result = []
    for row in rows:
        result.append(
            {
                "record_date": row.record_date,
                "synced_at": row.synced_at.isoformat() if row.synced_at else "",
                "data_source": "logistics_db",
                "tracking_number": row.tracking_number,
                "carrier": row.carrier,
                "customer": row.customer,
                "salesperson": row.salesperson,
                "ship_date": row.ship_date,
                "expected_status": row.expected_status,
                "current_status": row.current_status,
                "note": row.note,
                "tracking_url": row.tracking_url,
                "judgement": row.judgement,
                "reason": row.reason,
                "progress_pct": row.progress_pct,
            }
        )
    return result


def load_shipments(
    date_text: str | None = None,
    salesperson: str | None = None,
    status_group: str = "all",
    query: str = "",
    open_only: bool = False,
    *,
    return_state: bool = False,
) -> list[dict] | tuple[list[dict], str]:
    """合并运单数据：DB（logistics_shipments）优先，CSV 历史兜底。

    P2 起新录入直接落库（POST /logistics → upsert_logistics_shipment）；
    CSV 仅作为历史批次与未迁移数据的补充源，同一运单号以 DB 为准。
    """
    settings = get_settings()
    inputs_dir = settings.mvp_root / "inputs" / "logistics"
    outputs_dir = settings.mvp_root / "outputs"
    carriers = _load_carriers()
    cfg = _load_settings()
    # 在途天数/7天预警的参照点永远是真实今天
    ref_date = bridge.today_text()
    auto_map = load_auto_status_map()
    merged: dict[str, dict] = {}

    # 1) DB 行（最高优先）
    db_rows = _load_db_rows(date_text)
    for row in db_rows or []:
        tracking = (row.get("tracking_number") or "").strip()
        if not tracking:
            continue
        if not settings.include_demo_data and _is_demo_record(row):
            continue
        merged[tracking] = _enrich_row(
            row, carriers, cfg, row.get("record_date") or ref_date, ref_date, auto_map
        )

    # 2) CSV 历史（只补充 DB 没有的运单）
    if inputs_dir.is_dir():
        for csv_path in sorted(inputs_dir.glob("*_tracking.csv"), reverse=True):
            file_date = csv_path.stem.replace("_tracking", "")
            if date_text and date_text != "all" and file_date != date_text:
                continue
            results_path = outputs_dir / file_date / f"{file_date}_logistics_results.csv"
            results_by_tracking = {
                (r.get("tracking_number") or "").strip(): r
                for r in _read_csv(results_path)
            }
            for row in _read_csv(csv_path):
                tracking = (row.get("tracking_number") or "").strip()
                if not tracking or tracking in merged:
                    continue
                if not settings.include_demo_data and _is_demo_record(row):
                    continue
                enriched = dict(row)
                if tracking in results_by_tracking:
                    enriched.update(results_by_tracking[tracking])
                enriched["data_source"] = "csv_history"
                merged[tracking] = _enrich_row(
                    enriched, carriers, cfg, file_date, ref_date, auto_map
                )

    has_csv = any(row.get("data_source") == "csv_history" for row in merged.values())
    source_state = ("degraded" if has_csv else "missing") if db_rows is None else (
        ("mixed" if db_rows else "historical") if has_csv else "available"
    )
    rows = list(merged.values())
    if salesperson:
        rows = [r for r in rows if _match_sales(r.get("salesperson", ""), salesperson)]
    if open_only:
        rows = [r for r in rows if not r.get("is_delivered")]
    rows = [r for r in rows if _match_status_group(r, status_group)]
    rows = [r for r in rows if _match_search(r, query)]
    rows = _sort_shipments(rows)
    return (rows, source_state) if return_state else rows


def build_summary(shipments: list[dict]) -> dict:
    """汇总物流进展统计。"""
    total = len(shipments)
    delivered = sum(1 for s in shipments if s.get("is_delivered"))
    attention = [s for s in shipments if s.get("judgement") in STATUS_GROUPS["attention"]]
    in_transit = sum(
        1
        for s in shipments
        if not s.get("is_delivered")
        and s.get("judgement") in ("运输中", "正常", "待核查")
    )
    abnormal = len(attention)
    pending = sum(1 for s in shipments if s.get("judgement") == "待核查" and not s.get("is_delivered"))
    rate = round(delivered / total * 100) if total else 0
    return {
        "total": total,
        "delivered": delivered,
        "in_transit": in_transit,
        "abnormal": abnormal,
        "pending": pending,
        "open": total - delivered,
        "delivery_rate_pct": rate,
        "attention_items": [
            {
                "tracking_number": s.get("tracking_number"),
                "customer": s.get("customer"),
                "judgement": s.get("judgement"),
                "reason": s.get("reason"),
            }
            for s in attention[:5]
        ],
    }


def list_salespeople() -> list[str]:
    """所有出现过的销售姓名。"""
    names = set()
    for row in load_shipments(date_text="all"):
        name = canonical_sales_name(row.get("salesperson", ""))
        if name:
            names.add(name)
    return sorted(names)


async def refresh_tracking_statuses() -> dict:
    """从 UPS/FedEx/DHL 官网抓取在途运单的最新状态，写入 tracking_auto_status 表。

    SF 顺丰官网强制图形验证码，跳过（继续使用人工录入）。
    """
    from app.logistics import tracking_fetch

    open_rows = load_shipments(date_text="all", open_only=True)
    candidates = [
        (row.get("carrier", ""), (row.get("tracking_number") or "").strip())
        for row in open_rows
        if tracking_fetch.is_supported_carrier(row.get("carrier", ""))
        and (row.get("tracking_number") or "").strip()
    ]
    # 去重（同一运单可能出现在多个日期批次）
    candidates = list({(c, t) for c, t in candidates})

    if not candidates:
        return {"attempted": 0, "ok": 0, "failed": 0, "skipped_no_candidates": True}

    results = await tracking_fetch.fetch_many(candidates)

    from datetime import datetime
    from sqlmodel import Session, select
    from app.database import get_engine
    from app.models.tracking_status import TrackingAutoStatus

    ok_count = 0
    with Session(get_engine()) as session:
        for r in results:
            existing = session.exec(
                select(TrackingAutoStatus).where(
                    TrackingAutoStatus.tracking_number == r.tracking_number
                )
            ).first()
            if existing:
                existing.carrier = r.carrier
                existing.status_text = r.status_text
                existing.is_delivered = r.is_delivered
                existing.fetch_ok = r.fetch_ok
                existing.error = r.error
                existing.fetched_at = datetime.utcnow()
                session.add(existing)
            else:
                session.add(TrackingAutoStatus(
                    tracking_number=r.tracking_number,
                    carrier=r.carrier,
                    status_text=r.status_text,
                    is_delivered=r.is_delivered,
                    fetch_ok=r.fetch_ok,
                    error=r.error,
                ))
            if r.fetch_ok:
                ok_count += 1
        session.commit()

    return {
        "attempted": len(results),
        "ok": ok_count,
        "failed": len(results) - ok_count,
    }
