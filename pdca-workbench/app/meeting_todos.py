# -*- coding: utf-8 -*-
"""早会待办（第 8 节）：按群/管理名单分流渲染。

规格见 docs/MEETING_TODOS_SPEC.md（老板 2026-09-20/21 拍板）：
- scope=group 的条目只发给对应达标群（groups 字段匹配）；
- scope=mgmt 的条目只进管理名单那版（08:00），不往达标群丢；
- 当天没有条目 -> 不出这一节（不写「无」、不编造）。
数据源 app/meeting_todos.json，由老板发早会截图、本地 Qwen OCR 后按人填入。
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from app.config import get_settings

TODOS_FILE = Path(__file__).with_name("meeting_todos.json")


def _runtime_items(day: str) -> list[dict]:
    """当天运行时文件（每天从管理员丁私聊自动取图 OCR 后落盘）优先。"""
    path = get_settings().data_dir / "runtime" / "meeting_todos" / (day + ".json")
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("早会待办运行时文件读取失败（{}）：{}", path, exc)
        return []
    items = payload.get("items") if isinstance(payload, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _load(day: str) -> list[dict]:
    runtime = _runtime_items(day)
    if runtime:
        return runtime
    try:
        payload = json.loads(TODOS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("早会待办读取失败（{}）：{}", TODOS_FILE, exc)
        return []
    block = payload.get(day) if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        return []
    items = block.get("items") or []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def items_for_group(day: str, group_name: str) -> list[dict]:
    """某达标群该看到的条目（scope=group 且 groups 命中）。"""
    out = []
    for item in _load(day):
        if str(item.get("scope") or "") != "group":
            continue
        groups = [str(g) for g in (item.get("groups") or [])]
        if group_name in groups:
            out.append(item)
    return out


def items_for_mgmt(day: str) -> list[dict]:
    """管理名单那版该看到的条目（scope=mgmt）。"""
    return [item for item in _load(day) if str(item.get("scope") or "") == "mgmt"]


def render(items: list[dict], lang: str = "zh", numbered: bool = False, start: int = 8) -> str:
    """拼第 8 节正文；没有条目返回空串（调用方据此不输出这一节）。"""
    if not items:
        return ""
    if lang == "en":
        head = (f"{start}. Meeting to-dos (from this morning's meeting):" if numbered else "Meeting to-dos (this morning):")
    else:
        head = (f"{start}. 今日早会待办：" if numbered else "今日早会待办：")
    lines = [head]
    for item in items:
        who = str(item.get("who") or "").strip()
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"   • {who}：{text}" if who else f"   • {text}")
    return "\n".join(lines) + "\n" if len(lines) > 1 else ""


def block_for_group(day: str, group_name: str, lang: str = "zh") -> str:
    return render(items_for_group(day, group_name), lang)


def block_for_mgmt(day: str, lang: str = "zh") -> str:
    return render(items_for_mgmt(day), lang, numbered=False)
