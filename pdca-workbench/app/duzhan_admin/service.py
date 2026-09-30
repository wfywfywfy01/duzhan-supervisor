# -*- coding: utf-8 -*-
"""督战官子 Agent 配置服务层：解析、校验、结构试跑、从代码导入。

这一层**不取数、不推送**：试跑只把配置翻译成「运行时打算怎么做」。
真正按配置发送要等运行接管（调度从库注册）之后。
"""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger
from sqlmodel import Session, select

from app.duzhan_blocks import (
    CONDITION_KEYS,
    SOURCE_KEYS,
    Block,
    as_str_list,
    blocks_dict,
    dump_blocks,
    parse_blocks,
    validate_blocks,
)
from app.models.duzhan_agent import DuzhanAgent

RENDERER_DUZHAN = "duzhan.render_brief"
RENDERER_CTOB = "ctob.render_brief"

PREVIEW_NOTE = "结构试跑：只解析配置与校验，不取数、不发消息。"


def blocks_of(agent: DuzhanAgent) -> list[Block]:
    """读出积木列表（坏 JSON 按空配置处理）。"""
    return parse_blocks(agent.blocks_json)


def roster_by_group() -> dict[str, list[str]]:
    """达标群 → 名单；名单只在 duzhan_ledger 里维护一份，配置里不另存。"""
    from app.duzhan_ledger import OWNERS

    table: dict[str, list[str]] = {}
    for owner in OWNERS:
        table.setdefault(owner.group, []).append(owner.display)
    return table


def roster_names() -> list[str]:
    """督战名单：达标群成员 + C转B 群主。

    C转B 群主只在 ctob.OWNERS 里，不在 duzhan_ledger 里；不并进来，导入的
    C转B 配置会全部被判「不在督战名单内」而无法启用。
    """
    names = [name for names in roster_by_group().values() for name in names]
    from app.ctob import OWNERS as CTOB_OWNERS

    for owner in CTOB_OWNERS:
        if owner.display and owner.display not in names:
            names.append(owner.display)
    return names


def strategy_ids() -> list[str]:
    """当前在查的策略 id（运行时文件优先）。"""
    try:
        from app.strategy_wa_brief import load_strategies

        return [item.id for item in load_strategies()[0]]
    except Exception as exc:  # noqa: BLE001 — 策略文件坏掉不该挡住配置页
        logger.warning("读取策略清单失败，跳过策略校验: {}", exc)
        return []


def owners_for(blocks: list[Block]) -> Optional[list[str]]:
    """按 group 块的群名取该群名单；认不出群名就用全量名单。"""
    table = roster_by_group()
    picked: list[str] = []
    for block in blocks:
        if block.type != "group":
            continue
        picked.extend(table.get(str(block.get("label") or ""), []))
    picked = picked or roster_names()
    return picked or None


def validate_agent(
    agent: DuzhanAgent,
    *,
    owners: Optional[list[str]] = None,
    strategies: Optional[list[str]] = None,
) -> list[str]:
    """校验一个子 Agent 的配置，返回错误列表（不抛异常）。"""
    blocks = blocks_of(agent)
    return validate_blocks(
        blocks,
        owners=owners_for(blocks) if owners is None else owners,
        strategies=strategy_ids() if strategies is None else strategies,
    )


def _slots_of(blocks: list[Block]) -> list[str]:
    times = next((block for block in blocks if block.type == "times"), None)
    return as_str_list(times.get("slots")) if times else []


def _people_of(blocks: list[Block]) -> list[str]:
    people = next((block for block in blocks if block.type == "people"), None)
    return as_str_list(people.get("names")) if people else []


def _style_of(blocks: list[Block]) -> dict:
    style = next((block for block in blocks if block.type == "style"), None)
    return {
        "lang": str(style.get("lang") or "zh") if style else "zh",
        "title": str(style.get("title") or "") if style else "",
        "footer": str(style.get("footer") or "") if style else "",
    }


def renderer_for(blocks: list[Block]) -> str:
    """这份配置会走哪个渲染器：群在 C转B 名单里走 ctob，否则走达标群。"""
    from app.ctob import OWNERS as CTOB_OWNERS

    ctob_channels = {owner.channel_id for owner in CTOB_OWNERS}
    for block in blocks:
        if block.type == "group" and str(block.get("channel_id") or "") in ctob_channels:
            return RENDERER_CTOB
    return RENDERER_DUZHAN


def resolve_spec(agent: DuzhanAgent) -> dict:
    """配置 → 运行时结构（不含任何实时数据）。"""
    blocks = blocks_of(agent)
    style = _style_of(blocks)
    rules: list[dict] = []
    for block in blocks:
        if block.type != "rule":
            continue
        mode = str(block.get("mode") or "text")
        rules.append(
            {
                "mode": mode,
                "when": str(block.get("when") or "always"),
                "text": str(block.get("text") or "") if mode == "text" else "",
                "prompt": str(block.get("prompt") or "") if mode == "ai" else "",
            }
        )
    return {
        "id": agent.id,
        "name": agent.name,
        "enabled": bool(agent.enabled),
        "timezone": agent.timezone or "Asia/Shanghai",
        "groups": [
            {"channel_id": str(block.get("channel_id") or ""), "label": str(block.get("label") or "")}
            for block in blocks
            if block.type == "group"
        ],
        "slots": _slots_of(blocks),
        "people": _people_of(blocks),
        "sources": [str(block.get("key") or "") for block in blocks if block.type == "source"],
        "rules": rules,
        "conditions": [
            {"key": str(block.get("key") or ""), "value": block.get("value") is True}
            for block in blocks
            if block.type == "condition"
        ],
        "recipients": [
            {"kind": str(block.get("kind") or ""), "id": str(block.get("id") or "")}
            for block in blocks
            if block.type == "recipient"
        ],
        "strategies": [
            {"id": str(block.get("id") or ""), "until": str(block.get("until") or "")}
            for block in blocks
            if block.type == "strategy"
        ],
        "lang": style["lang"],
        "title": style["title"],
        "footer": style["footer"],
        "renderer": renderer_for(blocks),
    }


def _summary_lines(spec: dict, day: Optional[str], hour: Optional[int]) -> list[str]:
    """把结构翻成给人看的一行行说明（配置页「试跑」直接展示这些）。"""
    lines: list[str] = []
    for group in spec["groups"]:
        label = group["label"] or "(未命名群)"
        lines.append(f"发送到：{label}（{group['channel_id'] or '缺 channel_id'}）")
    slots = list(spec["slots"])
    if hour is not None:
        picked = [slot for slot in slots if slot.split(":")[0].lstrip("0") == str(hour)]
        slots = picked or slots
    lines.append(f"档位：{'、'.join(slots) or '未配置'}（{spec['timezone']}）")
    if day:
        lines.append(f"试跑日期：{day}")
    lines.append(f"覆盖人员：{'、'.join(spec['people']) if spec['people'] else '该群全员（未限定）'}")
    lines.append(
        "数据源："
        + (" → ".join(SOURCE_KEYS.get(key, key) for key in spec["sources"]) if spec["sources"] else "未配置")
    )
    for rule in spec["rules"]:
        body = rule["text"] or f"AI 生成：{rule['prompt']}"
        lines.append(f"规则（{rule['when']}）：{body}")
    for condition in spec["conditions"]:
        key = CONDITION_KEYS.get(condition["key"], condition["key"])
        lines.append(f"触发条件：{key} = {'是' if condition['value'] else '否'}")
    for recipient in spec["recipients"]:
        lines.append(f"额外收件人：{recipient['kind']} {recipient['id']}")
    for strategy in spec["strategies"]:
        tail = f"（到 {strategy['until']}）" if strategy["until"] else ""
        lines.append(f"策略核查：{strategy['id']}{tail}")
    lines.append(f"渲染器：{spec['renderer']}")
    return lines


def preview_agent(agent: DuzhanAgent, *, day: Optional[str] = None, hour: Optional[int] = None) -> dict:
    """结构试跑：配置能解析成什么、有没有错、到点会怎么发。"""
    spec = resolve_spec(agent)
    errors = validate_agent(agent)
    return {
        "agent": {"id": agent.id, "name": agent.name, "enabled": bool(agent.enabled)},
        "day": day,
        "hour": hour,
        "errors": errors,
        "spec": spec,
        "summary": _summary_lines(spec, day, hour),
        "note": PREVIEW_NOTE,
    }


def _pick_hour(agent: DuzhanAgent, hour: Optional[int], now) -> int:
    """没指定档位时，选"最近一个已经到点的档"，都没有就取第一档。"""
    from app.duzhan_admin import runtime

    slots = runtime.slots_of(agent) or [10, 15, 20]
    if hour in slots:
        return int(hour)
    passed = [item for item in slots if item <= now.hour]
    return passed[-1] if passed else slots[0]


def render_preview(agent: DuzhanAgent, *, day: Optional[str] = None, hour: Optional[int] = None) -> dict:
    """真实内容试跑：渲染这条配置到点**实际会发出去的那条正文**。

    数据来源是最近一次组表快照（调度在整点前 15 分钟已经采过），**不重新取数、不发消息**：
    快照里有这条群的消息体就直接用（最忠实），没有就用快照里的台账重渲染，
    连台账都没有（今天还没跑过组表）就按空表渲染并明确标注。
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app import ctob as ctob_module
    from app import duzhan as duzhan_module
    from app.duzhan_admin import runtime
    from app.duzhan_ledger import empty_ledger

    tz_name = (agent.timezone or "Asia/Shanghai").strip() or "Asia/Shanghai"
    now = datetime.now(ZoneInfo(tz_name))
    day = day or now.strftime("%Y-%m-%d")
    hour = _pick_hour(agent, hour, now)

    group = runtime.group_of(agent)
    if group is None:
        return {"mode": "render", "ok": False, "error": "配置里没有可用的 group 块", "day": day, "hour": hour}

    owner = next((item for item in ctob_module.OWNERS if item.channel_id == group.channel_id), None)

    if owner is not None:
        # 跟进群：快照在 app.ctob 那边，按群存
        collected = ctob_module.load_snapshot(day, hour) or {}
        data = collected.get(group.channel_id) or {"summary": {}, "chats": [], "cohort": {}}
        prev_slot = ctob_module._prev_slot(day, hour)  # noqa: SLF001 — 同包内复用既有档位计算
        prev_data = None
        if prev_slot:
            prev_data = (ctob_module.load_snapshot(prev_slot[0], prev_slot[1]) or {}).get(group.channel_id)
        body = ctob_module.render_brief(
            owner, day, data["summary"], data["chats"], data["cohort"], hour=hour, prev=prev_data
        )
        source = "snapshot" if group.channel_id in collected else "empty_ledger"
        renderer = "ctob.render_brief"
    else:
        # 达标群：快照按（时区, 日期, 档位）存；db 源下还有按群分的快照
        slot_key = group.channel_id[:8]
        snapshot = duzhan_module.load_prepared(tz_name, hour, day, slot_key) or duzhan_module.load_prepared(
            tz_name, hour, day
        )
        ledger = snapshot.get("ledger") if isinstance(snapshot, dict) else None
        prev_hour = duzhan_module.prev_slot_hour(hour)
        prev = duzhan_module.load_prepared(tz_name, prev_hour, day) if prev_hour else None
        prev_ledger = prev.get("ledger") if isinstance(prev, dict) else None
        body = ""
        if isinstance(snapshot, dict):
            body = str((snapshot.get("messages") or {}).get(group.channel_id) or "")
        if body:
            source = "snapshot"
        else:
            from app.duzhan_admin import ai_rules

            if ledger is not None:
                use_ledger, source = ledger, "snapshot_ledger"
            else:
                use_ledger, source = empty_ledger(day), "empty_ledger"
            # 和到点发送走同一条渲染路径：配了 AI 规则块时，这里出来的就是模型那句
            override = ai_rules.focus_for(group, hour, day, use_ledger)
            body = duzhan_module.render_brief(
                group, hour, now.astimezone(ZoneInfo(group.tz)), use_ledger, prev_ledger,
                focus_override=override,
            )
        renderer = "duzhan.render_brief"

    notes = {
        "snapshot": "快照原文：这就是本档到点会发出去的那条（正文一字未改）。",
        "snapshot_ledger": "按本档快照里的台账重新渲染（快照里没有这条群的消息体）。",
        "empty_ledger": "今天还没有这个档位的组表快照，按空台账渲染：正文结构真实、数字会显示待确认。",
    }
    return {
        "mode": "render",
        "ok": True,
        "agent": {"id": agent.id, "name": agent.name, "enabled": bool(agent.enabled)},
        "day": day,
        "hour": hour,
        "timezone": tz_name,
        "channel_id": group.channel_id,
        "group": group.name,
        "renderer": renderer,
        "source": source,
        "chars": len(body),
        "body": body,
        "note": notes.get(source, ""),
    }


def agent_out(agent: DuzhanAgent) -> dict:
    """接口返回体：配置 + 当前错误（前端据此禁掉「启用」）。"""
    blocks = blocks_of(agent)
    return {
        "id": agent.id,
        "name": agent.name,
        "enabled": bool(agent.enabled),
        "timezone": agent.timezone,
        "note": agent.note,
        "blocks": blocks_dict(blocks),
        "errors": validate_agent(agent),
        "renderer": renderer_for(blocks),
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else "",
    }


def _seed_blocks(
    *,
    channel_id: str,
    label: str,
    lang: str,
    slots: list[str],
    people: list[str],
    sources: list[str],
) -> dict:
    blocks: list[dict] = [
        {"type": "group", "channel_id": channel_id, "label": label},
        {"type": "times", "slots": slots},
    ]
    if people:
        blocks.append({"type": "people", "names": people})
    for key in sources:
        blocks.append({"type": "source", "key": key})
    blocks.append({"type": "style", "lang": lang, "title": label})
    return {"blocks": blocks}


#: 达标群现有取数节：业绩 / MTO 报价图 / VPS 留痕 / 日报 / 会议
DUZHAN_SEED_SOURCES = ["wa_summary", "mto_ocr", "im_trace", "daily_report", "vemory"]
#: C转B 群现有取数节：WhatsApp 日汇总
CTOB_SEED_SOURCES = ["wa_summary"]


def seed_from_code(session: Session) -> dict:
    """把现在硬编码的群导成配置；一律 **默认停用**，避免导入即双跑。"""
    from app.config import get_settings
    from app.ctob import OWNERS as CTOB_OWNERS
    from app.duzhan import GROUPS

    settings = get_settings()
    duzhan_slots = list(getattr(settings, "duzhan_times", None) or ["10:00", "15:00", "20:00"])
    ctob_slots = list(getattr(settings, "ctob_times", None) or duzhan_slots)
    groups = roster_by_group()

    plans: list[dict[str, Any]] = []
    for group in GROUPS:
        plans.append(
            {
                "name": f"{group.name}督战官",
                "timezone": group.tz,
                "blocks": _seed_blocks(
                    channel_id=group.channel_id,
                    label=group.name,
                    lang=group.lang,
                    slots=duzhan_slots,
                    people=groups.get(group.name, []),
                    sources=DUZHAN_SEED_SOURCES,
                ),
            }
        )
    for owner in CTOB_OWNERS:
        plans.append(
            {
                "name": f"{owner.display}C转B跟进群督战官",
                "timezone": "Asia/Shanghai",
                "blocks": _seed_blocks(
                    channel_id=owner.channel_id,
                    label=f"{owner.display}C转B跟进群",
                    lang="zh",
                    slots=ctob_slots,
                    people=[owner.display],
                    sources=CTOB_SEED_SOURCES,
                ),
            }
        )

    existing = {row.name for row in session.exec(select(DuzhanAgent)).all()}
    created: list[DuzhanAgent] = []
    skipped: list[str] = []
    for plan in plans:
        if plan["name"] in existing:
            skipped.append(plan["name"])
            continue
        row = DuzhanAgent(
            name=plan["name"],
            enabled=False,
            timezone=plan["timezone"],
            blocks_json=dump_blocks(parse_blocks(plan["blocks"])),
            note="从代码导入（默认停用，核对后再启用）",
        )
        session.add(row)
        created.append(row)
    session.commit()
    for row in created:
        session.refresh(row)
    logger.info("从代码导入督战官配置：新增 {}，跳过 {}", len(created), len(skipped))
    return {"created": [agent_out(row) for row in created], "skipped": skipped}
