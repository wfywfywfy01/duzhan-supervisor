# -*- coding: utf-8 -*-
"""运维告警（F7）：同步/备份/健康检查失败推送到外部 webhook。

默认仅记录 ERROR 日志（含 [alert] 前缀便于检索）；配置
``PDCA_ALERT_WEBHOOK_URL`` 后向通用 webhook POST ``{"text": ...}``
（Slack/企微/钉钉自定义机器人均兼容该字段）。推送失败绝不抛出异常，
且相同告警 10 分钟内去重，避免定时任务失败时刷屏。
"""
from __future__ import annotations

import os
import time

from loguru import logger

_DEDUP_SECONDS = 600.0
_last_alert: dict = {"key": "", "at": 0.0}


def webhook_url() -> str:
    return os.environ.get("PDCA_ALERT_WEBHOOK_URL", "").strip()


def notify(title: str, detail: str = "") -> None:
    """发送告警；优先 VPS IM 机器人，其次通用 webhook，均未配置仅写日志。

    调用方无需 try/except；相同告警 10 分钟内去重。
    """
    key = f"{title}|{(detail or '')[:120]}"
    now = time.monotonic()
    if key == _last_alert["key"] and now - _last_alert["at"] < _DEDUP_SECONDS:
        return
    _last_alert["key"] = key
    _last_alert["at"] = now

    logger.error("[alert] {} | {}", title, detail)
    message = f"[PDCA 告警] {title}\n{detail}"

    try:
        from app.vps_im_push import push_vps_alert

        if push_vps_alert(message):
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("VPS 告警推送失败: {}", exc)

    url = webhook_url()
    if not url:
        return
    try:
        import httpx

        httpx.post(url, json={"text": message}, timeout=5.0)
    except Exception as exc:  # noqa: BLE001 — 推送失败只能记日志，不能影响主流程
        logger.warning("告警 webhook 推送失败: {}", exc)
