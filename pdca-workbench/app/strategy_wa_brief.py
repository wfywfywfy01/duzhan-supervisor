# -*- coding: utf-8 -*-
"""策略 WhatsApp 前 24 小时 HTML。

策略清单不写死：优先 data/runtime/wa_strategies.json（换策略只改这个文件），
没有则用 app/wa_strategies.json。不带 MCP keyword，本地 regex 打分。
"""
from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from app.config import get_settings
from app.duzhan_ledger import OWNERS, mcp_call

TZ = ZoneInfo("Asia/Shanghai")
# 2026-09-24 老板要求：策略核查新增新人组（成员E即 Nora）
TARGETS = (
    "张三",
    "李四",
    "Alice",
    "赵六",
    "Bella",
    "成员A",
    "Sofia",
    "成员C",
    "成员D",
    "成员E",
)
MAX_PAGES = 8
PAGE_SIZE = 50
_MARKS = "①②③④⑤⑥⑦⑧⑨"
_BUNDLED = Path(__file__).with_name("wa_strategies.json")
_loaded: tuple[str, float, list, list] | None = None


@dataclass
class Theme:
    """策略下的一个语义要点（老板 2026-09-24：话术后 5 点是重点，按语义匹配）。"""

    id: str
    label: str
    patterns: list


@dataclass
class Strategy:
    """一条当前在查的策略。换策略改 JSON，不改这里。"""

    id: str
    label: str
    strong: list
    weak: list
    skip_if_noise: bool = False
    until: str = ""  # 到期日（含当天）；填了就在这之后不再出现在报告里
    themes: list = field(default_factory=list)  # 语义要点；填了按「讲清几点」判定


def strategies_path() -> Path:
    """运行时文件优先，方便不发版就换策略。"""
    try:
        override = get_settings().data_dir / "runtime" / "wa_strategies.json"
        if override.is_file():
            return override
    except Exception:  # noqa: BLE001 — 配置还没起来时用包内默认
        pass
    return _BUNDLED


def load_strategies() -> tuple[list[Strategy], list]:
    """读策略文件。mtime 变了才重新编译。"""
    global _loaded
    path = strategies_path()
    mtime = path.stat().st_mtime
    key = str(path)
    if _loaded and _loaded[0] == key and _loaded[1] == mtime:
        return _loaded[2], _loaded[3]
    raw = json.loads(path.read_text(encoding="utf-8"))
    noise: list[re.Pattern[str]] = []
    for pat in raw.get("noise") or []:
        try:
            noise.append(re.compile(str(pat), re.I))
        except re.error as exc:
            logger.warning("策略噪声正则无效，已跳过 {}: {}", pat, exc)
    strategies: list[Strategy] = []
    for item in raw.get("strategies") or []:
        if not isinstance(item, dict) or not item.get("id"):
            logger.warning("策略条目缺 id，已跳过: {}", item)
            continue
        strong, weak = [], []
        for bucket, dest in (("strong", strong), ("weak", weak)):
            for pat in item.get(bucket) or []:
                try:
                    dest.append(re.compile(str(pat), re.I))
                except re.error as exc:
                    logger.warning("策略 {} 正则无效，已跳过 {}: {}", item.get("id"), pat, exc)
        if not strong and not weak:
            logger.warning("策略 {} 没有可用正则，已跳过", item.get("id"))
            continue
        themes: list[Theme] = []
        for raw_theme in item.get("themes") or []:
            if not isinstance(raw_theme, dict) or not raw_theme.get("id"):
                continue
            compiled: list[re.Pattern[str]] = []
            for pat in raw_theme.get("patterns") or []:
                try:
                    compiled.append(re.compile(str(pat), re.I))
                except re.error as exc:
                    logger.warning(
                        "策略 {} 主题 {} 正则无效，已跳过 {}: {}",
                        item.get("id"),
                        raw_theme.get("id"),
                        pat,
                        exc,
                    )
            if compiled:
                themes.append(
                    Theme(
                        id=str(raw_theme["id"]),
                        label=str(raw_theme.get("label") or raw_theme["id"]),
                        patterns=compiled,
                    )
                )
        strategies.append(
            Strategy(
                id=str(item["id"]),
                label=str(item.get("label") or item["id"]),
                strong=strong,
                weak=weak,
                skip_if_noise=bool(item.get("skip_if_noise")),
                until=str(item.get("until") or "").strip(),
                themes=themes,
            )
        )
    if not strategies:
        raise RuntimeError(f"策略文件没有可用 strategies: {path}")
    _loaded = (key, mtime, strategies, noise)
    return strategies, noise


def strategies_for(push_day: str) -> list[Strategy]:
    """按 until 过滤当期策略；全部过期时返回空，绝不复用旧策略。"""
    strategies, _noise = load_strategies()
    day = (push_day or "").strip()
    if not day:
        return strategies
    live = [item for item in strategies if not item.until or day <= item.until]
    if not live:
        logger.warning("策略全部已过期（截至 {}），停止策略核查", day)
        return []
    return live


def _mark(index: int, label: str) -> str:
    prefix = _MARKS[index] if index < len(_MARKS) else f"{index + 1}."
    return f"{prefix} {label}"


@dataclass
class Hit:
    """一条命中的 WhatsApp 消息。"""

    owner: str
    topic: str
    strength: str
    time: str
    customer: str
    direction: str
    kind: str
    text: str
    complete: bool = True
    themes: tuple[str, ...] = ()  # 这条消息命中的语义要点 id
    platform: str = "WhatsApp"  # WhatsApp / VPS IM


@dataclass
class OwnerScan:
    """一人窗口内的扫描结果。"""

    display: str
    employee_id: int | None
    message_count: int = 0
    complete: bool = True
    wa_configured: bool = True
    hits: list[Hit] = field(default_factory=list)
    note: str = ""
    theme_ids: dict = field(default_factory=dict)  # topic -> {要点 id}，窗口内累计
    wa_count: int = 0  # 窗口内 WhatsApp 消息数
    im_count: int = 0  # 窗口内 VPS IM 本人发言数


def window_for(push_day: str) -> tuple[datetime, datetime]:
    """推送日 08:00 往前 24 小时。"""
    end = datetime.strptime(push_day, "%Y-%m-%d").replace(
        hour=8, minute=0, second=0, tzinfo=TZ
    )
    return end - timedelta(hours=24), end


def window_text(start: datetime, end: datetime) -> str:
    """窗口文案。"""
    return f"{start.strftime('%m-%d %H:%M')} → {end.strftime('%m-%d %H:%M')}"


def _msg_text(row: dict) -> str:
    for key in ("content", "message_body", "body", "text", "message_text", "preview"):
        val = row.get(key)
        if isinstance(val, str) and val.strip():
            return val
    mm = row.get("multimodal")
    if isinstance(mm, dict) and mm:
        return json.dumps(mm, ensure_ascii=False)
    return ""


def _msg_time(row: dict) -> datetime | None:
    raw = str(row.get("time") or row.get("message_time") or row.get("created_at") or "")
    if not raw:
        return None
    raw = raw.replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw[:19], fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    return None


def classify(text: str) -> list[tuple[str, str]]:
    """返回 [(strategy_id, strong|weak), ...]。skip_if_noise 的策略碰到噪声词跳过。"""
    blob = text or ""
    strategies, noise = load_strategies()
    noisy = any(pat.search(blob) for pat in noise)
    out: list[tuple[str, str]] = []
    for item in strategies:
        if noisy and item.skip_if_noise:
            continue
        if any(pat.search(blob) for pat in item.strong):
            out.append((item.id, "strong"))
        elif any(pat.search(blob) for pat in item.weak):
            out.append((item.id, "weak"))
    return out


def _strategy_by_id(topic: str) -> Strategy | None:
    """按 id 取策略对象（带编译好的正则与主题）。"""
    strategies, _noise = load_strategies()
    return next((item for item in strategies if item.id == topic), None)


def theme_hits(strategy: Strategy | None, text: str) -> list[str]:
    """一条消息命中了该策略的哪几个语义要点（主题）。"""
    if strategy is None or not strategy.themes:
        return []
    blob = text or ""
    return [
        theme.id
        for theme in strategy.themes
        if any(pattern.search(blob) for pattern in theme.patterns)
    ]


def theme_note(scan: OwnerScan, topic: str) -> str:
    """要点覆盖情况，如 3/5 点；没配主题或没命中就返回空串。"""
    strategy = _strategy_by_id(topic)
    if strategy is None or not strategy.themes:
        return ""
    hit = len(scan.theme_ids.get(topic) or ())
    if not hit:
        return ""
    return f"{hit}/{len(strategy.themes)} 点"


def _in_window(row: dict, start: datetime, end: datetime) -> bool:
    when = _msg_time(row)
    if when is None:
        return True
    return start <= when < end


def fetch_owner_messages(
    employee_id: int, start: datetime, end: datetime
) -> tuple[list[dict], bool, str]:
    """无 keyword 分页拉 WhatsApp；本地再按 24h 过滤。"""
    details: list[dict] = []
    complete = True
    note = ""
    period = {
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
    }
    for page in range(1, MAX_PAGES + 1):
        payload = mcp_call(
            "business.query",
            {
                "domain": "conversations",
                "query_mode": "messages",
                "subject": {"employee_id": employee_id},
                "period": period,
                "filters": {
                    "platform": "WhatsApp",
                    "page": page,
                    "page_size": PAGE_SIZE,
                },
            },
        ) or {}
        if payload.get("is_complete") is False:
            complete = False
        rows = [r for r in (payload.get("rows") or []) if r.get("row_type") == "detail"]
        summary = next(
            (r for r in (payload.get("rows") or []) if r.get("row_type") == "summary"),
            None,
        )
        if summary:
            wa = ((summary.get("data_freshness") or {}).get("platforms") or {}).get(
                "whatsapp"
            ) or {}
            if wa.get("status") == "not_configured":
                return [], True, "未配置 WhatsApp"
            note = str(wa.get("note") or note)
            if wa.get("is_complete") is False:
                complete = False
        if not rows:
            break
        details.extend(rows)
        paging = payload.get("pagination") or {}
        if not paging.get("has_more"):
            break
    kept = [r for r in details if _in_window(r, start, end)]
    return kept, complete, note


IM_RECORD_PAGE_SIZE = 100
IM_RECORD_MAX_PAGES = 250  # 实测约 13k 条/天，250 页足够覆盖一个 24h 窗口


def _im_records_page(day_from: str, day_to: str, page: int) -> dict:
    """vps-work im +all-chat-records 的一页（凭独立权限的全域聊天记录）。"""
    from app.vertu.client import run_vertu_sync_json

    payload = run_vertu_sync_json(
        [
            "im",
            "+all-chat-records",
            "--start-date",
            day_from,
            "--end-date",
            day_to,
            "--page",
            str(page),
            "--page-size",
            str(IM_RECORD_PAGE_SIZE),
        ],
        timeout=45.0,
    )
    return payload if isinstance(payload, dict) else {}


def fetch_window_im_messages(
    start: datetime, end: datetime, owners: list
) -> tuple[dict[str, list[dict]], bool]:
    """全域 VPS IM 记录 → {成员显示名: 行列表}，只留核查对象本人发的。

    2026-09-24 老板：VPS 的聊天用 vps-work 拿。一次性分页拉窗口内全量，
    再按 sender_user_id 过滤（比按会话逐个扫更全：私聊、各类群都覆盖）。
    """
    by_user = {
        int(owner.im_user_id): owner.display
        for owner in owners
        if getattr(owner, "im_user_id", None)
    }
    out: dict[str, list[dict]] = {}
    if not by_user:
        return out, True
    complete = True
    day_from, day_to = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    for page in range(1, IM_RECORD_MAX_PAGES + 1):
        payload: dict = {}
        for attempt in range(2):  # 单页抖动重试一次，避免静默截断（2026-09-24 实测踩到）
            payload = _im_records_page(day_from, day_to, page)
            if payload.get("messages"):
                break
            if attempt == 0:
                time.sleep(1.5)
        messages = payload.get("messages") or []
        if not messages:
            complete = page == 1  # 首页就空=窗口内本来没记录；中途空=不完整
            break
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            sender = msg.get("sender_user_id")
            if sender is None or int(sender) not in by_user:
                continue
            when = _msg_time({"time": str(msg.get("created_at") or "").replace("T", " ")})
            if when is not None and not (start <= when < end):
                continue
            body = str(msg.get("body") or "").strip()
            if not body:
                continue
            channel = msg.get("channel") if isinstance(msg.get("channel"), dict) else {}
            label = str(channel.get("name") or "").strip()
            is_direct = str(channel.get("type") or "") == "direct"
            if not label:
                label = "私聊" if is_direct else "群聊"
            out.setdefault(by_user[int(sender)], []).append(
                {
                    "id": str(msg.get("id") or ""),
                    "time": str(msg.get("created_at") or "")[:19].replace("T", " "),
                    "content": body,
                    "direction": "私聊发言" if is_direct else "群发言",
                    "message_kind": str(msg.get("message_type") or "text"),
                    "customer_display": label,
                    "_platform": "VPS IM",
                }
            )
        paging = payload.get("pagination") or {}
        if not paging.get("has_next"):
            break
    return out, complete


def _im_history(channel_id: str, date_from: str, limit: int = 100) -> list[dict]:
    """VPS IM 某会话从 date_from 起的消息（走 CLI，与督战官同一套身份）。"""
    from app.vertu.client import run_vertu_sync_json

    payload = run_vertu_sync_json(
        [
            "im",
            "+history",
            "--channel-id",
            channel_id,
            "--date-from",
            date_from,
            "--limit",
            str(limit),
        ],
        timeout=30.0,
    )
    if not isinstance(payload, dict):
        return []
    return [item for item in (payload.get("messages") or []) if isinstance(item, dict)]


def im_channels(owner) -> list[tuple[str, str]]:
    """该成员要扫的 VPS IM 会话：达标群 + 客户跟进群（2026-09-24 老板：不只查 WhatsApp）。"""
    out: list[tuple[str, str]] = []
    try:
        from app.duzhan import GROUPS

        for group in GROUPS:
            if group.name == owner.group and group.channel_id:
                out.append((group.name, group.channel_id))
                break
    except Exception as exc:  # noqa: BLE001 — 配置读不到就只扫跟进群
        logger.debug("读达标群配置失败: {}", exc)
    follow = str(getattr(owner, "follow_channel_id", "") or "")
    if follow:
        out.append(("客户跟进群", follow))
    return out


def fetch_owner_im_messages(owner, start: datetime, end: datetime) -> list[dict]:
    """VPS IM 里**该成员本人**发的消息，转成与 WhatsApp 同形的行（带 _platform 标记）。"""
    date_from = start.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    rows: list[dict] = []
    for label, channel_id in im_channels(owner):
        try:
            messages = _im_history(channel_id, date_from)
        except Exception as exc:  # noqa: BLE001 — 一个会话失败不影响另一个
            logger.warning("VPS IM 取数失败 {} {}: {}", owner.display, label, exc)
            continue
        for msg in messages:
            sender = msg.get("sender_user_id")
            if owner.im_user_id and sender != owner.im_user_id:
                continue
            body = str(msg.get("body") or "").strip()
            if not body:
                continue
            rows.append(
                {
                    "id": str(msg.get("id") or ""),
                    "time": str(msg.get("created_at") or "")[:19].replace("T", " "),
                    "content": body,
                    "direction": "群发言",
                    "message_kind": str(msg.get("message_type") or "text"),
                    "customer_display": label,
                    "_platform": "VPS IM",
                }
            )
    return rows


def scan_owners(start: datetime, end: datetime) -> list[OwnerScan]:
    """扫全部核查对象：WhatsApp（MCP）+ VPS IM（vps-work 全域聊天记录）。"""
    wanted = [o for o in OWNERS if o.display in TARGETS]
    im_by_owner: dict[str, list[dict]] = {}
    im_ok = False
    im_complete = True
    try:
        im_by_owner, im_complete = fetch_window_im_messages(start, end, wanted)
        im_ok = True
        logger.info(
            "VPS IM 全域记录：{} 人窗口内有发言（共 {} 条）",
            len(im_by_owner),
            sum(len(items) for items in im_by_owner.values()),
        )
    except Exception as exc:  # noqa: BLE001 — 全域拿不到就按会话兜底
        logger.warning("VPS IM 全域记录失败，回落按会话扫: {}", exc)
    out: list[OwnerScan] = []
    for owner in wanted:
        scan = OwnerScan(display=owner.display, employee_id=owner.employee_id)
        if not owner.employee_id:
            scan.wa_configured = False
            scan.note = "无 employee_id"
            out.append(scan)
            continue
        try:
            rows, complete, note = fetch_owner_messages(owner.employee_id, start, end)
        except Exception as exc:  # noqa: BLE001 — 一人失败不能拖垮整份
            logger.warning("策略核查采集失败 {}: {}", owner.display, exc)
            scan.complete = False
            scan.note = f"采集失败: {exc}"[:180]
            out.append(scan)
            continue
        wa_count = len(rows)
        im_rows: list[dict] = list(im_by_owner.get(owner.display) or [])
        if not im_ok:
            try:
                im_rows = fetch_owner_im_messages(owner, start, end)
            except Exception as exc:  # noqa: BLE001 — VPS IM 失败不影响 WhatsApp 口径
                logger.warning("策略核查 VPS IM 采集失败 {}: {}", owner.display, exc)
                scan.complete = False
        rows = rows + im_rows
        scan.message_count = len(rows)
        scan.wa_count = wa_count
        scan.im_count = len(im_rows)
        scan.complete = complete and scan.complete and im_complete
        scan.note = note
        if note == "未配置 WhatsApp" or (complete and wa_count == 0 and not note):
            try:
                cust = mcp_call(
                    "business.query",
                    {
                        "domain": "conversations",
                        "query_mode": "customers",
                        "subject": {"employee_id": owner.employee_id},
                        "period": {
                            "start_date": start.date().isoformat(),
                            "end_date": end.date().isoformat(),
                        },
                        "filters": {"platform": "WhatsApp", "page_size": 1},
                    },
                ) or {}
            except Exception as exc:  # noqa: BLE001
                logger.warning("策略核查客户数失败 {}: {}", owner.display, exc)
                cust = {}
                scan.complete = False
                scan.note = f"客户数失败: {exc}"[:180]
            summary_text = str(cust.get("summary") or "")
            if "客户 0" in summary_text or "触达 0" in summary_text:
                scan.wa_configured = False
                scan.note = summary_text[:180] or "区间 WhatsApp 触达 0"
        seen: set[str] = set()
        for row in rows:
            text = _msg_text(row)
            labels = classify(text)
            if not labels:
                continue
            mid = str(row.get("id") or text[:80])
            for topic, strength in labels:
                key = f"{mid}|{topic}"
                if key in seen:
                    continue
                seen.add(key)
                strategy = _strategy_by_id(topic)
                hit_themes = theme_hits(strategy, text)
                if hit_themes:
                    scan.theme_ids.setdefault(topic, set()).update(hit_themes)
                scan.hits.append(
                    Hit(
                        owner=owner.display,
                        topic=topic,
                        strength=strength,
                        time=str(row.get("time") or ""),
                        customer=str(
                            row.get("customer_display")
                            or row.get("customer_id")
                            or ""
                        ),
                        direction=str(row.get("direction") or ""),
                        kind=str(row.get("message_kind") or row.get("message_type") or ""),
                        text=text[:800],
                        complete=complete,
                        themes=tuple(hit_themes),
                        platform=str(row.get("_platform") or "WhatsApp"),
                    )
                )
        out.append(scan)
    return out


def topic_verdict(scan: OwnerScan, topic: str) -> str:
    """有 / 部分 / 无 / 无WA / 待确认。

    已扫到正文但没命中 → 无（同步未完写在 HTML 副标题，不把「无」改成待确认）。
    0 条且同步未完 → 待确认。
    """
    if not scan.wa_configured:
        return "无WA"
    hits = [h for h in scan.hits if h.topic == topic]
    keyword = "有" if any(h.strength == "strong" for h in hits) else ("部分" if hits else "")
    strategy = _strategy_by_id(topic)
    if strategy is not None and strategy.themes:
        # 语义要点口径（2026-09-24 老板要求）：讲清 ≥2 个重点算「有」，只讲 1 个算「部分」
        covered = len(scan.theme_ids.get(topic) or ())
        semantic = "有" if covered >= 2 else ("部分" if covered == 1 else "")
        if semantic == "有" or keyword == "有":
            return "有"
        if semantic == "部分" or keyword == "部分":
            return "部分"
    elif keyword:
        return keyword
    if scan.message_count == 0 and not scan.complete:
        return "待确认"
    return "无"


def build_im_body(push_day: str, start: datetime, end: datetime, scans: list[OwnerScan]) -> str:
    """私聊短正文；明细在 HTML 附件。条数跟策略文件走。"""
    strategies = strategies_for(push_day)
    lines = [
        f"【策略核查（WhatsApp + VPS IM）｜{push_day} 08:00】",
        f"窗口 {window_text(start, end)}｜对象 {' / '.join(TARGETS)}",
        "",
    ]
    for index, item in enumerate(strategies):
        bits = [
            f"{scan.display}{topic_verdict(scan, item.id)}"
            + (f"（{theme_note(scan, item.id)}）" if theme_note(scan, item.id) else "")
            for scan in scans
        ]
        lines.append(f"{_mark(index, item.label)}：{' · '.join(bits)}")
    lines.append("")
    lines.append("明细见 HTML 附件。未检出 ≠ 没发过图（file/image 无 OCR）。")
    return "\n".join(lines)


def _esc(text: object) -> str:
    return html.escape(str(text if text is not None else ""))


def _verdict_class(verdict: str) -> str:
    return {
        "有": "v-strong",
        "部分": "v-mid",
        "待确认": "v-weak",
        "无": "v-none",
        "无WA": "v-none",
    }.get(verdict, "v-weak")


def build_html(
    push_day: str, start: datetime, end: datetime, scans: list[OwnerScan]
) -> str:
    """金黑单文件 HTML。栏目跟策略文件走。"""
    strategies = strategies_for(push_day)
    labels = {item.id: _mark(index, item.label) for index, item in enumerate(strategies)}
    kpis = []
    for scan in scans:
        cells = " / ".join(
            f"{labels[item.id][0]}{topic_verdict(scan, item.id)}"
            + (f"·{theme_note(scan, item.id)}" if theme_note(scan, item.id) else "")
            for item in strategies
        )
        kpis.append(
            f'<div class="kpi"><div class="who">{_esc(scan.display)}</div>'
            f'<div class="verdict">{_esc(cells)}</div>'
            f'<div class="sub">窗口内 {scan.message_count} 条'
            f"（WhatsApp {scan.wa_count} · VPS IM {scan.im_count}）"
            f'{" · 同步未完" if not scan.complete else ""}'
            f'{" · " + _esc(scan.note[:80]) if scan.note else ""}</div></div>'
        )
    tables = []
    for item in strategies:
        topic = item.id
        label = labels[topic]
        rows = []
        for scan in scans:
            verdict = topic_verdict(scan, topic)
            note = theme_note(scan, topic)
            verdict_cell = verdict + (f"（{note}）" if note else "")
            hits = [h for h in scan.hits if h.topic == topic]
            if not hits:
                rows.append(
                    "<tr>"
                    f"<td>{_esc(scan.display)}</td><td>—</td><td>—</td>"
                    f'<td class="{_verdict_class(verdict)}">{_esc(verdict_cell)}</td>'
                    f"<td>{_esc(scan.note[:120] if verdict in ('无WA', '待确认') else '未检出正文')}</td>"
                    "</tr>"
                )
                continue
            for hit in hits:
                theme_tag = ("·" + "/".join(hit.themes)) if hit.themes else ""
                rows.append(
                    "<tr>"
                    f"<td>{_esc(scan.display)}</td>"
                    f"<td>{_esc(hit.time)}</td>"
                    f"<td>{_esc(hit.platform)} · {_esc(hit.customer)} / { _esc(hit.direction)}</td>"
                    f'<td class="{_verdict_class(verdict)}">{_esc(verdict_cell)}（{ _esc(hit.strength)}{_esc(theme_tag)}）</td>'
                    f"<td>{_esc(hit.text)}</td>"
                    "</tr>"
                )
        tables.append(
            f'<section id="{topic}"><h2>{_esc(label)}</h2>'
            "<table><thead><tr>"
            "<th>销售</th><th>时间</th><th>客户 / 方向</th><th>结论</th><th>原文</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></section>"
        )
    people = []
    for scan in scans:
        blocks = []
        for item in strategies:
            topic = item.id
            hits = [h for h in scan.hits if h.topic == topic]
            verdict = topic_verdict(scan, topic)
            if hits:
                quotes = "".join(
                    f"<blockquote>{_esc(h.text)}</blockquote>"
                    f'<p class="note">{_esc(h.time)} · {_esc(h.customer)} · {_esc(h.direction)}</p>'
                    for h in hits[:6]
                )
            else:
                quotes = f'<p class="note">{_esc(verdict)}：窗口内无匹配正文。</p>'
            blocks.append(
                f"<h3>{_esc(labels[topic])} "
                f'<span class="tag { _verdict_class(verdict)}">{_esc(verdict)}</span></h3>'
                + quotes
            )
        people.append(
            f'<section class="person" id="p-{_esc(scan.display)}">'
            f"<h3>{_esc(scan.display)} <span class=\"tag\">employee_id {scan.employee_id}</span>"
            f'<span class="tag">{scan.message_count} 条</span></h3>'
            + "".join(blocks)
            + "</section>"
        )
    title = "策略核查"
    try:
        title = str(json.loads(strategies_path().read_text(encoding="utf-8")).get("title") or title)
    except (OSError, json.JSONDecodeError):
        pass
    nav = "".join(
        f'<a href="#{item.id}">{_esc(labels[item.id])}</a>' for item in strategies
    )
    joined = " / ".join(item.label for item in strategies)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_esc(title)} WhatsApp · { _esc(push_day) } 08:00</title>
<style>
:root {{
  --bg:#10141c; --panel:#171d28; --line:#2a3344; --gold:#d4b56a;
  --text:#e8edf5; --muted:#8b95a7; --strong:#3d9a6a; --mid:#c9a227; --none:#c45c5c;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.55 "Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
header.top {{ padding:28px 32px 18px; border-bottom:1px solid var(--line); }}
header.top h1 {{ margin:0; font-size:22px; color:var(--gold); font-weight:650; }}
header.top p {{ margin:8px 0 0; color:var(--muted); font-size:13px; }}
nav {{ display:flex; gap:8px; flex-wrap:wrap; padding:12px 32px; border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--bg); }}
nav a {{ color:var(--text); text-decoration:none; border:1px solid var(--line); padding:6px 10px; font-size:13px; }}
main {{ padding:8px 32px 56px; max-width:1080px; }}
h2 {{ font-size:16px; color:var(--gold); border-bottom:1px solid var(--line); padding-bottom:8px; margin:28px 0 14px; }}
h3 {{ font-size:15px; margin:16px 0 8px; }}
.note {{ color:var(--muted); font-size:12px; margin:8px 0 12px; }}
.grid5 {{ display:grid; grid-template-columns:repeat(5,1fr); gap:10px; margin:16px 0; }}
.kpi {{ background:var(--panel); border:1px solid var(--line); padding:14px 12px; }}
.kpi .who {{ font-size:13px; color:var(--muted); }}
.kpi .verdict {{ font-size:15px; font-weight:650; margin-top:6px; }}
.kpi .sub {{ font-size:12px; color:var(--muted); margin-top:6px; }}
.v-strong {{ color:var(--strong); }}
.v-mid {{ color:var(--mid); }}
.v-weak {{ color:var(--muted); }}
.v-none {{ color:var(--none); }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th,td {{ border:1px solid var(--line); padding:8px 10px; vertical-align:top; text-align:left; }}
th {{ background:#1c2432; color:var(--gold); }}
.person {{ background:var(--panel); border:1px solid var(--line); margin:0 0 14px; padding:16px 18px; }}
blockquote {{ margin:0; padding:10px 12px; background:#121821; border:1px solid var(--line); font-size:13px; white-space:pre-wrap; }}
.tag {{ display:inline-block; font-size:11px; padding:2px 7px; border:1px solid var(--line); color:var(--muted); margin-right:6px; }}
.caveat {{ border:1px solid #5a4630; background:#1a1712; padding:12px 14px; font-size:13px; color:#e2d3b3; }}
@media (max-width:900px) {{ .grid5 {{ grid-template-columns:1fr 1fr; }} }}
</style>
</head>
<body>
<header class="top">
  <h1>{_esc(title)}（WhatsApp + VPS IM）</h1>
  <p>对象：{' / '.join(TARGETS)} · 窗口 { _esc(window_text(start, end)) } · 推送日 { _esc(push_day) } 08:00 北京 · 数据源：WhatsApp（AINativeSales MCP messages）+ VPS IM 全域聊天记录（vps-work im +all-chat-records，只取本人发言）· 策略文件 { _esc(strategies_path().name) }</p>
</header>
<nav>
  <a href="#summary">总览</a>
  {nav}
</nav>
<main>
<section id="summary">
  <h2>总览</h2>
  <p class="note">对照：{_esc(joined)}。结论按已同步聊天正文（每人卡片括号内为 WhatsApp / VPS IM 各自扫到的条数）。换策略改 wa_strategies.json。</p>
  <div class="grid5">{''.join(kpis)}</div>
</section>
{''.join(tables)}
{''.join(people)}
<section>
  <h2>口径</h2>
  <div class="caveat">MCP 无 keyword 分页 + 本地过滤。keyword 会漏「30% upfront」这类词面。file/image 无 OCR，海报图可能漏。is_complete=false 时 0 条写成待确认。skip_if_noise 的策略不计 Apple Watch / 维修店。</div>
</section>
</main>
</body>
</html>
"""


def save_html(push_day: str, html_text: str) -> Path:
    """落到 data/exports/strategy_wa/。"""
    out_dir = get_settings().data_dir / "exports" / "strategy_wa"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"策略核查_{push_day}.html"
    path.write_text(html_text, encoding="utf-8")
    files = sorted(out_dir.glob("策略核查_*.html"), key=lambda p: p.stat().st_mtime)
    for stale in files[: max(len(files) - 45, 0)]:
        stale.unlink(missing_ok=True)
    return path


def run_report(push_day: str) -> dict:
    """采集并写 HTML，不发送。策略来自 wa_strategies.json。"""
    strategies = strategies_for(push_day)
    if not strategies:
        raise RuntimeError(f"截至 {push_day} 没有有效策略；策略核查已停止")
    start, end = window_for(push_day)
    scans = scan_owners(start, end)
    path = save_html(push_day, build_html(push_day, start, end, scans))
    body = build_im_body(push_day, start, end, scans)
    return {
        "day": push_day,
        "html": str(path),
        "body": body,
        "scans": [
            {
                "display": scan.display,
                "messages": scan.message_count,
                "complete": scan.complete,
                **{item.id: topic_verdict(scan, item.id) for item in strategies},
            }
            for scan in scans
        ],
        "sent": False,
        "reason": "",
    }


def run_brief(push_day: str, *, dry_run: bool = False) -> dict:
    """采集 → HTML。dry_run 不发。正式发送走 08:00 的 campaign 任务。"""
    result = run_report(push_day)
    if dry_run:
        result["reason"] = "dry_run"
    return result


def main() -> None:
    """CLI：python -m app.strategy_wa_brief [--day YYYY-MM-DD] [--dry-run]。"""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    day = args.day or datetime.now(TZ).strftime("%Y-%m-%d")
    result = run_brief(day, dry_run=args.dry_run)
    print(json.dumps({k: result[k] for k in result if k != "body"}, ensure_ascii=False, indent=2))
    print(result["body"])


if __name__ == "__main__":
    main()
