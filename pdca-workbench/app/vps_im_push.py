# -*- coding: utf-8 -*-
"""VPS IM 机器人推送：日报与告警分通道。

凭据从环境变量读取（.env 未跟踪）：
  PDCA_VPS_BOT_APP_ID / PDCA_VPS_BOT_APP_SECRET
日报目标群与告警目标群分别支持持久化覆盖（位于挂载数据目录，跨部署
存活，不受部署机 .env 回滚影响）：
  data/runtime/push_channel.txt  — 日报目标群（经销商群）
  data/runtime/alert_channel.txt — 告警/报错目标群（内部运维群）
文件缺失/为空时回退环境变量 PDCA_VPS_BOT_CHANNEL_ID。
未配置 channel 时返回 False（调用方降级为通用 webhook / 仅日志）。
"""
from __future__ import annotations

import os

import httpx
from loguru import logger

from app.config import get_settings

VPS_PUSH_URL = "https://vps.example.com/v1/im/user-robots/push"

_PUSH_CHANNEL_OVERRIDE = "runtime/push_channel.txt"
_ALERT_CHANNEL_OVERRIDE = "runtime/alert_channel.txt"


def _file_channel(relative_path: str) -> str:
    """读取持久化频道覆盖文件；缺失/为空返回空串。"""
    path = get_settings().data_dir / relative_path
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def push_vps_message(message: str, *, idempotency_key: str = "") -> bool:
    """推日报/业务消息到日报目标群。"""
    channel_id = _file_channel(_PUSH_CHANNEL_OVERRIDE) or os.environ.get(
        "PDCA_VPS_BOT_CHANNEL_ID", ""
    ).strip()
    scoped_key = f"{idempotency_key}:{channel_id}" if idempotency_key else ""
    return _push(message, channel_id, idempotency_key=scoped_key)


def push_vps_alert(message: str) -> bool:
    """推告警/报错消息到告警目标群（与日报群分离）。"""
    channel_id = (
        _file_channel(_ALERT_CHANNEL_OVERRIDE)
        or os.environ.get("PDCA_ALERT_BOT_CHANNEL_ID", "").strip()
    )
    business_channel = _file_channel(_PUSH_CHANNEL_OVERRIDE) or os.environ.get(
        "PDCA_VPS_BOT_CHANNEL_ID", ""
    ).strip()
    if channel_id and channel_id == business_channel:
        return False
    return _push(message, channel_id)


def push_duzhan_message(
    message: str,
    channel_id: str,
    *,
    parent_message_id: str = "",
    idempotency_key: str = "",
) -> bool:
    """推督战官消息到指定群；只用 PDCA_DUZHAN_*，不回退日报机器人。"""
    app_id = os.environ.get("PDCA_DUZHAN_BOT_APP_ID", "").strip()
    app_secret = os.environ.get("PDCA_DUZHAN_BOT_APP_SECRET", "").strip()
    return _push(
        message,
        channel_id,
        app_id=app_id,
        app_secret=app_secret,
        parent_message_id=parent_message_id,
        idempotency_key=idempotency_key,
    )


def push_as_bot(
    message: str,
    channel_id: str,
    *,
    app_id: str,
    app_secret: str,
    parent_message_id: str = "",
    idempotency_key: str = "",
) -> bool:
    """以任意自有机器人身份推一条消息到群（小黑屋管理员这类专用机器人用）。"""
    return _push(
        message,
        channel_id,
        app_id=app_id,
        app_secret=app_secret,
        parent_message_id=parent_message_id,
        idempotency_key=idempotency_key,
    )


def _push(
    message: str,
    channel_id: str,
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    parent_message_id: str = "",
    idempotency_key: str = "",
) -> bool:
    """推一条文本消息到指定群；成功返回 True，未配置/失败返回 False。"""
    if app_id is None:
        app_id = os.environ.get("PDCA_VPS_BOT_APP_ID", "").strip()
    if app_secret is None:
        app_secret = os.environ.get("PDCA_VPS_BOT_APP_SECRET", "").strip()
    if not (app_id and app_secret and channel_id):
        return False
    payload: dict = {"channel_id": channel_id, "body": message}
    if parent_message_id:
        payload["parent_message_id"] = parent_message_id
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
        payload["client_message_id"] = idempotency_key
    headers = {
        "x-vertu-bot-app-id": app_id,
        "x-vertu-bot-app-secret": app_secret,
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        resp = httpx.post(
            VPS_PUSH_URL,
            json=payload,
            headers=headers,
            timeout=15.0,
        )
        if resp.status_code != 200:
            logger.warning(
                "VPS 推送失败 status={} channel={}：{}",
                resp.status_code,
                (channel_id or "")[:8],
                (resp.text or "")[:200],
            )
            return False
        payload = resp.json()
        return bool(payload.get("ok"))
    except Exception as exc:  # noqa: BLE001 — 推送失败仅返回 False，由调用方降级
        # 必须留日志：否则运维无法区分密钥失效 / 网关 5xx / 网络超时（2026-09-20 审查发现）
        logger.warning("VPS 推送异常 channel={}: {}", (channel_id or "")[:8], exc)
        return False
