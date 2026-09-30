# -*- coding: utf-8 -*-
"""配置 → 运行时（code / db 双源）。

- `code`：维持现状，群清单写死在 `app.duzhan.GROUPS` / `app.ctob.OWNERS`。
- `db`：读 `duzhan_agents` 里 **启用** 的子 Agent，一个子 Agent 一个群，
  按它的 `timezone` + `times.slots` 注册调度；台账与幂等键都按子 Agent 分档。

这一层只做「翻译 + 注册」，不取数、不推送；真正发送仍走现有
`run_duzhan` / `run_ctob`（复用同一套渲染器）。
"""
from __future__ import annotations

from typing import Optional

from loguru import logger
from sqlmodel import Session, select

from app.duzhan import TZ_SHANGHAI, DuzhanGroup, parse_hours
from app.duzhan_blocks import Block, as_str_list, parse_blocks
from app.models.duzhan_agent import DuzhanAgent

CONFIG_SOURCE_CODE = "code"
CONFIG_SOURCE_DB = "db"


def config_source() -> str:
    """当前配置来源：code | db（认不出来的值一律当 code，宁可维持现状）。"""
    try:
        from app.config import get_settings

        value = str(getattr(get_settings(), "duzhan_config_source", CONFIG_SOURCE_CODE) or "").strip().lower()
    except Exception:  # noqa: BLE001 — 配置没起来时按现状走
        value = ""
    return value if value in {CONFIG_SOURCE_CODE, CONFIG_SOURCE_DB} else CONFIG_SOURCE_CODE


def using_db() -> bool:
    return config_source() == CONFIG_SOURCE_DB


def load_enabled_agents() -> list[DuzhanAgent]:
    """读启用的子 Agent；库不可用时不抛异常，返回空并告警。"""
    try:
        from app.database import get_engine

        with Session(get_engine()) as session:
            statement = select(DuzhanAgent).where(DuzhanAgent.enabled == True).order_by(DuzhanAgent.id)  # noqa: E712
            return list(session.exec(statement).all())
    except Exception as exc:  # noqa: BLE001 — 配置表读不到不该拖垮整个调度
        logger.warning("读取督战官配置失败，本轮按空配置处理: {}", exc)
        return []


def _blocks(agent: DuzhanAgent) -> list[Block]:
    return parse_blocks(agent.blocks_json)


def _first(blocks: list[Block], kind: str) -> Optional[Block]:
    return next((block for block in blocks if block.type == kind), None)


def group_of(agent: DuzhanAgent) -> Optional[DuzhanGroup]:
    """子 Agent → 督战群（群名 / 群 ID / 语言 / 时区）。"""
    blocks = _blocks(agent)
    group = _first(blocks, "group")
    if group is None:
        return None
    channel_id = str(group.get("channel_id") or "").strip()
    if not channel_id:
        return None
    style = _first(blocks, "style")
    lang = str(style.get("lang") or "zh").strip() if style else "zh"
    label = str(group.get("label") or "").strip() or agent.name
    tz_name = str(agent.timezone or "").strip() or TZ_SHANGHAI
    return DuzhanGroup(name=label, channel_id=channel_id, lang=lang, tz=tz_name)


def slots_of(agent: DuzhanAgent) -> list[int]:
    """子 Agent 的推送档位（只认 10/15/20，和引擎一致）。"""
    times = _first(_blocks(agent), "times")
    if times is None:
        return []
    return parse_hours(as_str_list(times.get("slots")))


def ledger_job_name(agent: DuzhanAgent, *, collect: bool) -> str:
    """台账 job 名：按子 Agent 分档，同群同时刻互不抢 claim。"""
    suffix = "collect" if collect else "push"
    return f"duzhan_{suffix}:agent:{agent.id}"


def _follow_owners_by_channel() -> dict:
    from app.ctob import OWNERS as FOLLOW_OWNERS

    return {owner.channel_id: owner for owner in FOLLOW_OWNERS}


def duzhan_agents() -> list[tuple[DuzhanAgent, DuzhanGroup]]:
    """启用、合法、且不是跟进群的子 Agent。"""
    follow_channels = set(_follow_owners_by_channel())
    picked: list[tuple[DuzhanAgent, DuzhanGroup]] = []
    for agent in load_enabled_agents():
        group = group_of(agent)
        if group is None:
            logger.warning("子 Agent #{} {} 缺少可用的 group 块，跳过注册", agent.id, agent.name)
            continue
        if group.channel_id in follow_channels:
            continue
        if not slots_of(agent):
            logger.warning("子 Agent #{} {} 没有可用档位（只支持 10:00/15:00/20:00），跳过注册", agent.id, agent.name)
            continue
        picked.append((agent, group))
    return picked


def groups_for_tz(tz_name: str) -> list[DuzhanGroup]:
    """db 源：该时区下所有启用子 Agent 的群。"""
    return [group for _agent, group in duzhan_agents() if group.tz == tz_name]


def cron_timezones() -> list[str]:
    seen: list[str] = []
    for _agent, group in duzhan_agents():
        if group.tz not in seen:
            seen.append(group.tz)
    return seen


def follow_agents() -> list[tuple[DuzhanAgent, object]]:
    """启用且指向跟进群（C转B）的子 Agent，配对现有的跟进群负责人。"""
    by_channel = _follow_owners_by_channel()
    picked: list[tuple[DuzhanAgent, object]] = []
    for agent in load_enabled_agents():
        group = group_of(agent)
        if group is None:
            continue
        owner = by_channel.get(group.channel_id)
        if owner is None:
            continue
        if not slots_of(agent):
            logger.warning("跟进群子 Agent #{} {} 没有可用档位，跳过注册", agent.id, agent.name)
            continue
        picked.append((agent, owner))
    return picked


def register_agent_jobs(scheduler) -> list[dict]:
    """db 源：按子 Agent 注册调度，返回注册清单（日志与测试都用它）。

    每个子 Agent 一条采集 + 一条推送 + 一条 +30 分钟兜底；台账 job 名与
    快照键都按子 Agent 分档，同群同时刻互不抢 claim、互不覆盖快照。
    """
    from zoneinfo import ZoneInfo

    from app.config import get_settings
    from app.duzhan import collect_clock
    from app.scheduler.jobs import ctob_job, duzhan_collect_job, duzhan_job

    lead = int(getattr(get_settings(), "duzhan_lead_minutes", 15) or 15)
    registered: list[dict] = []

    for agent, group in duzhan_agents():
        zone = ZoneInfo(group.tz)
        slug = f"agent{agent.id}"
        hours = slots_of(agent)
        for hour in hours:
            collect_hour, collect_minute = collect_clock(hour, lead)
            backup_hour, backup_minute = collect_clock(hour, -30)
            channel_ids = [group.channel_id]
            scheduler.add_job(
                duzhan_collect_job,
                args=[group.tz, hour],
                kwargs={"ledger_job": ledger_job_name(agent, collect=True), "channel_ids": channel_ids},
                trigger="cron",
                hour=collect_hour,
                minute=collect_minute,
                day_of_week="mon-sun",
                timezone=zone,
                id=f"duzhan_collect_{slug}_{hour:02d}",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )
            push_kwargs = {"ledger_job": ledger_job_name(agent, collect=False), "channel_ids": channel_ids}
            scheduler.add_job(
                duzhan_job,
                args=[group.tz, hour],
                kwargs=push_kwargs,
                trigger="cron",
                hour=hour,
                minute=0,
                day_of_week="mon-sun",
                timezone=zone,
                id=f"duzhan_{slug}_{hour:02d}",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )
            scheduler.add_job(
                duzhan_job,
                args=[group.tz, hour],
                kwargs=push_kwargs,
                trigger="cron",
                hour=backup_hour,
                minute=backup_minute,
                day_of_week="mon-sun",
                timezone=zone,
                id=f"duzhan_backup_{slug}_{hour:02d}",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )
        registered.append(
            {
                "kind": "duzhan",
                "agent_id": agent.id,
                "name": agent.name,
                "channel_id": group.channel_id,
                "timezone": group.tz,
                "slots": hours,
            }
        )

    for agent, owner in follow_agents():
        hours = slots_of(agent)
        kwargs = {"owners": (owner,), "ledger_job": f"ctob:agent:{agent.id}"}
        for hour in hours:
            backup = hour * 60 + 30
            scheduler.add_job(
                ctob_job,
                args=[hour],
                kwargs=kwargs,
                trigger="cron",
                hour=hour,
                minute=0,
                day_of_week="mon-sun",
                timezone=ZoneInfo(TZ_SHANGHAI),
                id=f"ctob_agent{agent.id}_{hour:02d}",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )
            scheduler.add_job(
                ctob_job,
                args=[hour],
                kwargs=kwargs,
                trigger="cron",
                hour=(backup // 60) % 24,
                minute=backup % 60,
                day_of_week="mon-sun",
                timezone=ZoneInfo(TZ_SHANGHAI),
                id=f"ctob_agent{agent.id}_backup_{hour:02d}",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )
        registered.append(
            {
                "kind": "follow",
                "agent_id": agent.id,
                "name": agent.name,
                "channel_id": owner.channel_id,
                "timezone": TZ_SHANGHAI,
                "slots": hours,
            }
        )

    if not registered:
        logger.warning(
            "督战官配置源=db，但库里没有可注册的启用子 Agent：本次不会注册任何督战推送。"
            "若不是本意，请检查配置页，或把 PDCA_DUZHAN_CONFIG_SOURCE 切回 code。"
        )
    for row in registered:
        logger.info(
            "督战官注册【{}】{} → {}（{}，档位 {}）",
            row["kind"], row["name"], row["channel_id"], row["timezone"], row["slots"],
        )
    return registered

