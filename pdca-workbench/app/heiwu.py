# -*- coding: utf-8 -*-
"""小黑屋（直营门店销售提升群）自动运维：开单晒单自动祝贺。

背景（老板 2026-09-30）："小黑屋那个群有人成单了，机器人没反应"。
群里本来就有「小黑屋管理员」机器人（群公告写着"机器人会协助你出群"），
但一直没人驱动它 —— 这个模块负责驱动：

- 轮询群消息，识别**晒单**（成交 / 开单 / 已开单），作业贴和打卡贴不算；
- 以「小黑屋管理员」身份，在**那条消息下面**回复：祝贺 + 金额 + 红包档位 + 出群提示；
- 同一条消息只回一次（游标 + 幂等键）；机器人自己发的不回。

规则来自群公告：开单奖金 18.8 元；销售金额 ≥1W 可退出小黑屋，
红包 ≥1W 18.8 / ≥5W 38.8 / ≥10W 68.8；月底仍未开单扣 100（新人首月保护）。

没配 PDCA_HEIWU_BOT_APP_ID / PDCA_HEIWU_BOT_APP_SECRET 时整条链路安静跳过（不报错、不发消息）。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

TZ_SHANGHAI = "Asia/Shanghai"
DEFAULT_CHANNEL_ID = "66666666-6666-4666-8666-666666666666"  # Vertu  小黑屋
CURSOR_FILE = "runtime/heiwu_cursor.json"
NAMES_FILE = "runtime/heiwu_names.json"

#: 晒单关键词；作业/复盘/打卡贴里经常也出现"成交卡点""预估开单"，所以先排噪声
DEAL_RE = re.compile(r"成交|开单|出单|已下单")
NOISE_RE = re.compile(r"作业|打卡|复盘|意向客户|跟进情况|改进措施|无效客户|学习任务")
#: 金额：13800 / 3.8w / 136000 / 1.2万
AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万|w|W|k|K)?")

#: 红包档位（群公告口径）
REWARD_TIERS: tuple[tuple[int, str], ...] = (
    (100000, "68.8 元"),
    (50000, "38.8 元"),
    (10000, "18.8 元"),
)
OPEN_REWARD = "18.8 元"  # 只要有开单就有


def channel_id() -> str:
    return os.environ.get("PDCA_HEIWU_CHANNEL_ID", "").strip() or DEFAULT_CHANNEL_ID


def bot_credentials() -> tuple[str, str]:
    return (
        os.environ.get("PDCA_HEIWU_BOT_APP_ID", "").strip(),
        os.environ.get("PDCA_HEIWU_BOT_APP_SECRET", "").strip(),
    )


def bot_credentials_effective() -> tuple[str, str]:
    """实际使用的机器人凭证：小黑屋专用优先，没配就回退到现有督战官机器人。

    老板 2026-09-30："你自己现有的不能祝贺吗" —— 能，只要那个机器人已在群里
    （vps-work im +add-bot）。等拿到「小黑屋管理员」的 App Secret，配上
    PDCA_HEIWU_BOT_APP_ID/SECRET 就自动切过去。
    """
    app_id, app_secret = bot_credentials()
    if app_id and app_secret:
        return app_id, app_secret
    return (
        os.environ.get("PDCA_DUZHAN_BOT_APP_ID", "").strip(),
        os.environ.get("PDCA_DUZHAN_BOT_APP_SECRET", "").strip(),
    )


def configured() -> bool:
    app_id, app_secret = bot_credentials_effective()
    return bool(app_id and app_secret)


def _data_dir() -> Path:
    from app.config import get_settings

    folder = get_settings().data_dir / "runtime"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def parse_deal(body: str) -> dict:
    """判断一条消息是不是晒单，并尽量把金额加起来。

    作业贴里会出现"预估开单时间""成交卡点"这类词，所以先排噪声；
    "已开单"这种没有金额的也算晒单（回复里提示补金额）。
    """
    text = str(body or "").strip()
    result: dict[str, Any] = {"is_deal": False, "amount": None, "declared_only": False, "text": text[:200]}
    if not text or NOISE_RE.search(text) or not DEAL_RE.search(text):
        return result
    result["is_deal"] = True
    amounts: list[float] = []
    for number, unit in AMOUNT_RE.findall(text):
        try:
            value = float(number)
        except ValueError:
            continue
        if unit in ("万",):
            value *= 10000
        elif unit in ("w", "W"):
            value *= 10000
        elif unit in ("k", "K"):
            value *= 1000
        elif value < 1000:
            continue  # 一台/2台/日期这些小数不算金额
        amounts.append(value)
    if amounts:
        result["amount"] = sum(amounts)
    else:
        result["declared_only"] = True
    return result


def reward_for(amount: Optional[float]) -> Optional[str]:
    """按金额给红包档位；金额未知时只给"开单奖励"。"""
    if not amount:
        return None
    for threshold, reward in REWARD_TIERS:
        if amount >= threshold:
            return reward
    return None


def money_text(amount: Optional[float]) -> str:
    """群里习惯直接写数字（13800 / 136000），这里照抄，只加千分位。"""
    if not amount:
        return "金额待确认"
    return f"{amount:,.0f} 元"


def reply_text(name: str, parsed: dict) -> str:
    """祝贺文案（群公告口径：开单奖金 + ≥1W 出群 + 红包档位）。"""
    who = (name or "").strip()
    head = f"🎉 恭喜{who}开单！" if who else "🎉 恭喜开单！"
    amount = parsed.get("amount")
    lines = [head]
    if amount:
        tail = "（按你上一条报的金额）" if parsed.get("amount_from_previous") else ""
        lines.append(f"本次：{money_text(amount)}{tail}")
    else:
        lines.append("本次：金额待确认（把成交金额补一句，我好按档位给红包）")
    reward = reward_for(amount)
    if amount and amount >= 10000:
        lines.append(f"已达标：可以退出小黑屋 ✅ 红包 {reward or OPEN_REWARD}")
        lines.append("我这边记上了，稍后协助你出群；截图/单号留好备查。")
    else:
        lines.append(f"开单奖励 {OPEN_REWARD}；再累计到 1W 就能出群。")
    return "\n".join(lines)


def _cursor_path() -> Path:
    return _data_dir() / "heiwu_cursor.json"


def _names_path() -> Path:
    return _data_dir() / "heiwu_names.json"


def _load_cursor() -> dict:
    try:
        payload = json.loads(_cursor_path().read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _save_cursor(payload: dict) -> None:
    try:
        _cursor_path().write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("小黑屋游标写入失败: {}", exc)


def _load_names() -> dict:
    try:
        payload = json.loads(_names_path().read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def refresh_names() -> dict:
    """拉一次群成员名单（user_id → 姓名），缓存到当天。名单拉不到不影响功能。"""
    from app.vertu.client import run_vertu_sync_json

    payload = run_vertu_sync_json(["im", "+channels", "--limit", "200"], timeout=90.0)
    if payload is None:
        logger.warning("小黑屋成员名单拉取失败，沿用本地缓存")
        return _load_names()
    items = payload.get("channels") if isinstance(payload, dict) else payload
    names: dict[str, str] = {}
    for channel in items or []:
        if not isinstance(channel, dict) or channel.get("id") != channel_id():
            continue
        for member in channel.get("members") or []:
            if not isinstance(member, dict):
                continue
            user_id = member.get("user_id")
            label = member.get("employee_name") or member.get("name") or member.get("nickname")
            if user_id and label:
                names[str(user_id)] = str(label)
    if names:
        names["_cached_at"] = datetime.now(timezone.utc).isoformat()
        try:
            _names_path().write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            logger.warning("小黑屋成员名单写入失败: {}", exc)
    return names


def _minutes_between(earlier: str, later: str) -> float:
    """两条消息相差多少分钟（解析不出来就当很久以前，避免误用旧金额）。"""
    try:
        first = datetime.fromisoformat(earlier.replace("Z", "+00:00"))
        second = datetime.fromisoformat(later.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 10_000.0
    return abs((second - first).total_seconds()) / 60.0


def _name_of(user_id: Any, names: dict) -> str:
    if user_id is None:
        return ""
    cached_at = names.get("_cached_at") or ""
    if not cached_at or datetime.now(timezone.utc) - datetime.fromisoformat(cached_at) > timedelta(days=1):
        names = refresh_names()
    return str(names.get(str(user_id)) or "")


def fetch_recent(hours: int = 24) -> list[dict]:
    """拉最近的消息（默认 24 小时）。"""
    from app.vertu.client import run_vertu_sync_json

    date_from = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = run_vertu_sync_json(
        ["im", "+history", "--channel-id", channel_id(), "--date-from", date_from, "--limit", "100"],
        timeout=90.0,
    )
    if payload is None:
        logger.warning("小黑屋拉历史失败")
        return []
    items = payload.get("messages") if isinstance(payload, dict) else payload
    return [item for item in (items or []) if isinstance(item, dict)]


def _reply(body: str, parent_message_id: str, idempotency_key: str) -> bool:
    from app.vps_im_push import push_as_bot

    app_id, app_secret = bot_credentials_effective()
    return push_as_bot(
        body,
        channel_id(),
        app_id=app_id,
        app_secret=app_secret,
        parent_message_id=parent_message_id,
        idempotency_key=idempotency_key,
    )


def poll_once(*, hours: int = 24, dry_run: bool = False) -> dict:
    """扫一轮：新的晒单就回一条祝贺。dry_run 只算不发。"""
    if not configured():
        return {"skipped": "not_configured"}

    cursor = _load_cursor()
    replied: list[str] = list(cursor.get("replied_ids") or [])
    replied_set = set(replied)
    last_seen = str(cursor.get("last_created_at") or "")
    messages = fetch_recent(hours=hours)
    if not messages:
        return {"scanned": 0, "replied": []}

    messages.sort(key=lambda item: str(item.get("created_at") or ""))
    if not last_seen:
        # 首轮只记游标、不回历史晒单（和督战官 @ 轮询同一约定）：
        # 否则一上线就会把几天前的旧单全祝贺一遍。
        cursor["last_created_at"] = str(messages[-1].get("created_at") or "")
        cursor["replied_ids"] = []
        if not dry_run:
            _save_cursor(cursor)
        logger.info("小黑屋首次运行：只记游标到 {}，不回历史", cursor["last_created_at"])
        return {"scanned": len(messages), "replied": [], "seeded": True}

    names: dict = {}
    sent: list[str] = []
    newest = last_seen
    #: 同一个人上一条晒单的金额：群里常见的写法是先发"成交：…13800"，再补一句"已开单"，
    #: 所以没有金额的申报，回看 30 分钟内同一个人的上一条金额。
    last_amount: dict[Any, tuple[str, float]] = {}
    for message in messages:
        created = str(message.get("created_at") or "")
        newest = max(newest, created)
        message_id = str(message.get("id") or "")
        if not message_id or message_id in replied_set:
            continue
        if last_seen and created <= last_seen:
            continue
        if message.get("sender_bot_id") or message.get("sender_type") == "bot":
            continue  # 机器人自己发的不回
        parsed = parse_deal(str(message.get("body") or ""))
        if not parsed["is_deal"]:
            continue
        sender = message.get("sender_user_id")
        if not parsed["amount"] and sender in last_amount:
            previous_at, previous_amount = last_amount[sender]
            if _minutes_between(previous_at, created) <= 30:
                parsed = {**parsed, "amount": previous_amount, "amount_from_previous": True}
        if parsed["amount"]:
            last_amount[sender] = (created, float(parsed["amount"]))
        if not names:
            names = _load_names()
        name = _name_of(message.get("sender_user_id"), names)
        body = reply_text(name, parsed)
        if dry_run:
            logger.info("小黑屋 dry-run：会对 {} 回「{}」", name or message.get("sender_user_id"), body.replace(chr(10), " / "))
        else:
            ok = _reply(body, message_id, f"heiwu-{message_id}")
            if not ok:
                logger.warning("小黑屋祝贺发送失败 {} {}", name, message_id)
                continue
        replied.append(message_id)
        replied_set.add(message_id)
        sent.append(message_id)

    cursor["last_created_at"] = newest
    cursor["replied_ids"] = replied[-200:]
    if not dry_run:
        _save_cursor(cursor)
    if sent:
        logger.info("小黑屋已祝贺 {} 条晒单", len(sent))
    return {"scanned": len(messages), "replied": sent}
