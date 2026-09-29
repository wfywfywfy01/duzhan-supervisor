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

#: 渲染器标识：达标群日报 / 跟进群日报（各自复用现有渲染函数，不重写）
RENDERER_GROUP = "group_brief.renderer"
RENDERER_FOLLOW = "follow_brief.renderer"

PREVIEW_NOTE = "结构试跑：只解析配置与校验，不取数、不发消息。"


def blocks_of(agent: DuzhanAgent) -> list[Block]:
    """读出积木列表（坏 JSON 按空配置处理）。"""
    return parse_blocks(agent.blocks_json)


def roster_by_group() -> dict[str, list[str]]:
    """达标群 → 名单（名单只有一份，配置里不另存）。"""
    from app.adapters.roster import roster_by_group as _roster

    return _roster()


def roster_names() -> list[str]:
    """督战名单：达标群成员 + 跟进群主。

    跟进群主通常在另一份表里；不并进来，导入的跟进群配置会被自己判成
    「不在督战名单内」而无法启用（生产上踩过这个坑）。
    """
    from app.adapters.roster import roster_names as _names

    return _names()


def strategy_ids() -> list[str]:
    """当前在查的策略 id。"""
    try:
        from app.adapters.strategies import strategy_ids as _ids

        return _ids()
    except Exception as exc:  # noqa: BLE001 — 策略清单坏掉不该挡住配置页
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
    """这份配置会走哪个渲染器：群在跟进群名单里走 follow_brief，否则走达标群。"""
    from app.adapters.groups import FOLLOW_CHANNELS

    for block in blocks:
        if block.type == "group" and str(block.get("channel_id") or "") in FOLLOW_CHANNELS:
            return RENDERER_FOLLOW
    return RENDERER_GROUP


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


#: 达标群现在发的取数节（导入时按这些生成 source 块）
DUZHAN_SEED_SOURCES = ["msg_summary", "quote_ocr", "im_trace", "daily_report", "meeting_notes"]
#: 跟进群现在发的取数节
FOLLOW_SEED_SOURCES = ["msg_summary"]

#: 默认推送档位（一天三追：早定任务 / 中追变化 / 晚验兑现）
DEFAULT_SLOTS = ["10:00", "15:00", "20:00"]


def seed_from_code(session: Session) -> dict:
    """把现在硬编码的群导成配置；一律 **默认停用**，避免导入即双跑。"""
    from app.adapters.groups import FOLLOW_GROUPS, GROUPS

    duzhan_slots = list(DEFAULT_SLOTS)
    ctob_slots = list(DEFAULT_SLOTS)
    groups = roster_by_group()

    plans: list[dict[str, Any]] = []
    for group in GROUPS:
        plans.append(
            {
                "name": f"{group['name']}督战官",
                "timezone": group["tz"],
                "blocks": _seed_blocks(
                    channel_id=group["channel_id"],
                    label=group["name"],
                    lang=group["lang"],
                    slots=duzhan_slots,
                    people=groups.get(group["name"], []),
                    sources=DUZHAN_SEED_SOURCES,
                ),
            }
        )
    for owner in FOLLOW_GROUPS:
        plans.append(
            {
                "name": f"{owner['display']}跟进群督战官",
                "timezone": "Asia/Shanghai",
                "blocks": _seed_blocks(
                    channel_id=owner["channel_id"],
                    label=f"{owner['display']}跟进群",
                    lang="zh",
                    slots=ctob_slots,
                    people=[owner["display"]],
                    sources=FOLLOW_SEED_SOURCES,
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
