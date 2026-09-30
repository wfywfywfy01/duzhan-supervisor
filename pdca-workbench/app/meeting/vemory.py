# -*- coding: utf-8 -*-
"""经销商部门 Vemory 全量拉取：列表分页 / 单场详情 / 音频直链（TTL 缓存）。

数据源是服务器本机的 vertu-cli（meeting +list / +detail），与页面侧
bridge 解耦，供 /api/meeting-center/vemory/* 三个端点复用。

- 列表：按部门 ID 分页拉全量轻量记录（最快路径）
- 详情：单场纪要全文 / 音频链接 / 章节 / 待办 / 逐字稿（vps 会议）
- 音频直链：列表 + 并发详情，首次较慢，TTL 内命中缓存
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

from app.config import get_settings
from app.vertu.client import run_vertu_json

# (start, end, dept_ids) -> {"ts": monotonic, "links": [ ... ]}
_AUDIO_CACHE: dict[tuple, dict] = {}
# meeting_id -> {"ts": monotonic, "meeting": {...}}
_DETAIL_CACHE: dict[str, dict] = {}
_DETAIL_CONCURRENCY = 8
_CACHE_HARD_CAP = 2048


def _cache_evict(cache: dict, key: Any) -> None:
    cache.pop(key, None)
    if len(cache) > _CACHE_HARD_CAP:
        for stale in list(cache)[: _CACHE_HARD_CAP // 2]:
            cache.pop(stale, None)


def _cache_get(cache: dict, key: Any, ttl: int) -> Any | None:
    entry = cache.get(key)
    if entry and time.monotonic() - entry["ts"] < ttl:
        return entry["value"]
    if entry:
        cache.pop(key, None)
    return None


def _dept_ids(dept_ids: str) -> str:
    ids = (dept_ids or "").strip()
    return (ids or get_settings().vemory_dept_ids).strip()


def normalize_meeting(row: dict) -> dict:
    """把 Vemory 轻量记录收敛为会议中心的稳定读取模型。

    Vemory 列表没有可靠的内部/外部和会议分类字段，因此未知值必须明确
    标为 ``unknown``，不能把它们伪装成内部例会。
    """
    start_time = str(row.get("start_time") or "")
    date_text = str(row.get("date") or start_time)[:10]
    meeting_date = date_text if len(date_text) == 10 else None
    owner_name = str(row.get("owner_name") or "").strip()
    participants = row.get("participants")
    participants = list(participants) if isinstance(participants, list) else []
    participant_names = {
        str(item.get("name") or item.get("display_name") or "").strip()
        for item in participants
        if isinstance(item, dict)
    }
    if owner_name and owner_name not in participant_names:
        participants.append({"name": owner_name})
    todos = row.get("todos")
    todos = list(todos) if isinstance(todos, list) else []

    duration = row.get("duration_minutes")
    if duration in (None, ""):
        try:
            duration = float(row.get("duration_seconds") or 0) / 60
        except (TypeError, ValueError):
            duration = 0
    try:
        duration_minutes = max(0, round(float(duration)))
    except (TypeError, ValueError):
        duration_minutes = 0

    meeting_type = str(row.get("meeting_type") or "").strip().lower()
    if meeting_type not in {"internal", "external"}:
        meeting_type = "unknown"
    bucket = str(row.get("bucket") or "").strip().lower()
    if bucket not in {"interview", "report", "customer"}:
        bucket = "unknown"
    brief = row.get("brief") or row.get("summary") or row.get("todo_summary") or ""
    return {
        "id": str(row.get("id") or row.get("meeting_id") or "").strip(),
        "meeting_date": meeting_date,
        "title": str(row.get("title") or row.get("name") or "未命名会议").strip() or "未命名会议",
        "meeting_type": meeting_type,
        "bucket": bucket,
        "duration_minutes": duration_minutes,
        "brief": str(brief) if isinstance(brief, str) else "",
        "todos": todos,
        "participants": participants,
        "owner_name": owner_name,
        "source": "vemory_live",
    }


async def list_dealer_meetings(
    start: str,
    end: str,
    dept_ids: str = "",
) -> tuple[list[dict], str | None]:
    """分页拉取指定部门的会议轻量列表。

    @returns (rows, error)；error 非空时 rows 可能为已拉取的部分数据。
    """
    settings = get_settings()
    ids = _dept_ids(dept_ids)
    if not ids.strip():
        return [], "未配置经销商部门 ID（PDCA_VEMORY_DEPT_IDS）"
    rows: list[dict] = []
    error: str | None = None
    expected_total: int | None = None
    from app.vertu.client import cli_command_missing

    for page in range(1, settings.vemory_max_pages + 1):
        payload = await run_vertu_json(
            [
                "meeting", "+list",
                "--scope", "selection",
                "--department-ids", ids,
                "--start-date", start,
                "--end-date", end,
                "--page-size", str(settings.vemory_page_size),
                "--page", str(page),
            ],
            timeout=45.0,
        )
        if not isinstance(payload, dict) or not payload.get("ok"):
            if cli_command_missing("meeting"):
                error = "本机 vps-work 没有 meeting 命令"
            else:
                error = "会议列表接口调用失败（vps-work meeting +list）"
            logger.warning("vemory list failed page={} ids={}", page, ids)
            break
        try:
            total = int(payload.get("total"))
        except (TypeError, ValueError):
            total = None
        if total is None or total < 0:
            error = "会议列表缺少有效总数，无法确认数据完整性"
            logger.warning("vemory list missing total page={} ids={}", page, ids)
            break
        expected_total = total
        batch = payload.get("meetings") or []
        if not isinstance(batch, list) or not all(isinstance(row, dict) for row in batch):
            error = "会议列表返回格式异常"
            logger.warning("vemory list malformed page={} ids={}", page, ids)
            break
        if not batch:
            if expected_total is not None and len(rows) < expected_total:
                error = "会议列表未完整返回（分页提前结束）"
            break
        rows.extend(batch)
        if expected_total is not None and len(rows) >= expected_total:
            break
    if error is None and expected_total is not None and len(rows) < expected_total:
        error = "会议列表超出安全分页上限，未使用不完整数据"
        logger.warning(
            "vemory list truncated expected_total={} received={} max_pages={}",
            expected_total, len(rows), settings.vemory_max_pages,
        )
    for row in rows:
        row["date"] = (row.get("start_time") or "")[:10]
    return rows, error


async def meeting_detail(
    meeting_id: str,
    refresh: bool = False,
) -> tuple[dict | None, str | None]:
    """单场会议详情：纪要全文 / audio_url / 章节 / 待办 / 逐字稿。"""
    mid = (meeting_id or "").strip()
    if not mid:
        return None, "缺少 meeting_id"
    if not refresh:
        cached = _cache_get(_DETAIL_CACHE, mid, get_settings().vemory_audio_cache_ttl)
        if cached:
            return cached, None
    payload = await run_vertu_json(
        ["meeting", "+detail", "--meeting-id", mid],
        timeout=60.0,
    )
    if not isinstance(payload, dict) or not payload.get("ok") or not payload.get("meeting"):
        return None, "会议详情获取失败（meeting_id 不存在或无权访问）"
    meeting = payload["meeting"]
    _DETAIL_CACHE[mid] = {"ts": time.monotonic(), "value": meeting}
    _cache_evict(_DETAIL_CACHE, "")
    return meeting, None


async def audio_links(
    start: str,
    end: str,
    dept_ids: str = "",
    force: bool = False,
) -> tuple[list[dict], str | None]:
    """经销商部门会议音频直链清单（纪要+音频口径）。

    首次请求会并发拉详情，TTL 内命中缓存；force=True 绕过缓存。
    """
    ids = _dept_ids(dept_ids)
    if not ids.strip():
        return [], "未配置经销商部门 ID（PDCA_VEMORY_DEPT_IDS）"
    key = (start, end, ids)
    if not force:
        cached = _cache_get(_AUDIO_CACHE, key, get_settings().vemory_audio_cache_ttl)
        if cached is not None:
            return cached, None
    rows, error = await list_dealer_meetings(start, end, ids)
    if error:
        return [], error
    if not rows:
        _AUDIO_CACHE[key] = {"ts": time.monotonic(), "value": []}
        return [], None

    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def one(row: dict) -> dict | None:
        mid = row.get("id") or ""
        if not mid:
            return None
        async with semaphore:
            detail, _ = await meeting_detail(mid)
        url = (detail or {}).get("audio_url")
        if not url:
            return None
        return {
            "date": (row.get("start_time") or "")[:10],
            "owner": row.get("owner_name") or "",
            "name": row.get("name") or "",
            "audio_url": url,
            "meeting_id": mid,
        }

    gathered = await asyncio.gather(*(one(row) for row in rows))
    links = [item for item in gathered if item]
    _AUDIO_CACHE[key] = {"ts": time.monotonic(), "value": links}
    return links, None


def clear_cache() -> None:
    """测试/运维入口：清空全部缓存。"""
    _AUDIO_CACHE.clear()
    _DETAIL_CACHE.clear()
