# -*- coding: utf-8 -*-
"""海外渠道督战官：按群时区在 10:00 / 15:00 / 20:00 推送达标战报。

Alice 群用欧洲/巴黎时间与英文；其余群用北京时间与中文。
"""

from __future__ import annotations

import json
import operator
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from loguru import logger

from app.alerting import notify
from app.config import get_settings
from app.duzhan_ledger import (
    TODAY_SLOGAN,
    collect_ledger,
    count_text,
    daily_target_text,
    diff_tasks,
    empty_ledger,
    hours_text,
    infer_status,
    people_for,
    wan_text,
)
from app.vps_im_push import push_duzhan_message

TZ_SHANGHAI = "Asia/Shanghai"
TZ_PARIS = "Europe/Paris"


@dataclass(frozen=True)
class DuzhanGroup:
    """一个达标督战群。"""

    name: str
    channel_id: str
    lang: str
    tz: str


GROUPS: tuple[DuzhanGroup, ...] = (
    DuzhanGroup(
        "新人小组业绩达标群",
        "11111111-1111-4111-8111-111111111111",
        "zh",
        TZ_SHANGHAI,
    ),
    DuzhanGroup(
        "张三业绩达标群",
        "22222222-2222-4222-8222-222222222222",
        "zh",
        TZ_SHANGHAI,
    ),
    DuzhanGroup(
        "李四业绩达标群",
        "33333333-3333-4333-8333-333333333333",
        "zh",
        TZ_SHANGHAI,
    ),
    DuzhanGroup(
        "Bella业绩达标群",
        "44444444-4444-4444-8444-444444444444",
        "zh",
        TZ_SHANGHAI,
    ),
    DuzhanGroup(
        "Alice业绩达标群",
        "55555555-5555-4555-8555-555555555555",
        "en",
        TZ_PARIS,
    ),
)

# 10:00 定任务 / 15:00 追变化 / 20:00 验兑现。
_SLOT_ZH = {
    10: (
        "早追·定任务",
        "每人提交今日 3–5 项工作（对象、交付物、截止时间）；未报则点名催报。",
    ),
    15: (
        "中追·追变化",
        "只报相对 10:00 的变化：完成、推进、停滞、未回复；卡点写清协同人和时限。",
    ),
    20: (
        "晚追·验兑现",
        "核验交付物与证据；未完成写原因并结转明早第一动作。",
    ),
}
_SLOT_EN = {
    10: (
        "Morning chase · lock today's work",
        "Post 3–5 must-do items (owner, deliverable, deadline); missing plan will be name-checked.",
    ),
    15: (
        "Midday chase · chase changes",
        "Only changes vs 10:00: done, progressed, stalled, no reply; blockers need owner and ETA.",
    ),
    20: (
        "Evening chase · verify delivery",
        "Verify deliverables and evidence; unfinished items roll to tomorrow's first action.",
    ),
}
_REPORTER = {
    "新人小组业绩达标群": "成员A / Sofia / 成员C / 成员D",
    "张三业绩达标群": "张三",
    "李四业绩达标群": "李四 / 赵六",
    "Bella业绩达标群": "Bella",
    "Alice业绩达标群": "Alice",
}

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_EN_PAIRS = (
    ("土耳其合同与伊斯坦布尔市场保护", "Turkey contract and Istanbul market protection"),
    ("今天与迪拜经销商讨论了土耳其合同。", "Discussed the Turkey contract with the Dubai distributor today. "),
    ("他看了合同后反馈，认为目前版本和之前的版本还是太相似，对他在伊斯坦布尔店铺的投资保护不够。", "He said this draft is still too similar to the last one and does not protect his Istanbul store investment enough. "),
    ("他希望公司在合同中加强市场保护，尤其是关于其他合作伙伴或经销商进入同一市场的问题。", "He wants stronger market protection in the contract, especially against other partners entering the same market."),
    ("他特别提出，如果有客户联系 VERTU 询问在伊斯坦布尔哪里可以购买产品，希望公司可以把客户引流到他的店铺。", "He asked that if a customer contacts VERTU about where to buy in Istanbul, we refer them to his store. "),
    ("我已告知他，这一点可以加入到协议中。", "I told him this can be added to the agreement."),
    ("再次跟进了迪拜当前订单的待付款情况，并推动他尽快确认付款状态。", "Followed up Dubai outstanding payment and pushed him to confirm status. "),
    ("我也向他说明 Gary 正在催促付款进展，所以我们需要尽快得到明确反馈。", "Told him Gary is chasing payment, so we need a clear answer soon. "),
    ("该付款目前仍在等待中，但预计会尽快安排。", "Payment is still pending but expected soon."),
    ("讨论了定制高端手机的报价，包括一款 Signature S+ 白金、全钻、深蓝色鳄鱼皮版本，零售价约 98,500 美金，付款后生产周期约 40–45 天。", "Discussed custom high-end quotes, including Signature S+ platinum / full diamond / dark-blue alligator, about USD 98,500, 40-45 days after payment."),
    ("讨论了迪拜下一步采购计划，包括后续订单以及经销商正在考虑的定制机型。", "Discussed Dubai next purchase plan, follow-on orders, and custom models under review."),
    ("讨论了英国市场的销售表现、年度目标，以及 VERTU London 需要公司提供哪些支持来提升销售。", "Discussed UK sales, annual targets, and support VERTU London needs."),
    ("讨论了本周预计完成的新订单，金额约 65,000 美金。", "Discussed a new order expected this week, about USD 65,000."),
    ("讨论了 BRABUS × VERTU 的合作机会，包括为限量版汽车项目定制手机。", "Discussed BRABUS x VERTU, including phones for a limited car project."),
    ("跟进了 Harrods 家具问题，以及英国零售展示所需的相关支持。", "Followed up the Harrods furniture issue and UK retail display support."),
    ("讨论了老款机型的价格和促销方案，包括 Meta 1、Meta 2 和 Quantum Flip 的折扣方案。", "Discussed legacy pricing/promos for Meta 1, Meta 2 and Quantum Flip."),
    ("讨论了 Harrods 专属/定制版 Agent Q 和 AlphaFold，包括 MOQ 要求。", "Discussed Harrods exclusive/custom Agent Q and AlphaFold, including MOQ."),
    ("讨论了为 Harrods VIP 客户直接发货到乌兹别克斯坦的安排，以及私人航空领域合作和 VERTU 汽车生态项目机会。", "Discussed direct ship to Uzbekistan for a Harrods VIP, plus private aviation and VERTU auto ecosystem opportunities."),
    ("由于迪拜接下来会同时为迪拜和土耳其进行较大数量的采购，他希望根据整体采购量，给予 40%的折扣。", "Because Dubai will buy in volume for both Dubai and Turkey, he wants a 40% discount on total volume. "),
    ("他并不是要求公司停止线上团队或线上销售。他只是希望合同中明确，在土耳其门店正式运营后，如果有客户联系公司询问在土耳其哪里可以购买 VERTU，我们可以向客户提供土耳其门店的地址，并引导客户到该门店购买。", "He is not asking to stop online sales. He wants the contract to say that after the Turkey store opens, inbound Turkey buyers can be directed to that store."),
    ("他们让我先向你确认并获得你对以下两点的批准，然后再加入合同：", "They asked me to get your approval on two points before adding them to the contract: "),
    ("他并不是要求公司停", "He is not asking the company to stop"),
    ("他看了合同后反馈", "He reviewed the contract and said"),
    ("认为目前版本和之前的版本还是太相似", "this draft is still too similar to the last one"),
    ("对他在伊斯坦布尔店铺的", "for his Istanbul store"),
    ("今日WhatsApp触达0", "WhatsApp reach 0 today"),
    ("WhatsApp未覆盖", "WhatsApp not covered"),
    ("今日明确意向0", "no clear intent today"),
    ("累计已录单未出", "MTD booked pending"),
    ("本月已录单0", "MTD booked 0"),
    ("意向未出数", "intent pending"),
    ("未报今日任务", "no plan posted today"),
    ("证据不足", "evidence missing"),
    ("未报计划，点名催报", "No plan posted; name-check"),
    ("相对上一档无变化", "no change vs last slot"),
    ("任务完成", "tasks done "),
    ("逾期", "overdue "),
    ("MTO未采到", "MTO not collected"),
    ("Vemory未出数", "Vemory pending"),
    ("今日无Vemory", "no Vemory today"),
    ("目标进度", "target progress "),
    ("土耳其客户引流", "Turkey customer referral"),
    ("迪拜待付款跟进", "Dubai outstanding payment"),
    ("MTO / 定制 Signature 手机", "MTO / custom Signature"),
    ("下一步采购计划", "Next purchase plan"),
    ("销售表现与年度目标", "Sales performance and annual target"),
    ("新订单与付款", "New order and payment"),
    ("合作机会", "partnership"),
    ("Harrods 支持与家具问题", "Harrods support and furniture"),
    ("产品促销与老款机型价格", "Legacy model pricing and promos"),
    ("Harrods 定制机型", "Harrods custom models"),
    ("VIP 发货与新合作机会", "VIP shipping and new partnerships"),
    ("经销商沟通汇报", "Distributor update"),
    ("关于土耳其合同", " on the Turkey contract"),
    ("客户转介", "customer referral"),
    ("折扣", "discount"),
    ("金额待确认", "amount pending"),
    ("今天可付款", "payable today"),
    ("本周内付款", "payment this week"),
    ("本周付款", "payment this week"),
    ("无音频直链", "no audio link"),
    ("群内提及水单", "payment slip mentioned in-group"),
    ("部门月度战役", "Department campaign"),
    ("待确认", "pending"),
    ("英国", "UK"),
    ("迪拜", "Dubai"),
)
_EN_PAIRS = tuple(sorted(_EN_PAIRS, key=lambda item: len(item[0]), reverse=True))


def _to_en(text: str) -> str:
    """中文台账字段翻成英文；人名为专有名词保留。"""
    out = text or ""
    for src, dst in _EN_PAIRS:
        if src in out:
            out = out.replace(src, dst)
    out = out.replace("万", " wan")
    out = (
        out.replace("，", ", ")
        .replace("。", ". ")
        .replace("：", ": ")
        .replace("；", "; ")
        .replace("、", ", ")
        .replace("（", "(")
        .replace("）", ")")
        .replace("—", "-")
        .replace("⸻", "-")
    )
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _slogan_text(slogan: str, lang: str) -> str:
    raw = slogan or TODAY_SLOGAN
    return _to_en(raw) if lang == "en" else raw


def is_duzhan_workday(tz_name: str, now: datetime | None = None) -> bool:
    """按中国工作日日历：工作日 + 周末调休上班 + 长假（长假照推、只是不处罚）。"""
    from app.workday_calendar import is_workday

    clock = (now or datetime.now(ZoneInfo(tz_name))).astimezone(ZoneInfo(tz_name))
    return is_workday(clock.strftime("%Y-%m-%d"))


def prev_slot_hour(hour: int) -> int | None:
    """15 对 10，20 对 15；早档没有上一档。"""
    return {15: 10, 20: 15}.get(hour)


def parse_hours(times: list[str]) -> list[int]:
    """把 `10:00,15:00,20:00` 解析成整点小时。"""
    hours: list[int] = []
    for item in times:
        hour_text, _, minute_text = item.partition(":")
        try:
            hour = int(hour_text)
            minute = int(minute_text or "0")
        except ValueError:
            logger.warning("忽略非法督战时刻: {}", item)
            continue
        if minute != 0 or hour not in (10, 15, 20):
            logger.warning("督战官只支持 10:00/15:00/20:00，忽略 {}", item)
            continue
        if hour not in hours:
            hours.append(hour)
    return hours or [10, 15, 20]


def groups_for_tz(tz_name: str, channel_ids: Iterable[str] | None = None) -> list[DuzhanGroup]:
    """返回指定时区的督战群。

    code 源：写死的 GROUPS；db 源：读 duzhan_agents 里启用的子 Agent。
    `channel_ids` 用于「一个子 Agent 一条调度」时只取自己那个群。
    """
    from app.duzhan_admin import runtime

    if runtime.using_db():
        groups = runtime.groups_for_tz(tz_name)
    else:
        groups = [group for group in GROUPS if group.tz == tz_name]
    if channel_ids is not None:
        wanted = {str(item) for item in channel_ids}
        groups = [group for group in groups if group.channel_id in wanted]
    return groups


def all_groups() -> list[DuzhanGroup]:
    """所有督战群（db 源读配置，code 源读常量）。"""
    from app.duzhan_admin import runtime

    if runtime.using_db():
        return [group for _agent, group in runtime.duzhan_agents()]
    return list(GROUPS)


def cron_timezones() -> list[str]:
    """调度需要注册的时区（去重、保序）。"""
    from app.duzhan_admin import runtime

    if runtime.using_db():
        return runtime.cron_timezones()
    seen: list[str] = []
    for group in GROUPS:
        if group.tz not in seen:
            seen.append(group.tz)
    return seen


def collect_clock(hour: int, lead_minutes: int) -> tuple[int, int]:
    """踩点时刻：整点前 lead 分钟开始全源采集，不发群。10:00 档 = 09:45。"""
    total = hour * 60 - lead_minutes
    if total < 0:
        total += 24 * 60
    return divmod(total, 60)


def _idempotency_key(
    tz_name: str,
    day: str,
    hour: int,
    channel_id: str = "",
    producer: str = "duzhan",
) -> str:
    """催收同款：producer-YYYYMMDD-HHMM-时区，群维度再拼 channel 前 8 位。

    producer 必须区分「确定性三追推送」与「Agent/Outbox 草稿推送」：两者
    同一群同一档内容完全不同，若共用键会被服务端静默去重掉一条。
    """
    slug = tz_name.lower().replace("/", "")
    base = f"{producer}-{day.replace('-', '')}-{hour:02d}00-{slug}"
    if channel_id:
        return f"{base}-{channel_id[:8]}"
    return base


def _slot_key(channel_ids: Iterable[str] | None) -> str:
    """同一时区多个子 Agent 各写各的快照；code 源下返回空串，文件名保持原样。"""
    if not channel_ids:
        return ""
    return sorted(str(item) for item in channel_ids)[0][:8]


def _slot_path(tz_name: str, day: str, hour: int, key: str = "") -> Path:
    slug = tz_name.lower().replace("/", "_")
    folder = get_settings().data_dir / "runtime" / "duzhan_slots"
    folder.mkdir(parents=True, exist_ok=True)
    suffix = f"_{key}" if key else ""
    return folder / f"{slug}_{day}_{hour:02d}{suffix}.json"


def prepare_duzhan(
    tz_name: str,
    hour: int,
    now: datetime | None = None,
    *,
    channel_ids: Iterable[str] | None = None,
) -> dict:
    """踩点：拉群消息 + WhatsApp MCP + Vemory + VPS + 日报，写快照，不发群。

    `channel_ids` 有值时只组这些群的表（db 源下一个子 Agent 一次）。
    """
    now = now or datetime.now(ZoneInfo(tz_name))
    if not is_duzhan_workday(tz_name, now):
        logger.info("周末不组表 {} {}", tz_name, hour)
        return {"tz": tz_name, "hour": hour, "skipped": "weekend", "messages": {}}
    day = now.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    try:
        ledger = collect_ledger(day)
    except Exception as exc:  # noqa: BLE001
        logger.exception("督战官台账采集失败: {}", exc)
        ledger = empty_ledger(day)
    prev_hour = prev_slot_hour(hour)
    prev = load_prepared(tz_name, prev_hour, day) if prev_hour else None
    if hour == 10 and prev is None:
        # 早追：以昨日 20:00 档为上一档，用于“昨日未闭环结转今日第一动作”
        from datetime import timedelta

        yday = (now.astimezone(ZoneInfo(tz_name)) - timedelta(days=1)).strftime("%Y-%m-%d")
        prev = load_prepared(tz_name, 20, yday)
    prev_ledger = prev.get("ledger") if isinstance(prev, dict) else None
    from app.duzhan_admin import ai_rules

    messages = {}
    for group in groups_for_tz(tz_name, channel_ids):
        override = ai_rules.focus_for(group, hour, day, ledger)
        messages[group.channel_id] = render_brief(
            group, hour, now, ledger, prev_ledger, focus_override=override
        )
    payload = {
        "tz": tz_name,
        "hour": hour,
        "day": day,
        "prepared_at": now.isoformat(),
        "idempotency_key": _idempotency_key(tz_name, day, hour),
        "ledger": ledger,
        "messages": messages,
    }
    slot_key = _slot_key(channel_ids)
    slot_path = _slot_path(tz_name, day, hour, slot_key) if slot_key else _slot_path(tz_name, day, hour)
    slot_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("督战官已组表 {} {} {}", tz_name, day, f"{hour:02d}:00")
    return payload


def load_prepared(tz_name: str, hour: int, day: str, key: str = "") -> dict | None:
    """读取本档组表快照；没有则返回 None。

    `key` 为空时沿用原来的调用形状（code 源路径零改动）。
    """
    path = _slot_path(tz_name, day, hour, key) if key else _slot_path(tz_name, day, hour)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def render_brief(
    group: DuzhanGroup,
    hour: int,
    now: datetime,
    ledger: dict | None = None,
    prev_ledger: dict | None = None,
    *,
    focus_override: str | None = None,
) -> str:
    """按群语言渲染每日三追进度表。15/20 有上一档则只报变化。

    `focus_override` 有值时替换「本档动作」那一句（AI 规则块生成），其余一字不动。
    """
    local = now.astimezone(ZoneInfo(group.tz))
    day = local.strftime("%Y-%m-%d")
    slot = f"{hour:02d}:00"
    people = people_for(group.name, ledger)
    slogan = (ledger or {}).get("today_target") or TODAY_SLOGAN
    prev_people = {
        str(item.get("display") or ""): item
        for item in people_for(group.name, prev_ledger)
    }
    use_diff = hour in (15, 20) and prev_ledger is not None
    if people:
        blocks = [
            _render_person(
                group,
                hour,
                day,
                slot,
                slogan,
                item,
                prev_people.get(str(item.get("display") or "")),
                use_diff,
                focus_override,
            )
            for item in people
        ]
    else:
        blocks = [_render_person(group, hour, day, slot, slogan, None, None, use_diff, focus_override)]
    board = _board_text(
        ledger,
        hour,
        group.lang,
        compact=bool(getattr(get_settings(), "duzhan_compact", True)),
        strict_holiday=group.channel_id in strict_holiday_channels(),
    )
    return "\n\n".join(blocks) + board


def _render_person(
    group: DuzhanGroup,
    hour: int,
    day: str,
    slot: str,
    slogan: str,
    person: dict | None,
    prev_person: dict | None = None,
    use_diff: bool = False,
    focus_override: str | None = None,
) -> str:
    lang = group.lang
    reporter = (person or {}).get("display") or _REPORTER.get(group.name, group.name)
    target = wan_text(person.get("target_wan") if person else None, lang)
    mtd = wan_text(person.get("mtd_wan") if person else None, lang)
    slogan_text = _slogan_text(slogan, lang)
    target_progress = daily_target_text(
        {
            "daily_target": (person or {}).get("daily_target_wan"),
            "rolling_target": (person or {}).get("rolling_target_wan"),
            "days_elapsed": (person or {}).get("days_elapsed"),
            "days_in_month": (person or {}).get("days_in_month"),
            "gap": (person or {}).get("target_gap_wan"),
            "ahead": (person or {}).get("target_ahead"),
        },
        lang,
    )
    if group.lang == "en":
        title, focus = _SLOT_EN.get(hour, ("Brief", ""))
        focus = focus_override or focus
        head = (
            f"[Overseas Channel Daily Triple Chase] {title} {slot} (Paris time)\n"
            f"- Owner: {reporter} | Date: {day} | Slot: {slot}\n"
            f"- Focus: {focus}\n"
            f"- Monthly target: {target} wan | Rolling daily plan: {target_progress}\n"
            f"- MTD booked (sales orders): {mtd} wan | Campaign: {slogan_text}\n"
        )
    else:
        title, focus = _SLOT_ZH.get(hour, ("督战", ""))
        # 配了 AI 规则块的子 Agent：这句话由模型按当天数据生成（失败时是 None，回落固定文案）
        focus = focus_override or focus
        tz_label = "北京时间" if group.tz == TZ_SHANGHAI else group.tz
        head = (
            f"【海外渠道业绩达标群 · 每日三追进度表】{title} {slot}（{tz_label}）\n"
            f"- 汇报人：{reporter} | 日期：{day} | 阶段：{slot}\n"
            f"- 本档动作：{focus}\n"
            f"- 月度目标：{target} 万 | 滚动日目标：{target_progress}\n"
            f"- 已录单（开单额）：{mtd} 万 | 战役：{slogan}\n"
        )
    if getattr(get_settings(), "duzhan_compact", True):
        # 老板 2026-09-19：三档只出总结性内容，明细放每天 08:00 的证据 HTML。
        return head + "\n" + _compact_body(hour, person, lang, prev_person)
    if use_diff:
        body = _changes_text(person, prev_person, lang)
        extra = _slot_sections(hour, person, lang, prev_person)
        if extra:
            body = body + "\n" + extra
        return head + "\n" + body
    body = _full_person_body(group, hour, person, lang)
    if hour == 10:
        body = _morning_target_block(person, prev_person, lang) + "\n" + body
    extra = _slot_sections(hour, person, lang, prev_person)
    if extra:
        body = body + "\n" + extra
    return head + "\n" + body


def _full_person_body(
    group: DuzhanGroup,
    hour: int,
    person: dict | None,
    lang: str,
) -> str:
    wa = count_text(
        person.get("wa_reached") if person else None,
        lower_bound=bool(person and person.get("wa_lower_bound")),
        lang=lang,
    )
    intent = count_text(person.get("intent_count") if person else None, lang=lang)
    mto = _mto_text(person, lang)
    vps = _vps_text(person, lang)
    vemory = _vemory_text(person, group.lang)
    core = _collections_text(person, lang, hour)
    blockers = _blockers_text(person, lang)
    evidence = _evidence_text(person, lang)
    hours = hours_text(person, lang)
    daily = _daily_report_text(person, lang)
    perf = _perf_text(person, lang)
    if lang == "en":
        return (
            "1. Performance keywords (booked / payment slip / intent):\n"
            f"{perf}\n"
            "2. Core collection (progress % + expected payment time):\n"
            f"{core}\n"
            "3. Daily 4 MTO luxury proposals (≥ CNY 300k):\n"
            f"   • Submitted: {mto}\n"
            "   • Reach: WhatsApp screenshot pending unless posted in-group\n"
            "4. Today's activity and hours:\n"
            f"   • VPS trace: {vps}\n"
            "   • VPS opportunities: pending\n"
            f"   • WhatsApp accounts: {wa} | clear intent: {intent}\n"
            f"   • Hours vs 8h std: {hours}\n"
            f"   • Vemory recordings: {vemory}\n"
            "5. Blockers needing help today:\n"
            f"{blockers}\n"
            f"6. Evidence: {evidence}\n"
            f"7. Daily report check: {daily}"
        )
    return (
        "1. 业绩三关键词（已录单 / 水单 / 意向）：\n"
        f"{perf}\n"
        "2. 核心客户催款与回款进展（百分比量化 + 预计打款时间）：\n"
        f"{core}\n"
        "3. 每日 4 款 MTO 高奢方案输出（≥30万）：\n"
        f"   • 摘要：{_mto_summary(person, lang)}\n"
        f"   • {mto}\n"
        "   • 触达记录：WhatsApp 发送截图以群内原图为准，未标截图则待确认\n"
        "4. 今日过程留痕与工时消耗：\n"
        f"   • VPS 留痕：{vps}\n"
        f"   • VPS 录入商机数：待确认\n"
        f"   • WhatsApp 沟通户数：{wa} 户 | 产生明确意向：{intent} 户\n"
        f"   • 工时（对照标准8h）：{hours}\n"
        f"   • Vemory 会议录音：{vemory}\n"
        "5. 今日卡点与需协同解决项：\n"
        f"{blockers}\n"
        f"6. 附件证据：{evidence}\n"
        f"7. 海外日报群核对：{daily}\n"
        + ((person or {}).get("meeting_todos") or "")
    )


def _mto_summary(person: dict | None, lang: str) -> str:
    """MTO 摘要行（老板 2026-09-22 要求）：N 款达标 + 型号/金额，读不出写待确认。"""
    person = person or {}
    quotes = [q for q in (person.get("mto_quotes") or []) if isinstance(q, dict)]
    if not quotes:
        count = person.get("mto_count")
        if count is None:
            return "pending" if lang == "en" else "待确认"
        text = str(count)
        return text + " qualified" if lang == "en" else text + " 款达标"
    ok, near, unread = [], [], 0
    for q in quotes:
        model = str(q.get("model") or "").strip()
        wan = q.get("wan")
        label = model or ("model pending" if lang == "en" else "型号待确认")
        if wan is None:
            unread += 1
            continue
        money = f"{wan:g} wan" if lang == "en" else f"{wan:g}万"
        (ok if q.get("qualifies") else near).append(f"{label} {money}")
    parts = []
    if ok:
        parts.append((f"{len(ok)} qualified: " if lang == "en" else f"{len(ok)} 款达标：") + "、".join(ok[:3]))
    elif lang == "zh":
        parts.append("0 款达标")
    else:
        parts.append("0 qualified")
    if near:
        parts.append((f"{len(near)} under 300k" if lang == "en" else f"{len(near)} 款未满 30 万") + "（" + "、".join(near[:2]) + "）")
    if unread:
        parts.append(f"{unread} " + ("unreadable" if lang == "en" else "张读不出金额"))
    return "｜".join(parts)


def _mto_wrapup_line(person: dict | None, lang: str) -> str:
    """20:00 验兑现里的 MTO 打分一行（2026-09-23 老板要求）。

    10:00 那档的「摘要」是早上采集的，当天还没人发 MTO 图，必然是 0 款；
    交付物核验放在 20:00 才有意义：几款、达标了哪几款、几张读不出。
    """
    person = person or {}
    quotes = [q for q in (person.get("mto_quotes") or []) if isinstance(q, dict)]
    if not quotes:
        count = person.get("mto_count")
        if count is None:
            return "pending" if lang == "en" else "待确认"
        return f"{count} " + ("proposal(s) recorded" if lang == "en" else "款")
    ok: list[str] = []
    near: list[str] = []
    unread = 0
    for quote in quotes:
        label = str(quote.get("model") or "").strip() or (
            "model pending" if lang == "en" else "型号待确认"
        )
        wan = quote.get("wan")
        if wan is None:
            unread += 1
            continue
        money = f"{wan:g} wan" if lang == "en" else f"{wan:g}万"
        (ok if quote.get("qualifies") else near).append(f"{label} {money}")
    if lang == "en":
        parts = [
            f"{len(ok)}/{len(quotes)} qualified" + ((" (" + ", ".join(ok[:3]) + ")") if ok else "")
        ]
        if near:
            parts.append(f"{len(near)} under 300k (" + ", ".join(near[:2]) + ")")
        if unread:
            parts.append(f"{unread} unreadable")
    else:
        parts = [
            f"{len(ok)}/{len(quotes)} 款达标" + (("（" + "、".join(ok[:3]) + "）") if ok else "")
        ]
        if near:
            parts.append(f"{len(near)} 款未满 30 万（" + "、".join(near[:2]) + "）")
        if unread:
            parts.append(f"{unread} 张读不出金额")
    return "｜".join(parts)


def _perf_text(person: dict | None, lang: str) -> str:
    """业绩三关键词：已录单（开单额，系统口径）/ 水单（已付款未到账）/ 意向（明确意向金额）。"""
    person = person or {}
    arrived = wan_text(person.get("perf_arrived_wan"), lang)
    slip = person.get("perf_slip") or []
    intent = person.get("perf_intent") or []

    def _brief(items: list[dict]) -> str:
        if not items:
            return "pending" if lang == "en" else "待确认"
        parts = []
        for item in items[:3]:
            money = item.get("amount_text") or ("金额待确认" if lang == "zh" else "amount pending")
            snippet = str(item.get("snippet") or "")
            if lang != "zh":
                parts.append(f"{money}" + (f" ({snippet})" if snippet else ""))
            elif money == "金额待确认" and snippet:
                parts.append(snippet)
            else:
                parts.append(f"{money}（{snippet}）" if snippet else money)
        return "；".join(parts)

    if lang == "en":
        return (
            f"   • Booked (sales orders): {arrived} wan\n"
            f"   • Payment slip: {_brief(slip)}\n"
            f"   • Intent: {_brief(intent)}"
        )
    return (
        f"   • 已录单（开单额）：{arrived} 万\n"
        f"   • 水单：{_brief(slip)}\n"
        f"   • 意向：{_brief(intent)}"
    )


def _daily_report_text(person: dict | None, lang: str) -> str:
    """日报申报工时与系统证据工时并列，不互相覆盖。"""
    report = (person or {}).get("daily_report") or {}
    if not report:
        if not (person or {}).get("daily_report_ok", True):
            # 取数失败 ≠ 没交：读不到就写「待确认」，不能替人下结论。
            return "daily report pending" if lang == "en" else "待确认（日报群取数失败）"
        return "not submitted today" if lang == "en" else "未见今日正式日报"
    declared = float(report.get("spent_hours") or 0)
    evidenced = float((person or {}).get("hours_minutes") or 0) / 60
    gap = max(declared - evidenced, 0)
    count = int(report.get("item_count") or 0)
    done = int(report.get("done_count") or 0)
    if lang == "en":
        return (
            f"submitted: {done}/{count} complete; declared {declared:g}h; "
            f"system-evidenced {evidenced:.2f}h; {gap:.2f}h pending evidence"
        )
    return (
        f"已交，完成{done}/{count}项；申报{declared:g}h；"
        f"系统证据{evidenced:.2f}h；{gap:.2f}h待补证"
    )


def _morning_target_block(person: dict | None, prev_person: dict | None, lang: str) -> str:
    """10:00 定今日目标：滚动日目标 + 昨日未闭环结转今日第一动作。"""
    person = person or {}
    daily = person.get("daily_target_wan")
    rolling = person.get("rolling_target_wan")
    gap = person.get("target_gap_wan")
    carried = _unfinished_titles(prev_person, lang=lang)
    group_target = person.get("group_target_wan")
    group_name = person.get("group_target_name") or "小组"
    if lang == "en":
        head = (
            "0. Today's target (lock it first): "
            + (f"{daily} wan/day, cumulative due {rolling} wan" if daily is not None else "pending")
        )
        if daily is None and group_target:
            head = (
                f"0. Today's target (lock it first): group-level {group_name} "
                f"{group_target:g} wan/month (assessed as a team)"
            )
        if gap is not None:
            head += f" | {'ahead' if person.get('target_ahead') else 'behind'} {abs(gap)} wan"
        lines = [head]
        lines.append(
            "   • Carry-over from yesterday (first action today): "
            + ("; ".join(carried[:3]) if carried else "no unfinished items found")
        )
        return chr(10).join(lines)
    if daily is None and group_target:
        head = (
            f"0. 今日目标（本档先定）：小组口径 {group_name} {group_target:g} 万/月"
            f"（{group_name}整体考核，不摊人头）"
        )
    else:
        head = (
            "0. 今日目标（本档先定）："
            + (
                f"日目标 {daily} 万/天，累计应达 {rolling} 万"
                if daily is not None
                else "待确认（缺月度目标）"
            )
        )
    if gap is not None:
        head += f"｜{'领先' if person.get('target_ahead') else '落后'} {abs(gap)} 万"
    lines = [head]
    lines.append(
        "   • 昨日未闭环结转（今日第一动作）："
        + ("；".join(carried[:3]) if carried else "未见未闭环事项")
    )
    ping = _amount_ping(person, lang, source=prev_person)
    if ping:
        lines.append(ping)
    return chr(10).join(lines)


def _unfinished_titles(
    person: dict | None,
    limit: int = 5,
    lang: str = "zh",
) -> list[str]:
    """上一档未完成事项标题（用于结转与明日预告）。英文群翻成英文。"""
    items = (person or {}).get("collections") or []
    out: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if infer_status(item) == "done":
            continue
        title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
        if lang == "en":
            title = _to_en(title)
        if title and title not in out:
            out.append(title[:80])
        if len(out) >= limit:
            break
    return out


def _compact_body(
    hour: int,
    person: dict | None,
    lang: str,
    prev_person: dict | None = None,
) -> str:
    """三档总结版（10:00 定任务 / 15:00 追变化 / 20:00 验兑现），每人 5–7 行。

    明细（三口径原文、MTO 图与 OCR、工时拆解、群原话）放每天 08:00 的证据 HTML，
    这里只留「目标 / 变化 / 兑现 / 要你补什么」。
    """
    person = person or {}
    english = lang == "en"
    lines: list[str] = []
    daily = person.get("daily_target_wan")
    rolling = person.get("rolling_target_wan")
    arrived = person.get("perf_arrived_wan")
    gap = person.get("target_gap_wan")
    ahead = bool(person.get("target_ahead"))
    if hour == 10:
        if daily is not None:
            target_line = (
                f"今日目标：日目标 {daily} 万/天，累计应达 {rolling} 万"
                if not english
                else f"Today's target: {daily} wan/day, cumulative due {rolling} wan"
            )
        else:
            target_line = "今日目标：待确认" if not english else "Today's target: pending"
        if arrived is not None:
            target_line += (
                (f"｜累计已录单 {arrived} 万（{'领先' if ahead else '落后'} {abs(float(gap)):g} 万）"
                 if gap is not None else f"｜累计已录单 {arrived} 万")
                if not english
                else (f" | MTD {arrived} wan ({'ahead' if ahead else 'behind'} {abs(float(gap)):g} wan)"
                      if gap is not None else f" | MTD {arrived} wan")
            )
        lines.append(target_line)
        carried = _unfinished_titles(prev_person, lang=lang)
        lines.append(
            ("昨日未闭环 → 今日第一动作：" if not english else "Carry-over → first action today: ")
            + ("；".join(carried[:3]) if carried else ("未见未闭环事项" if not english else "none found"))
        )
        ping = _amount_ping(person, lang, source=prev_person)
        if ping:
            lines.append(ping.strip())
        lines.append(
            "请回：今日 3–5 项（对象 / 交付物 / 截止时间）"
            if not english
            else "Reply with: 3–5 items (owner / deliverable / deadline)"
        )
        return chr(10).join(lines)
    if hour == 15:
        delta = None
        if arrived is not None and (prev_person or {}).get("perf_arrived_wan") is not None:
            delta = round(float(arrived) - float(prev_person["perf_arrived_wan"]), 1)
        if delta is None:
            delta_text = "本档新增：待确认" if not english else "New this slot: pending"
        elif delta == 0:
            delta_text = "本档新增：0（与上一档持平）" if not english else "New this slot: none"
        else:
            delta_text = (
                f"本档新增：{('+' if delta > 0 else '')}{delta} 万"
                if not english
                else f"New this slot: {('+' if delta > 0 else '')}{delta} wan"
            )
        if rolling is not None:
            delta_text += (
                f"；累计已录单 {arrived} 万｜应达 {rolling} 万"
                if not english
                else f"; MTD {arrived} wan | due {rolling} wan"
            )
            if gap is not None:
                delta_text += (
                    f"（{'领先' if ahead else '落后'} {abs(float(gap)):g} 万）"
                    if not english
                    else f" ({'ahead' if ahead else 'behind'} {abs(float(gap)):g} wan)"
                )
        lines.append(delta_text)
        lines.append(
            ("上午工作：" if not english else "Morning work: ") + _hours_text_short(person, lang)
        )
        ping = _amount_ping(person, lang)
        if ping:
            lines.append(ping.strip())
        lines.append(
            "请回：相对 10:00 的变化（完成 / 推进 / 停滞 / 未回）+ 卡点"
            if not english
            else "Reply with: changes vs 10:00 (done / progressed / stalled / no reply) + blockers"
        )
        return chr(10).join(lines)
    slip = person.get("perf_slip") or []
    intent = person.get("perf_intent") or []

    def _brief(items: list[dict]) -> str:
        if not items:
            return "未检索到" if not english else "not found"
        return "；".join(
            str(item.get("amount_text") or ("金额待确认" if not english else "amount pending"))
            for item in items[:2]
        )

    wa_text = count_text(person.get("wa_reached"), lower_bound=bool(person.get("wa_lower_bound")), lang=lang)
    intent_text = count_text(person.get("intent_count"), lang=lang)
    if english:
        lines.append(f"Booked {wan_text(arrived, lang)} wan | Slip {_brief(slip)} | Intent {_brief(intent)}")
        lines.append(
            f"WhatsApp {wa_text} accounts | intent {intent_text} | Hours {_hours_text_short(person, lang)}"
        )
    else:
        lines.append(f"已录单 {wan_text(arrived, lang)} 万｜水单 {_brief(slip)}｜意向 {_brief(intent)}")
        lines.append(
            f"WhatsApp {wa_text} 户（明确意向 {intent_text} 户）｜工时：{_hours_text_short(person, lang)}"
        )
    nxt = _unfinished_titles(person, lang=lang)
    lines.append(
        ("明日第一动作：" if not english else "First action tomorrow: ")
        + ("；".join(nxt[:3]) if nxt else ("按日目标继续推进" if not english else "keep pushing the daily target"))
    )
    lines.append(
        "请回：交付物 + 证据（截图 / 水单 / 单号）"
        if not english
        else "Reply with: deliverable + evidence (screenshot / slip / order no.)"
    )
    return chr(10).join(lines)


def _amount_ping(person: dict | None, lang: str, source: dict | None = None) -> str:
    """水单/意向金额没写清时点名补一句（10:00 用上一档口径，15:00 用本档）。

    老板 2026-09-18 拍板：10:00/15:00 档自动点名要金额。
    """
    data = source or person or {}
    items = list(data.get("perf_slip") or []) + list(data.get("perf_intent") or [])
    missing = [item for item in items if item.get("wan") is None]
    if not missing:
        return ""
    names = "；".join(_clip_text(str(item.get("snippet") or ""), 30) for item in missing[:2])
    if lang == "en":
        return (
            f"   • Amount missing on {len(missing)} slip/intent item(s) — reply with "
            f"customer / amount+currency / expected payment date / category. {names}"
        )
    return (
        f"   • 补一句：水单/意向有 {len(missing)} 条没写金额（{names}），"
        "按「客户 / 金额+币种 / 预计到账日 / 品类」补一句。"
    )


def _clip_text(text: str, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _slot_sections(
    hour: int,
    person: dict | None,
    lang: str,
    prev_person: dict | None = None,
) -> str:
    """15:00 梳理目标+上午总结；20:00 业绩核对+WhatsApp+工时+明日预告。

    口径：已录单是月累计（系统），日目标只作参照；本档只报“相对上一档新增”，
    避免拿月累计去除日目标得出离谱完成率。
    """
    person = person or {}
    english = lang == "en"
    lines: list[str] = []
    daily = person.get("daily_target_wan")
    arrived = person.get("perf_arrived_wan")
    rolling = person.get("rolling_target_wan")
    gap = person.get("target_gap_wan")
    prev_arrived = (prev_person or {}).get("perf_arrived_wan")
    delta = None
    if arrived is not None and prev_arrived is not None:
        delta = round(float(arrived) - float(prev_arrived), 1)
    ahead = bool(person.get("target_ahead"))
    if hour == 15:
        lines.append("【Target review | Morning summary】" if english else "【目标梳理｜上午总结】")
        if delta is None:
            delta_text = (
                "new this slot pending (no previous slot)"
                if english
                else "本次新增 待确认（缺上一档口径）"
            )
        elif delta == 0:
            delta_text = "no change this slot" if english else "本次新增 0（与上一档持平）"
        else:
            delta_text = f"+{delta} wan this slot" if english else f"本次新增 +{delta} 万"
        if arrived is None:
            arrived_text = "pending" if english else "待确认"
        else:
            arrived_text = f"{arrived} wan" if english else f"{arrived} 万"
        progress_text = ("MTD booked " if english else "累计已录单 ") + arrived_text
        if rolling is not None:
            progress_text += (
                f" | cumulative due {rolling} wan" if english else f"｜累计应达 {rolling} 万"
            )
        if gap is not None:
            if english:
                progress_text += f" ({'ahead' if ahead else 'behind'} {abs(gap)} wan)"
            else:
                progress_text += f"（{'领先' if ahead else '落后'} {abs(gap)} 万）"
        if daily is not None:
            progress_text += (
                f" | today's target {daily} wan" if english else f"｜今日日目标 {daily} 万"
            )
        elif person.get("group_target_wan"):
            group_note = (
                f" | group-level {person.get('group_target_name') or 'team'} "
                f"{person['group_target_wan']:g} wan/month"
                if english
                else f"｜小组口径 {person.get('group_target_name') or '小组'} "
                f"{person['group_target_wan']:g} 万/月"
            )
            progress_text += group_note
        label = "   • Today's target: " if english else "   • 今日目标进度："
        lines.append(label + delta_text + ("; " if english else "；") + progress_text)
        ping = _amount_ping(person, lang)
        if ping:
            lines.append(ping)
        lines.append(
            ("   • Morning work: " if english else "   • 上午工作：")
            + _hours_text_short(person, lang)
        )
    if hour == 20:
        lines.append("【Day wrap-up | Performance check】" if english else "【当天总结｜业绩核对】")
        lines.append(_perf_text(person, lang))
        lines.append(
            ("   • MTO today: " if english else "   • MTO 今日达标：")
            + _mto_wrapup_line(person, lang)
        )
        wa_text = count_text(
            person.get("wa_reached"),
            lower_bound=bool(person.get("wa_lower_bound")),
            lang=lang,
        )
        intent_text = count_text(person.get("intent_count"), lang=lang)
        if english:
            lines.append(f"   • WhatsApp: {wa_text} accounts | explicit intent {intent_text} accounts")
            lines.append(f"   • Hours today: {hours_text(person, lang)}")
        else:
            lines.append(f"   • WhatsApp：{wa_text} 户｜明确意向 {intent_text} 户")
            lines.append(f"   • 当日工时：{hours_text(person, lang)}")
        nxt = _unfinished_titles(person, lang=lang)
        if english:
            lines.append(
                "【Tomorrow】First action: "
                + ("; ".join(nxt[:3]) if nxt else "nothing open, keep pushing the daily target")
            )
        else:
            lines.append(
                "【明日预告】"
                + ("第一动作：" + "；".join(nxt[:3]) if nxt else "无未完成事项，按日目标继续推进")
            )
    return chr(10).join(lines) if lines else ""


def _hours_text_short(person: dict, lang: str) -> str:
    """上午总结用的一句话工时（完整口径见 20:00 段）。"""
    minutes = person.get("hours_minutes")
    if minutes is None:
        return "hours pending" if lang == "en" else "工时待确认"
    hours = float(minutes) / 60
    band = person.get("hours_band") or ("pending" if lang == "en" else "待确认")
    if lang == "en":
        return f"{hours:.1f}h vs 8h std ({band})"
    return f"{hours:.1f}h / 标准8h（{band}）"


def _task_line(item: dict, lang: str) -> str:
    title = str(item.get("title") or "").strip()
    progress = str(item.get("progress") or "").strip()
    if lang == "en":
        title = _to_en(title)
        progress = _to_en(progress or "待确认")
        return f"   • {title} | {progress}"
    return f"   • {title} | {progress or '待确认'}"


def _changes_text(person: dict | None, prev_person: dict | None, lang: str) -> str:
    curr = list((person or {}).get("collections") or [])
    prev = list((prev_person or {}).get("collections") or [])
    diff = diff_tasks(curr, prev)
    labels = (
        ("Done", "Progressed", "Stalled", "New", "No reply")
        if lang == "en"
        else ("完成", "推进", "停滞", "新增", "未回复")
    )
    sections = (
        ("completed", labels[0]),
        ("progressed", labels[1]),
        ("stalled", labels[2]),
        ("new", labels[3]),
        ("unanswered", labels[4]),
    )
    lines: list[str] = []
    for key, label in sections:
        items = diff.get(key) or []
        if not items:
            continue
        lines.append(f"{label}:")
        lines.extend(_task_line(item, lang) for item in items[:8])
    if not lines:
        empty = "no change vs last slot" if lang == "en" else "相对上一档无变化"
        lines.append(f"   • {empty}")
    blockers = _blockers_text(person, lang)
    evidence = _evidence_text(person, lang)
    extra = "Blockers" if lang == "en" else "卡点"
    proof = "Evidence" if lang == "en" else "证据"
    lines.append(f"{extra}:\n{blockers}")
    lines.append(f"{proof}: {evidence}")
    return "\n".join(lines)


def _mto_text(person: dict | None, lang: str) -> str:
    if not person or person.get("mto_count") is None:
        return "pending" if lang == "en" else "待确认"
    count = int(person.get("mto_count") or 0)
    quotes = [item for item in (person.get("mto_quotes") or []) if isinstance(item, dict)]
    ocr_on = bool(quotes) and any(item.get("raw_ok") for item in quotes)
    names = [str(item) for item in (person.get("mto_names") or []) if item]
    shown = "; ".join(names[:4]) if lang == "en" else "、".join(names[:4])
    extra = f": {shown}" if lang == "en" and shown else (f"：{shown}" if shown else "")
    if lang == "en":
        if ocr_on:
            tag = "met 4 quotes ≥CNY300k" if count >= 4 else "under 4 qualifying quotes"
        else:
            tag = "met 4" if count >= 4 else "under 4"
        return f"{count}/4 ({tag}){extra}"
    if ocr_on:
        tag = "达标" if count >= 4 else "未满4款≥30万"
    else:
        tag = "达标" if count >= 4 else "未满4款"
    return f"已交{count}/4（{tag}）{extra}"


def _vps_text(person: dict | None, lang: str) -> str:
    if not person:
        return "pending" if lang == "en" else "待确认"
    im_sent = person.get("vps_im_sent")
    turns = person.get("vps_turns")
    agent = person.get("vps_agent_calls")
    if im_sent is None and turns is None and agent is None:
        return "pending" if lang == "en" else "待确认"
    first_at = str(person.get("vps_first") or "")[:16].replace("T", " ")
    last_at = str(person.get("vps_last") or "")[:16].replace("T", " ")
    span = ""
    if first_at and last_at:
        span = f"; active {first_at}–{last_at}" if lang == "en" else f"；活跃{first_at}–{last_at}"
    daily = bool(person.get("vps_daily"))
    if lang == "en":
        im_text = "pending" if im_sent is None else str(im_sent)
        turns_text = "pending" if turns is None else str(turns)
        scope = "today; hours=rounds×6min" if daily else "week total; hours=daily avg×6min"
        return f"IM sent {im_text} | Agent rounds {turns_text} ({scope}){span}"
    im_zh = "待确认" if im_sent is None else str(im_sent)
    turns_zh = "待确认" if turns is None else str(turns)
    scope = "今日 Standard+OpenCode，工时×6分钟" if daily else "本周累计，工时按日均×6分钟"
    return f"IM发送{im_zh}条 | Agent轮数{turns_zh}（{scope}）{span}"


def _vemory_text(person: dict | None, lang: str) -> str:
    if person is None or person.get("vemory_ok") is False:
        return "pending" if lang == "en" else "待确认"
    meetings = person.get("vemory") or []
    if not meetings:
        return "0" if lang == "en" else "0场"
    parts = []
    for item in meetings[:5]:
        name = str(item.get("name") or "").strip()
        link = str(item.get("link") or "").strip()
        mid = str(item.get("meeting_id") or item.get("id") or "").strip()
        if link:
            shown = _to_en(name) if lang == "en" else name
            parts.append(f"{shown} {link}".strip())
        elif mid:
            tag = "audio pending" if lang == "en" else "无音频直链"
            shown = _to_en(name) if lang == "en" else name
            parts.append(f"{shown} ({mid[:8]}… {tag})")
        else:
            parts.append(_to_en(name) if lang == "en" else name)
    prefix = f"{len(meetings)} " if lang == "en" else f"{len(meetings)}场 "
    joiner = "; " if lang == "en" else "；"
    return prefix + joiner.join(parts)


def _collections_text(person: dict | None, lang: str, hour: int = 10) -> str:
    items = (person or {}).get("collections") or []
    if not items:
        if hour == 10:
            if lang == "en":
                return "   • No plan posted; name-check"
            return "   • 未报计划，点名催报"
        if lang == "en":
            return "   • pending: no owner-posted account/amount/% in-group; not invented"
        return "   • 待确认：群内未见本人报客户清单，不编造"
    lines = []
    for item in items[:8]:
        title = str(item.get("title") or "").strip()
        amount = str(item.get("amount") or "").strip()
        progress = str(item.get("progress") or "").strip()
        if lang == "en":
            title = _to_en(title)
            amount = amount or "—"
            progress = _to_en(progress or "待确认")
            lines.append(f"   • {title}: {amount} | progress {progress}")
        else:
            amount = amount or "金额待确认"
            progress = progress or "待确认"
            lines.append(f"   • {title}：{amount} | 进度 {progress}")
    return "\n".join(lines)


def _blockers_text(person: dict | None, lang: str) -> str:
    items = (person or {}).get("blockers") or []
    if not items:
        if lang == "en":
            return "   • pending blockers in-group"
        return "   • 群内未见明确卡点原文"
    if lang == "en":
        return "\n".join(f"   • {_to_en(item)}" for item in items[:4])
    return "\n".join(f"   • {item}" for item in items[:4])


def _evidence_text(person: dict | None, lang: str) -> str:
    items = [str(item) for item in ((person or {}).get("evidence") or []) if item]
    meetings = (person or {}).get("vemory") or []
    for meeting in meetings[:3]:
        name = str(meeting.get("name") or "").strip()
        if name:
            items.append(name)
    if not items:
        return "pending" if lang == "en" else "打款水单待确认 / WhatsApp截图待确认 / Vemory音频直链待确认"
    if lang == "en":
        return " / ".join(_to_en(item) for item in items[:8])
    return " / ".join(items[:8])


def _collect_for_missing_snapshot(tz_name: str, day: str, hour: int) -> dict | None:
    """组表快照缺失时的兜底：现场采一次台账；失败返回 None 由上层退上一档。"""
    logger.warning("督战官无组表快照，现场采集 {} {} {}:00", tz_name, day, f"{hour:02d}")
    try:
        return collect_ledger(day)
    except Exception as exc:  # noqa: BLE001 — 采集失败退上一档台账，不阻断推送
        logger.exception("督战官现场采集失败: {}", exc)
        return None


def _red_item_text(item: dict, lang: str) -> str:
    """红榜一行：综合分 + 过程分 + 业绩达成（两个口径都给，缺业绩口径就明说）。"""
    display = item.get("display") or "未署名"
    combined = item.get("combined_score")
    score = item.get("score")
    perf = item.get("perf_score")
    rolling = item.get("rolling_target_wan")
    mtd = item.get("mtd_wan")
    if lang == "en":
        head = f"@{display}"
        if combined is not None:
            head += f" overall {round(float(combined))}"
        if score is not None:
            head += f" | process {round(float(score))}"
        if perf is not None:
            head += f" | performance {perf:g}%"
        elif item.get("group_scope"):
            head += " | performance n/a (team target)"
        else:
            head += " | performance pending"
        return head
    head = f"@{display}"
    if combined is not None:
        head += f" 综合{round(float(combined))}"
    if score is not None:
        head += f"｜过程{round(float(score))}"
    if perf is not None:
        head += f"｜业绩{perf:g}%"
    elif item.get("group_scope"):
        head += "｜业绩按小组口径"
    else:
        head += "｜业绩待确认"
    if mtd is not None:
        head += f"（已录单{wan_text(mtd, lang)}万）"
    return head


STRICT_HOLIDAY_ENV = "PDCA_DUZHAN_STRICT_HOLIDAY_CHANNELS"


def strict_holiday_channels() -> set[str]:
    """长假期间也"按正常工作日"处理的群（逗号分隔的 channel_id）。

    长假本来就照推三档（见 workday_calendar），差的只是"一律不处罚"这条豁免：
    列进这里的群，红黑榜照列、扣罚照记，和普通工作日一模一样。
    """
    raw = os.environ.get(STRICT_HOLIDAY_ENV, "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _board_text(
    ledger: dict | None,
    hour: int,
    lang: str,
    compact: bool = False,
    *,
    strict_holiday: bool = False,
) -> str:
    """晚追才出红黑榜；@ 纯文本拼进 body。compact=只留红榜与黑榜。

    `strict_holiday=True`：这个群在长假里也按工作日办（豁免不生效）。
    """
    if hour != 20 or not ledger:
        return ""
    red = ledger.get("red") or []
    black = ledger.get("black") or []
    rewards = ledger.get("rewards") or []
    penalties = ledger.get("penalties") or []
    def incentive(item: object) -> str:
        if isinstance(item, dict):
            return str(item.get(lang) or item.get("zh") or item.get("en") or "")
        return str(item)

    if lang == "en":
        red_line = " / ".join(_red_item_text(item, lang) for item in red) or "pending"
        black_line = " / ".join(
            f"@{item['display']} {_to_en(str(item.get('reason') or ''))}"
            for item in black
        ) or "none"
        exempt = bool((ledger or {}).get("_penalty_exempt")) and not strict_holiday
        head = (
            f"\nRed TOP3 (department-wide, visible to all groups): {red_line}\n"
            + ("Black: holiday period, no penalty\n" if exempt else f"Black (to improve, department-wide): {black_line}\n")
        )
        if compact:
            return head
        reward = "none verified" if not rewards else "; ".join(incentive(item) for item in rewards)
        penalty = (
            "holiday period, no penalty recorded"
            if exempt
            else ("none" if not penalties else "; ".join(incentive(item) for item in penalties))
        )
        return head + f"Reward ledger: {reward}\nPenalty ledger: {penalty}\n"
    red_line = " / ".join(_red_item_text(item, lang) for item in red) or "待确认"
    black_line = " / ".join(
        f"@{item['display']} {item.get('reason')}"
        for item in black
    ) or "无"
    # 老板 2026-09-20 拍板：红黑榜是部门口径、全员可见（每个群都会看到全部 10 人）
    # 长假期间照常推送进度，但一律不处罚：不列黑榜、不记扣罚（老板 2026-09-20 拍板）。
    # 例外：PDCA_DUZHAN_STRICT_HOLIDAY_CHANNELS 里的群，长假也照常评价（老板 2026-09-30）。
    exempt = bool((ledger or {}).get("_penalty_exempt")) and not strict_holiday
    head = (
        "\n"
        f"红榜 TOP3（部门口径·全员可见｜综合=过程50%+业绩50%）：{red_line}\n"
        + ("黑榜：长假期间不处罚（不列黑榜、不记扣罚）\n" if exempt else f"黑榜 待改进（部门口径·全员可见）：{black_line}\n")
    )
    if compact:
        return head
    reward = "今日无已核验奖励记录" if not rewards else "；".join(incentive(item) for item in rewards)
    penalty = (
        "长假期间不记扣罚"
        if exempt
        else ("今日无扣罚记录" if not penalties else "；".join(incentive(item) for item in penalties))
    )
    return head + f"奖励台账：{reward}\n扣罚台账：{penalty}\n"


def run_duzhan(
    tz_name: str,
    hour: int,
    now: datetime | None = None,
    *,
    channel_ids: Iterable[str] | None = None,
) -> dict:
    """整点推送：优先用提前组好的快照，没有快照则现场渲染。周末不发。

    `channel_ids` 有值时只推这些群（db 源下一个子 Agent 一次）。
    """
    now = now or datetime.now(ZoneInfo(tz_name))
    if not is_duzhan_workday(tz_name, now):
        logger.info("周末不推送 {} {}", tz_name, hour)
        return {"tz": tz_name, "hour": hour, "sent": [], "failed": [], "skipped": "weekend"}
    day = now.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    slot_key = _slot_key(channel_ids)
    snapshot = load_prepared(tz_name, hour, day, slot_key)
    bodies = snapshot.get("messages") if snapshot else None
    ledger = snapshot.get("ledger") if snapshot else None
    prev_hour = prev_slot_hour(hour)
    prev = load_prepared(tz_name, prev_hour, day, slot_key) if prev_hour else None
    prev_ledger = prev.get("ledger") if isinstance(prev, dict) else None
    if ledger is None:
        # 快照缺失（如 2026-09-18 容器重启打断 19:45 组表）时，绝不把空台账推给群：
        # 先现场采一次，再退回上一档台账，最后才允许空表（并告警）。
        ledger = _collect_for_missing_snapshot(tz_name, day, hour) or prev_ledger
        if ledger is None:
            notify(
                "督战官无快照且兜底采集失败",
                f"{tz_name} {day} {hour:02d}:00 将按空表推送（全部待确认）",
            )
    sent: list[str] = []
    failed: list[str] = []
    for group in groups_for_tz(tz_name, channel_ids):
        body = ""
        if isinstance(bodies, dict):
            body = str(bodies.get(group.channel_id) or "")
        if not body:
            from app.duzhan_admin import ai_rules

            override = ai_rules.focus_for(group, hour, day, ledger)
            body = render_brief(group, hour, now, ledger, prev_ledger, focus_override=override)
        ok = push_duzhan_message(
            body,
            group.channel_id,
            idempotency_key=_idempotency_key(tz_name, day, hour, group.channel_id),
        )
        if ok:
            sent.append(group.name)
        else:
            failed.append(group.name)
            logger.warning("督战官推送失败 {} {}", group.name, group.channel_id)
    return {"tz": tz_name, "hour": hour, "sent": sent, "failed": failed, "from_snapshot": bool(snapshot)}


BOT_NAME = "海外渠道督战官"
AT_NAMES = (BOT_NAME, "Overseas Channel Battle Commander")
_OWN_PREFIXES = (
    "【海外渠道督战官】",
    "【海外渠道业绩达标群",
    "[Overseas Channel Battle Commander]",
    "[Overseas Channel Daily Triple Chase]",
)
_AT_RE = re.compile(
    r"[@＠]\s*(海外渠道督战官|Overseas Channel Battle Commander)",
    re.IGNORECASE,
)
_MATH_RE = re.compile(
    r"^\s*(\d+)\s*([+\-*/x×])\s*(\d+)\s*[=＝?？]*\s*$"
)
_OPS = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "x": operator.mul,
    "×": operator.mul,
    "/": operator.truediv,
}
_CURSOR_FILE = "runtime/duzhan_at_cursor.json"


def message_body(message: dict) -> str:
    """取出 IM 消息正文。"""
    body = message.get("body") or message.get("content") or ""
    if isinstance(body, dict):
        return str(body.get("text") or "")
    return str(body)


def is_own_bot_message(message: dict, body: str) -> bool:
    """跳过督战官自己发的消息。"""
    if any(body.startswith(prefix) for prefix in _OWN_PREFIXES):
        return True
    bot_id = str(message.get("bot_id") or "")
    if bot_id == "aaaaaaa6-aaaa-4aaa-8aaa-aaaaaaaaaaa6":
        return True
    sender = str(message.get("bot_name") or message.get("sender_name") or "")
    return sender in AT_NAMES


def is_at_duzhan(body: str) -> bool:
    """是否 @了海外渠道督战官。未 @ 一律不回。"""
    return bool(_AT_RE.search(body or ""))


def strip_at(body: str) -> str:
    """去掉 @督战官 后剩下的问题。"""
    return _AT_RE.sub(" ", body or "").strip()


def draft_at_reply(question: str, lang: str) -> str:
    """被 @ 后的短答：先算式，再身份；其余确认收到。"""
    text = (question or "").strip()
    if not text:
        return "Here." if lang == "en" else "在。"
    math = _MATH_RE.match(text)
    if math:
        left, op_text, right = math.group(1), math.group(2), math.group(3)
        op = _OPS[op_text]
        value = op(int(left), int(right))
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value)
    if lang == "en":
        return (
            "Noted. I only reply when @mentioned. "
            "Ask about today's target, overdue follow-up, or the next action."
        )
    return "收到。只回复 @我 的消息。问本群达标进度、逾期跟单或下一步即可。"


def _cursor_path() -> Path:
    path = get_settings().data_dir / _CURSOR_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_cursor() -> dict:
    path = _cursor_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"channels": {}}
    channels = payload.get("channels")
    return {"channels": channels} if isinstance(channels, dict) else {"channels": {}}


def _save_cursor(payload: dict) -> None:
    """原子写游标：先写临时文件再 os.replace，避免崩溃留下半个 JSON（2026-09-20 审查）。"""
    path = _cursor_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _cursor_row(last_created_at: str, answered: list[str], limit: int = 200) -> dict:
    """游标里「单个频道」的状态行。

    读写必须同一套 schema（last_created_at / answered_ids）：2026-09-20 的逐条落盘
    曾经写成 last_seen / answered，和读取端不一致，下一次轮询会把游标当成空的重新
    初始化（跳过待答的 @），并且丢掉已答 id 集合（可能重答）。这里统一成一个构造函数。
    """
    return {"last_created_at": last_created_at, "answered_ids": answered[-limit:]}


def _remember_answered(answered: list[str], message_id: str, limit: int = 200) -> list[str]:
    """记一条已答 id，保持写入顺序（先进先出淘汰），不按字典序排序。

    按字典序截断会随机丢掉「最近答过的 id」，一旦 last_created_at 没能前进就会重答。
    """
    if message_id not in answered:
        answered.append(message_id)
    return answered[-limit:] if len(answered) > limit else list(answered)


def _fetch_recent(channel_id: str, date_from: str) -> list[dict]:
    from app.vertu.client import run_vertu_sync_json

    payload = run_vertu_sync_json(
        [
            "im",
            "+history",
            "--channel-id",
            channel_id,
            "--date-from",
            date_from,
            "--limit",
            "30",
        ],
        timeout=25.0,
    )
    if not isinstance(payload, dict):
        return []
    return [item for item in (payload.get("messages") or []) if isinstance(item, dict)]


def poll_at_mentions() -> dict:
    """扫描达标群：仅当正文 @海外渠道督战官 时回复。

    首次运行只记游标、不回历史 @，避免把旧测试题再答一遍。
    """
    state = _load_cursor()
    channels = state["channels"]
    replied: list[str] = []
    skipped_init: list[str] = []
    for group in all_groups():
        state_row = channels.get(group.channel_id) or {}
        last_seen = str(state_row.get("last_created_at") or "")
        answered = list(state_row.get("answered_ids") or [])
        answered_set = set(answered)
        date_from = last_seen or "2026-01-01T00:00:00Z"
        try:
            messages = _fetch_recent(group.channel_id, date_from)
        except Exception as exc:  # noqa: BLE001
            logger.warning("督战官拉历史失败 {}: {}", group.name, exc)
            continue
        messages.sort(key=lambda item: str(item.get("created_at") or ""))
        if not last_seen:
            newest = str(messages[-1].get("created_at") or "") if messages else ""
            channels[group.channel_id] = _cursor_row(newest, answered)
            skipped_init.append(group.name)
            continue
        newest = last_seen
        for message in messages:
            created = str(message.get("created_at") or "")
            if created:
                newest = max(newest, created)
            message_id = str(message.get("id") or "")
            if not message_id or message_id in answered_set:
                continue
            if created and created < last_seen:
                continue
            body = message_body(message)
            if is_own_bot_message(message, body) or not is_at_duzhan(body):
                continue
            reply = draft_at_reply(strip_at(body), group.lang)
            ok = push_duzhan_message(
                reply,
                group.channel_id,
                parent_message_id=message_id,
                # 幂等键带原消息 id：回复成功但进程在落游标前崩溃，重启也不会重复回答（2026-09-20 审查）
                idempotency_key=f"duzhan-at-{message_id}",
            )
            if ok:
                replied.append(f"{group.name}:{message_id}")
                answered_set.add(message_id)
                answered = _remember_answered(answered, message_id)
                # 立刻落盘：崩溃重启后不会重复回答同一条 @（原来整轮结束才写）。
                # 注意与读取端同一套 schema，见 _cursor_row。
                state["channels"][group.channel_id] = _cursor_row(
                    max(newest, created or ""), answered
                )
                try:
                    _save_cursor(state)
                except OSError as exc:  # noqa: BLE001 — 落盘失败不影响已发出的回复
                    logger.warning("督战官游标落盘失败: {}", exc)
            else:
                logger.warning("督战官 @回复失败 {} {}", group.name, message_id)
        channels[group.channel_id] = _cursor_row(newest, answered)
    _save_cursor(state)
    return {"replied": replied, "initialized": skipped_init}
