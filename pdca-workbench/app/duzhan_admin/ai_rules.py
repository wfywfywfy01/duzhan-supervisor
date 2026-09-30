# -*- coding: utf-8 -*-
"""AI 规则块执行：按当天数据生成「本档动作」那一句话。

只在 **db 源** 且该子 Agent 配了 mode=ai 的规则块时才起作用：

- 走模型路由约定里的文本模型（DeepSeek flash，supervisor_client()）；
- 生成结果按（子 Agent, 日期, 档位）落盘缓存，一档只花一次钱；
- 每天有调用次数上限，超了就直接用固定文案；
- **任何失败（超时/不可用/返回为空）都返回 None**，调用方回落到写死的
  _SLOT_ZH / _SLOT_EN 文案 —— 文案可以普通，但绝不能因为模型问题漏发。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from app.duzhan_blocks import Block, parse_blocks
from app.models.duzhan_agent import DuzhanAgent

MAX_CHARS = 80
# deepseek-flash 是带推理的模型：预算给小了会全花在 reasoning 上、content 为空。
# 默认给足 1200，并且 content 为空时再给一次 4 倍预算的机会。
DEFAULT_MAX_TOKENS = 1200
DEFAULT_TIMEOUT_SECONDS = 25.0
DEFAULT_DAILY_LIMIT = 60

_SLOT_TITLES = {10: "早追·定任务", 15: "中追·追变化", 20: "晚追·验兑现"}


def _cache_dir() -> Path:
    from app.config import get_settings

    folder = get_settings().data_dir / "runtime" / "duzhan_ai_rules"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _limit(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


def ai_rule_of(agent: DuzhanAgent) -> Optional[Block]:
    """取这个子 Agent 的 AI 规则块（没有就返回 None）。"""
    for block in parse_blocks(agent.blocks_json):
        if block.type == "rule" and str(block.get("mode") or "text") == "ai":
            if str(block.get("prompt") or "").strip():
                return block
    return None


def _agent_for_channel(channel_id: str) -> Optional[DuzhanAgent]:
    from app.duzhan_admin import runtime

    for agent in runtime.load_enabled_agents():
        group = runtime.group_of(agent)
        if group is not None and group.channel_id == channel_id:
            return agent
    return None


def _digest(group, hour: int, day: str, ledger: dict | None) -> str:
    """给模型的事实清单（用的都是现有渲染器已经在用的字段，缺数据会写"待确认"）。"""
    from app.duzhan import _daily_report_text, _perf_text
    from app.duzhan_ledger import people_for

    people = people_for(group.name, ledger) or []
    lines = [
        f"群：{group.name}",
        f"日期：{day}",
        f"档位：{hour:02d}:00（{_SLOT_TITLES.get(hour, '督战')}）",
        f"覆盖人数：{len(people)}",
    ]
    for person in people[:8]:
        display = str(person.get("display") or "")
        if not display:
            continue
        perf = re.sub(r"\s+", " ", _perf_text(person, "zh")).strip()[:80]
        report = re.sub(r"\s+", " ", _daily_report_text(person, "zh")).strip()[:60]
        lines.append(f"- {display}：{perf}；{report}")
    return "\n".join(lines)


def _build_messages(group, hour: int, day: str, ledger: dict | None, rule: Block) -> list[dict[str, str]]:
    system = (
        "你是跨境电商团队的督战助理。你只写一句话：这一档要求每个人做什么。"
        "要求：中文；一行；不超过 60 字；像主管当面催办；不要称呼、不要 emoji、不要引号、不要换行、不要编号。"
        "直接输出这句话本身，不要推理过程、不要解释、不要复述要求。"
    )
    user = (
        f"{_digest(group, hour, day, ledger)}\n\n"
        f"这一档的额外要求（由配置页填写）：{str(rule.get('prompt') or '').strip()}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _clean(text: str) -> str:
    """把模型输出收拾成一行短句；收拾完不像话就返回空串。"""
    line = re.sub(r"\s+", " ", str(text or "")).strip()
    line = line.strip("“”'\"*-—• ").strip()
    line = re.sub(r"^(本档动作|动作|Focus)\s*[：:·-]?\s*", "", line)
    line = re.sub(r"[。；;]+$", "", line).strip()
    if len(line) > MAX_CHARS:
        line = line[:MAX_CHARS].rstrip()
    if len(line) < 4:
        return ""
    return line


def _cache_path(agent_id: int, day: str, hour: int) -> Path:
    return _cache_dir() / f"agent{agent_id}_{day}_{hour:02d}.txt"


def _counter_path(day: str) -> Path:
    return _cache_dir() / f"calls_{day}.json"


def _daily_calls(day: str) -> int:
    try:
        return int(json.loads(_counter_path(day).read_text(encoding="utf-8")).get("calls") or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def _bump_daily_calls(day: str) -> int:
    calls = _daily_calls(day) + 1
    try:
        _counter_path(day).write_text(json.dumps({"calls": calls}), encoding="utf-8")
    except OSError as exc:
        logger.warning("AI 规则调用计数写入失败: {}", exc)
    return calls


def _generate(messages: list[dict[str, str]]) -> str:
    """真正调模型（单独抽出来，便于测试替换）。"""
    from app.agents.llm_client import supervisor_client

    client = supervisor_client()
    max_tokens = _limit("PDCA_DUZHAN_AI_MAX_TOKENS", DEFAULT_MAX_TOKENS)
    try:
        timeout = float(os.environ.get("PDCA_DUZHAN_AI_TIMEOUT_SECONDS", "") or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS
    client.timeout = timeout
    budget = max_tokens or DEFAULT_MAX_TOKENS
    content = _content_of(client.chat(messages, max_tokens=budget, temperature=0.3))
    if content.strip():
        return content
    # 推理模型偶尔把预算全花在 reasoning 上（finish_reason=length），content 为空：
    # 再给一次 4 倍预算；还是空就让调用方回落固定文案。
    logger.warning("AI 规则首次返回为空（max_tokens={}），用 4 倍预算重试一次", budget)
    return _content_of(client.chat(messages, max_tokens=budget * 4, temperature=0.3))


def _content_of(data: dict) -> str:
    """兼容两种返回：`supervisor_client().chat()` 归一化后的 {content, usage}，
    以及标准 OpenAI 形状的 {choices: [{message: {content}}]}。"""
    if not isinstance(data, dict):
        return ""
    if "content" in data:
        return str(data.get("content") or "")
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return str(message.get("content") or "")


def focus_for(group, hour: int, day: str, ledger: dict | None = None) -> Optional[str]:
    """这一档的「本档动作」文案：配了 AI 规则就用模型生成，否则 None（走固定文案）。"""
    from app.duzhan_admin import runtime

    if not runtime.using_db():
        return None
    agent = _agent_for_channel(group.channel_id)
    if agent is None:
        return None
    rule = ai_rule_of(agent)
    if rule is None:
        return None

    cache = _cache_path(agent.id, day, hour)
    if cache.is_file():
        try:
            cached = _clean(cache.read_text(encoding="utf-8"))
        except OSError:
            cached = ""
        if cached:
            return cached

    limit = _limit("PDCA_DUZHAN_AI_DAILY_LIMIT", DEFAULT_DAILY_LIMIT)
    calls = _daily_calls(day)
    if limit and calls >= limit:
        logger.warning("AI 规则今日调用已达上限 {}，{} 改用固定文案", limit, group.name)
        return None

    try:
        _bump_daily_calls(day)
        raw = _generate(_build_messages(group, hour, day, ledger, rule))
    except Exception as exc:
        logger.warning("AI 规则生成失败（改用固定文案）{} {}: {}", group.name, hour, exc)
        return None

    text = _clean(raw)
    if not text:
        logger.warning("AI 规则生成结果不可用（改用固定文案）{} {}", group.name, hour)
        return None
    try:
        cache.write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.warning("AI 规则缓存写入失败: {}", exc)
    logger.info("AI 规则已生成 {} {} → {}", group.name, f"{hour:02d}:00", text[:60])
    return text


def status() -> dict:
    """给接口/巡检用：今天调了多少次、缓存了几条。"""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        cached = len(list(_cache_dir().glob(f"*_{today}_*.txt")))
    except OSError:
        cached = 0
    return {"day": today, "calls": _daily_calls(today), "cached": cached,
            "limit": _limit("PDCA_DUZHAN_AI_DAILY_LIMIT", DEFAULT_DAILY_LIMIT)}
