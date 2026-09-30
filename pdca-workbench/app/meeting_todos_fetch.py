# -*- coding: utf-8 -*-
"""每天从「管理员丁」私聊里取早会待办图 -> 本地 Qwen OCR -> 落当日待办。

老板 2026-09-23 指示：不用他手动发图，去管理员丁私聊里取，每天会有一条待办。

流程：im +history 拉当天消息 -> 挑图片附件 -> im +attachment-download 下载
     -> 本地 Qwen 视觉 OCR（图像任务只走 qwen3.8-27b，见 AGENTS.md 模型路由）
     -> 解析成 {who, scope, groups, text} -> 写 data/runtime/meeting_todos/<day>.json。

失败不编造：拉不到/读不出就返回空结果，当天第 8 节自然不出现（同时告警）。
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from app.config import get_settings

# 管理员丁（运营中心 / 人事行政部 / 行政部）——早会待办图的固定来源
PEER_USER_ID_DEFAULT = 913271

OCR_PROMPT = (
    "这是一张会议/早会待办截图。逐条抄出全部待办，保持原文用词，不要总结、不要翻译、"
    "不要编造；看不清写「看不清」。格式：先用【负责人】一行，再逐条编号列出他的待办；"
    "没有负责人归属的待办放在【其他】下面。只输出内容本身。"
)

_HEADER_RE = re.compile(r"^[\u3010\[]([^\u3011\]]{1,20})[\u3011\]]\s*$")
_ITEM_RE = re.compile(r"^(?:\d+\s*[.\u3001)]|[-\u2022*\u00b7])\s*(.+)$")
_WHO_LINE_RE = re.compile(r"^([^\uff1a:]{2,14})[\uff1a:]\s*(.+)$")
_NOISE_RE = re.compile(r"^(?:\d{4}[.\-/\u5e74]\d{1,2}|\u7b2c\s*\d+\s*\u9875|\u770b\u4e0d\u6e05)$")
_DOC_TITLE_RE = re.compile(r"^\d{4}[.\-/\u5e74]\d{1,2}[.\-/\u6708]?\d{0,2}\s*\S*$")


def _clean(line: str) -> str:
    text = line.replace("**", "").replace("`", "").replace("\u3000", " ").strip()
    return re.sub(r"\s+", " ", text)


def parse_items(text: str) -> list[dict]:
    """把 OCR 正文解析成 [{who, text}]，顺序保持原文，去重。"""
    items: list[dict] = []
    current = ""
    for raw in (text or "").splitlines():
        line = _clean(raw)
        if not line or _NOISE_RE.match(line) or _DOC_TITLE_RE.match(line):
            continue
        header = _HEADER_RE.match(line)
        if header:
            current = header.group(1).strip()
            continue
        if line.endswith(("\uff1a", ":")) and len(line) <= 20:
            current = line[:-1].strip()
            continue
        numbered = _ITEM_RE.match(line)
        if numbered:
            body = numbered.group(1).strip()
            if body and body != "\u770b\u4e0d\u6e05":
                items.append({"who": current, "text": body})
            continue
        who_line = _WHO_LINE_RE.match(line)
        if who_line:
            items.append({"who": who_line.group(1).strip(), "text": who_line.group(2).strip()})
            continue
        if len(line) >= 4:
            items.append({"who": current, "text": line})
    deduped: list[dict] = []
    seen: set[tuple] = set()
    for item in items:
        key = (item["who"], item["text"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped

def attach_scope(items: list[dict]) -> list[dict]:
    """按 OWNERS 别名把负责人映射到达标群；映射不到的一律算管理面。"""
    from app.duzhan_ledger import OWNERS, vemory_aliases

    out: list[dict] = []
    for item in items:
        who = str(item.get("who") or "").strip()
        groups: list[str] = []
        for owner in OWNERS:
            names = [n for n in vemory_aliases(owner) if n and len(n) >= 2]
            if any(name in who or who in name for name in names):
                groups.append(owner.group)
        row = dict(item)
        if groups:
            row["scope"] = "group"
            row["groups"] = sorted(set(groups))
        else:
            row["scope"] = "mgmt"
        out.append(row)
    return out


def _cli_json(args: list[str], timeout: float = 30.0) -> dict | None:
    from app.vertu.client import run_vertu_sync_json

    try:
        payload = run_vertu_sync_json(args, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("早会待办 CLI 调用失败 {}: {}", " ".join(args[:3]), exc)
        return None
    return payload if isinstance(payload, dict) else None


def resolve_channel(peer_user_id: int | None = None) -> str:
    """管理员丁私聊的 channel_id；配置优先，其次按 user_id 取会话。"""
    settings = get_settings()
    configured = str(getattr(settings, "meeting_todos_channel_id", "") or "").strip()
    if configured:
        return configured
    peer = int(
        peer_user_id
        or getattr(settings, "meeting_todos_peer_user_id", PEER_USER_ID_DEFAULT)
        or PEER_USER_ID_DEFAULT
    )
    payload = _cli_json(["im", "+chat", "--user-id", str(peer)])
    channel = (payload or {}).get("channel") or {}
    return str(channel.get("id") or "")


def day_images(channel_id: str, day: str) -> list[dict]:
    """当天该会话里的图片消息（按时间升序）。"""
    wanted = date.fromisoformat(day)
    peer_id = str(getattr(get_settings(), "meeting_todos_peer_user_id", PEER_USER_ID_DEFAULT))
    local_tz = ZoneInfo("Asia/Shanghai")
    payload = _cli_json(
        [
            "im",
            "+history",
            "--channel-id",
            channel_id,
            "--date-from",
            day + "T00:00:00+08:00",
            "--date-to",
            (wanted + timedelta(days=1)).isoformat() + "T00:00:00+08:00",
            "--limit",
            "60",
        ]
    )
    out: list[dict] = []
    for msg in (payload or {}).get("messages") or []:
        if not isinstance(msg, dict) or str(msg.get("message_type") or "") != "image":
            continue
        if str(msg.get("sender_user_id") or "") != peer_id:
            continue
        raw_time = str(msg.get("created_at") or "")
        try:
            created = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        except ValueError:
            continue
        if created.tzinfo is None or created.astimezone(local_tz).date() != wanted:
            continue
        for att in msg.get("attachments") or []:
            if not isinstance(att, dict):
                continue
            url = str(att.get("url") or "")
            if url:
                out.append(
                    {
                        "id": str(msg.get("id") or ""),
                        "created_at": str(msg.get("created_at") or ""),
                        "url": url,
                    }
                )
    out.sort(key=lambda item: item["created_at"])
    return out


def download(url: str, dest: Path) -> bool:
    from app.vertu.client import run_vertu_sync

    dest.parent.mkdir(parents=True, exist_ok=True)
    code, out, err = run_vertu_sync(
        ["im", "+attachment-download", "--url", url, "--output", str(dest)], timeout=60.0
    )
    if code == 0 and dest.exists() and dest.stat().st_size > 0:
        return True
    logger.warning("早会待办附件下载失败 code={} {}", code, (err or out or "")[:160])
    return False

def ocr_image(path: Path) -> str:
    """本地 Qwen 视觉 OCR（图像任务不走别的供应商）。"""
    import httpx

    from app.mto_ocr import _tls_verify

    settings = get_settings()
    if not settings.qwen_api_key:
        logger.warning("未配置 QWEN 密钥，早会待办图无法识别")
        return ""
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else ("image/webp" if suffix == ".webp" else "image/jpeg")
    b64 = base64.b64encode(path.read_bytes()).decode()
    payload = {
        "model": settings.qwen_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": OCR_PROMPT},
                    {"type": "image_url", "image_url": {"url": "data:" + mime + ";base64," + b64}},
                ],
            }
        ],
        "max_tokens": 4000,
        "temperature": 0.1,
    }
    try:
        resp = httpx.post(
            settings.qwen_base_url.rstrip("/") + "/v1/chat/completions",
            json=payload,
            headers={
                "Authorization": "Bearer " + settings.qwen_api_key,
                "Content-Type": "application/json",
            },
            timeout=300.0,
            verify=_tls_verify(),
        )
        resp.raise_for_status()
        choices = resp.json().get("choices") or [{}]
        return str(((choices[0] or {}).get("message") or {}).get("content") or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("早会待办 OCR 失败 {}: {}", path.name, exc)
        return ""


def runtime_path(day: str) -> Path:
    return get_settings().data_dir / "runtime" / "meeting_todos" / (day + ".json")


def fetch_day(day: str, channel_id: str = "", max_images: int | None = None) -> dict:
    """取当天待办；返回 {day, items, images, source}，失败返回空 items。"""
    settings = get_settings()
    channel = channel_id or resolve_channel()
    result: dict = {"day": day, "items": [], "images": 0, "source": "管理员丁私聊"}
    if not channel:
        logger.warning("早会待办：拿不到管理员丁私聊 channel_id，跳过 {}", day)
        return result
    images = day_images(channel, day)
    result["images"] = len(images)
    if not images:
        logger.info("早会待办：{} 私聊里没有图片", day)
        return result
    limit = int(max_images or getattr(settings, "meeting_todos_max_images", 3) or 3)  # 只是重试上限，不再合并多张
    tmp = Path(tempfile.mkdtemp(prefix="meeting-todos-"))
    items: list[dict] = []
    try:
        # 最新的一张优先（管理员丁每天最后会发一份汇总图）；读不出再往前退，最多试 limit 张。
        for image in list(reversed(images))[:limit]:
            stamp = re.sub(r"[^0-9]", "", image["created_at"][11:19]) or "img"
            dest = tmp / (stamp + "_" + image["id"][:8] + ".png")
            if not download(image["url"], dest):
                continue
            parsed = parse_items(ocr_image(dest))
            if parsed:
                logger.info("早会待办：{} 这张读出 {} 条", dest.name, len(parsed))
                items = parsed
                break
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    result["items"] = attach_scope(items)
    return result


def save_day(day: str, items: list[dict], source: str = "管理员丁私聊") -> Path | None:
    """把当天待办原子写进运行时文件；空则不落盘（第 8 节自然不出现）。"""
    if not items:
        return None
    path = runtime_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"day": day, "source": source, "items": items}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    logger.info("早会待办已落盘 {}（{} 条）", path, len(items))
    return path


def run_day(day: str, channel_id: str = "") -> dict:
    """取数 + 落盘（给定时任务用）。"""
    result = fetch_day(day, channel_id=channel_id)
    saved = save_day(day, result.get("items") or [])
    result["saved"] = str(saved) if saved else ""
    return result
