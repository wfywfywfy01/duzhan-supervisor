# -*- coding: utf-8 -*-
"""本地 Qwen（llamacpp 网关）OpenAI 兼容客户端：供应商无关、超时可控、失败可回退。

模型与地址全部来自环境变量（见 app/config.py 的 PDCA_QWEN_* / PDCA_SUPERVISOR_*），
代码不写死供应商；网络或模型失败抛出 QwenUnavailable，调用方必须回退确定性路径，
严禁把模型错误解释为业务为零。
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
from loguru import logger

from app.config import get_settings


class QwenUnavailable(RuntimeError):
    """本地 Qwen 网关不可用（未配置 / 网络 / 超时 / 非 200）。"""


class QwenClient:
    """OpenAI 兼容 chat/completions 客户端（llm.example.com 为 llamacpp 网关）。"""

    def __init__(self, *, base_url: str = "", api_key: str = "", model: str = "",
                 timeout_seconds: float = 90.0):
        settings = get_settings()
        self.base_url = (base_url or settings.qwen_base_url).rstrip("/")
        self.api_key = api_key or settings.qwen_api_key
        self.model = model or settings.qwen_model
        self.timeout = timeout_seconds

    @property
    def configured(self) -> bool:
        """是否具备调用条件（缺 key 时调用方写“待确认”而不是报错）。"""
        return bool(self.base_url and self.api_key and self.model)

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 2000,
        temperature: float = 0.2,
        response_format: dict | None = None,
    ) -> dict[str, Any]:
        """调用 chat/completions 并返回解析后的 JSON；失败抛 QwenUnavailable。"""
        if not self.configured:
            raise QwenUnavailable("本地 Qwen 未配置（缺少 base_url / api_key / model）")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format
        url = self.base_url + "/v1/chat/completions"
        started = time.monotonic()
        last_error: Exception | None = None
        steered = False
        # 网络抖动重试 2 次（间隔 2s/4s）；业务失败不重试。
        for attempt in range(3):
            try:
                resp = httpx.post(
                    url,
                    json=payload,
                    headers={"Authorization": "Bearer " + self.api_key},
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    choice = (data.get("choices") or [{}])[0]
                    content = ""
                    try:
                        content = choice["message"]["content"] or ""
                    except (KeyError, IndexError, TypeError):
                        raise QwenUnavailable("Qwen 响应缺少 choices[0].message.content")
                    finish_reason = choice.get("finish_reason") or ""
                    usage = data.get("usage") or {}
                    logger.info(
                        "Qwen 调用成功 model={} elapsed={:.1f}s tokens={} finish={}",
                        self.model,
                        time.monotonic() - started,
                        usage,
                        finish_reason,
                    )
                    # 推理模型（deepseek-flash）reasoning 会吃满预算导致 content 为空：
                    # 追加“直接输出”引导重试一次，仍为空则明确报错（不回退成空结果）。
                    if not content.strip() and attempt < 2 and not steered:
                        payload["messages"] = messages + [{
                            "role": "user",
                            "content": "请直接输出最终答案，不要再进行任何推理或说明。",
                        }]
                        payload["max_tokens"] = min(max(max_tokens, 4096), 8192)
                        steered = True
                        last_error = RuntimeError(f"content 为空 finish={finish_reason}")
                        continue
                    if not content.strip():
                        raise QwenUnavailable(
                            f"模型返回空内容（finish_reason={finish_reason}）"
                        )
                    return {"content": content, "usage": usage}
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    last_error = RuntimeError(f"HTTP {resp.status_code}")
                    time.sleep(2 * (attempt + 1))
                    continue
                raise QwenUnavailable(f"HTTP {resp.status_code}: {(resp.text or "")[:200]}")
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                break
        elapsed = time.monotonic() - started
        logger.warning("Qwen 调用失败 model={} elapsed={:.1f}s err={}", self.model, elapsed, last_error)
        raise QwenUnavailable(str(last_error or "未知错误"))


def supervisor_client() -> QwenClient:
    """文本任务模型客户端（主 Agent 决策 / 群草稿润色）。

    模型路由约定（2026-09-17 拍板）：
    - 图像/OCR/视觉任务：本地 Qwen 网关（PDCA_QWEN_*，mto_ocr 专用）；
    - 其他文本任务：DeepSeek flash（PDCA_SUPERVISOR_PROVIDER/MODEL/API_KEY，
      默认 https://api.deepseek.com + deepseek-flash）；
    - PDCA_SUPERVISOR_* 未配置时回落本地 Qwen。
    """
    import os

    base_url = os.environ.get("PDCA_SUPERVISOR_PROVIDER", "").strip()
    if not base_url or base_url == "qwen":
        base_url = get_settings().qwen_base_url
    return QwenClient(
        base_url=base_url,
        api_key=os.environ.get("PDCA_SUPERVISOR_API_KEY", "").strip() or get_settings().qwen_api_key,
        model=os.environ.get("PDCA_SUPERVISOR_MODEL", "").strip() or get_settings().qwen_model,
        timeout_seconds=float(os.environ.get("PDCA_SUPERVISOR_TIMEOUT_SECONDS", "90") or 90),
    )
