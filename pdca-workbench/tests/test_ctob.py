# -*- coding: utf-8 -*-
"""C转B 20:00 WhatsApp 晚追。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.ctob import (
    CtobOwner,
    classify_lead,
    infer_blockers,
    parse_cohort,
    parse_wa_chats,
    parse_wa_summary,
    render_brief,
    run_ctob,
)

OWNER = CtobOwner("跟进人01", "ccccccc1-cccc-4ccc-8ccc-ccccccccccc1", 31)


class RetiredGroupTests(unittest.TestCase):
    def test_xiahuan_group_is_not_collected_registered_or_sent(self):
        from app.agents.group_context import ctob_group_configs
        from app.ctob import OWNERS

        retired_channel = "eeeeeee3-eeee-4eee-8eee-eeeeeeeeeee3"
        self.assertNotIn(retired_channel, {g.channel_id for g in ctob_group_configs("2026-09-22")})
        self.assertEqual(len(OWNERS), 15)
        for hour in (10, 15, 20):
            with self.subTest(hour=hour), patch("app.ctob.is_duzhan_workday", return_value=True), patch(
                "app.ctob.collect_owner", return_value={"summary": {}, "chats": [], "cohort": {}}
            ) as collect, patch("app.ctob.save_snapshot"), patch(
                "app.ctob.load_snapshot", return_value={}
            ), patch("app.ctob.render_brief", return_value="test-only"), patch(
                "app.ctob.push_duzhan_message", return_value=True
            ) as push:
                result = run_ctob("2026-09-22", hour=hour)
            self.assertNotIn("成员庚", result["sent"])
            self.assertEqual(len(result["sent"]), 15)
            self.assertNotIn(retired_channel, {call.args[0].channel_id for call in collect.call_args_list})
            self.assertNotIn(retired_channel, {call.args[1] for call in push.call_args_list})

CUSTOMERS = {
    "rows": [
        {
            "row_type": "summary",
            "reached_customer_count": 21,
            "replied_customer_count": 5,
            "outbound_message_count": 52,
            "inbound_message_count": 28,
            "new_customer_count": 4,
            "is_complete": True,
        },
        {
            "row_type": "detail",
            "customer_nickname": "S 9.7 静默耳机 澳大利亚 Demo Customer",
            "country": "澳大利亚",
            "outbound_message_count": 14,
            "inbound_message_count": 17,
            "replied": True,
            "touched": True,
            "last_message_time": "2026-09-16 14:42:27",
        },
        {
            "row_type": "detail",
            "customer_nickname": "9.13弃单Asma Aktar美国",
            "country": "美国",
            "outbound_message_count": 14,
            "inbound_message_count": 17,
            "replied": True,
            "touched": True,
            "last_message_time": "2026-09-16 08:29:00",
        },
        {
            "row_type": "detail",
            "customer_nickname": "汽车预售 迪拜线索",
            "country": "阿联酋",
            "outbound_message_count": 3,
            "inbound_message_count": 0,
            "replied": False,
            "touched": True,
            "last_message_time": "2026-09-16 14:59:15",
        },
        {
            "row_type": "detail",
            "customer_nickname": "160***1900",
            "country": "美国",
            "outbound_message_count": 1,
            "inbound_message_count": 1,
            "replied": True,
            "touched": True,
            "last_message_time": "2026-09-16 14:58:17",
        },
    ]
}

COHORT = {
    "summary": {
        "new_customer_count": 4,
        "has_chat_count": 4,
        "has_reply_count": 0,
        "in_intent_pipeline_count": 0,
    },
    "rows": [
        {
            "customer_display_name": "918***3361",
            "has_chat": True,
            "has_reply": False,
            "outbound_message_count": 1,
            "inbound_message_count": 0,
            "stage": "not_in_intent_ledger",
            "latest_progress": None,
        },
        {
            "customer_display_name": "官网询盘 印度",
            "has_chat": True,
            "has_reply": False,
            "outbound_message_count": 1,
            "inbound_message_count": 0,
            "stage": "not_in_intent_ledger",
            "latest_progress": None,
        },
    ],
}


class CtobLongFormatMixin:
    """断言老长版 C转B 文案时关掉精简（2026-09-19 起默认精简）。"""

    def setUp(self):
        super().setUp()
        from app.config import get_settings

        settings = get_settings()
        self._compact_backup = getattr(settings, "ctob_compact", True)
        settings.ctob_compact = False
        self.addCleanup(self._restore_compact)

    def _restore_compact(self):
        from app.config import get_settings

        get_settings().ctob_compact = self._compact_backup


class CtobCompactTests(unittest.TestCase):
    """C转B 三档总结版（明细走 08:00 证据 HTML）。"""

    def _chats(self) -> list[dict]:
        return [
            {
                "name": "汽车预售 迪拜线索",
                "country": "阿联酋",
                "kind": "汽车",
                "outbound": 3,
                "inbound": 1,
                "rounds": 2,
                "replied": False,
                "last": "2026-09-18 10:00",
            }
        ]

    def test_compact_morning(self):
        prev = {"summary": {"reached": 21, "replied": 5, "car": 3, "ctob": 2}, "chats": self._chats()}
        text = render_brief(
            OWNER, "2026-09-19", {}, [], parse_cohort(COHORT), hour=10, prev=prev
        )
        self.assertIn("【海外渠道督战官｜10:00 C转B早追｜2026-09-19】", text)
        self.assertIn("昨日全天：WA 触达 21 / 回复 5", text)
        self.assertIn("请回：今日汽车/转B 3–5 项", text)
        self.assertLessEqual(len(text.splitlines()), 8)

    def test_compact_midday_and_evening(self):
        prev = {"summary": {"reached": 21, "replied": 5}, "chats": self._chats()}
        curr = {"reached": 30, "replied": 8, "car": 4, "ctob": 3, "other": 1}
        text15 = render_brief(
            OWNER, "2026-09-19", curr, self._chats(), {}, hour=15, prev=prev
        )
        self.assertIn("本档新增：触达 +9 户 / 回复 +3 户（对照 10:00 档）", text15)
        self.assertIn("请回：相对 10:00 的变化", text15)
        text20 = render_brief(OWNER, "2026-09-19", curr, self._chats(), {}, hour=20)
        self.assertIn("本档口径：WA 触达 30 / 回复 8", text20)
        self.assertIn("重点客户：汽车预售 迪拜线索", text20)
        self.assertNotIn("回复格式", text20)


class CtobTests(CtobLongFormatMixin, unittest.TestCase):
    def test_classify_car_and_ctob_not_headset(self):
        self.assertEqual(classify_lead("汽车预售 迪拜线索"), "汽车")
        self.assertEqual(classify_lead("9.13弃单Asma"), "转B")
        self.assertEqual(classify_lead("手机维修店也卖手机/想做"), "转B")
        self.assertEqual(classify_lead("S 9.7 静默耳机 Demo Customer"), "C端")
        self.assertEqual(classify_lead("160***1900"), "未标")

    def test_parse_keeps_focus_drops_headset(self):
        summary = parse_wa_summary(CUSTOMERS)
        chats = parse_wa_chats(CUSTOMERS)
        names = [item["name"] for item in chats]
        self.assertEqual(summary["reached"], 21)
        self.assertEqual(summary["car"], 1)
        self.assertEqual(summary["ctob"], 1)
        self.assertEqual(summary["other"], 2)
        self.assertIn("9.13弃单Asma Aktar美国", names)
        self.assertIn("汽车预售 迪拜线索", names)
        self.assertNotIn("S 9.7 静默耳机 澳大利亚 Demo Customer", names)
        self.assertEqual(chats[0]["rounds"], 14)

    def test_blockers_focus_only(self):
        chats = parse_wa_chats(CUSTOMERS)
        cohort = parse_cohort(COHORT)
        lines = infer_blockers(chats, cohort)
        self.assertTrue(any("客户在等回复" in item for item in lines))
        self.assertTrue(any("等客户回" in item for item in lines))
        self.assertTrue(lines[0].startswith("9.13弃单"))
        self.assertEqual(cohort["focus_new"], 1)
        self.assertTrue(any("官网询盘" in item for item in lines))
        self.assertFalse(any("918" in item for item in lines))

    def test_brief_has_support_and_no_triple_chase(self):
        text = render_brief(
            OWNER,
            "2026-09-16",
            parse_wa_summary(CUSTOMERS),
            parse_wa_chats(CUSTOMERS),
            parse_cohort(COHORT),
        )
        self.assertIn("@跟进人01", text)
        self.assertIn("[转B]", text)
        self.assertIn("[汽车]", text)
        self.assertIn("14轮", text)
        self.assertIn("先回人", text)
        self.assertNotIn("工时估算", text)
        self.assertNotIn("Demo Customer", text)
        self.assertNotIn("静默耳机", text)
        self.assertNotIn("10:00", text)
        self.assertNotIn("红榜", text)

    def test_weekend_skip(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        saturday = datetime(2026, 9, 19, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with patch("app.ctob.push_duzhan_message") as push:
            result = run_ctob("2026-09-19", saturday)
        self.assertEqual(result.get("skipped"), "weekend")
        push.assert_not_called()


class CtobSlotTests(CtobLongFormatMixin, unittest.TestCase):
    """C转B 与达标群同结构：10:00 定任务 / 15:00 追变化 / 20:00 验兑现。"""

    def _prev(self) -> dict:
        return {
            "summary": {"reached": 21, "replied": 5, "car": 3, "ctob": 2},
            "chats": [
                {
                    "name": "汽车预售 迪拜线索",
                    "country": "阿联酋",
                    "kind": "汽车",
                    "outbound": 3,
                    "inbound": 0,
                    "rounds": 1,
                    "replied": False,
                    "last": "2026-09-16 09:00",
                },
                {
                    "name": "S 9.7 静默耳机 澳大利亚 Demo Customer",
                    "country": "澳大利亚",
                    "kind": "C端",
                    "outbound": 14,
                    "inbound": 17,
                    "rounds": 14,
                    "replied": True,
                    "last": "2026-09-16 14:42",
                },
            ],
            "cohort": {},
        }

    def test_morning_slot_carries_yesterday_and_new_queue(self):
        text = render_brief(
            OWNER,
            "2026-09-17",
            {},
            [],
            parse_cohort(COHORT),
            hour=10,
            prev=self._prev(),
        )
        self.assertIn("【海外渠道督战官｜10:00 C转B早追｜2026-09-17】", text)
        self.assertIn("本档动作：报今日汽车/转B 3–5 项", text)
        self.assertIn("昨日全天：WA总触达 21 / 回复 5", text)
        self.assertIn("昨日未回结转（今日第一动作）：汽车预售 迪拜线索（1轮）", text)
        self.assertIn("今日新客队列", text)
        self.assertIn("回复格式：客户名 / 汽车或转B", text)
        self.assertNotIn("20:00", text)

    def test_morning_without_prev_says_pending(self):
        text = render_brief(OWNER, "2026-09-17", {}, [], {}, hour=10)
        self.assertIn("昨日全天：待确认（缺昨日 20:00 档快照）", text)
        self.assertIn("未见未回客户", text)

    def test_midday_reports_delta_vs_1000(self):
        curr = {"reached": 30, "replied": 8, "outbound": 60, "inbound": 30, "car": 4, "ctob": 3}
        text = render_brief(
            OWNER,
            "2026-09-17",
            curr,
            parse_wa_chats(CUSTOMERS),
            parse_cohort(COHORT),
            hour=15,
            prev=self._prev(),
        )
        self.assertIn("【海外渠道督战官｜15:00 C转B中追｜2026-09-17】", text)
        self.assertIn("本次新增：触达 +9 户 / 回复 +3 户（对照 10:00 档）", text)
        self.assertIn("本档新回：", text)
        self.assertIn("仍未回：", text)
        self.assertIn("可能要的支持：", text)

    def test_midday_without_prev_says_pending(self):
        text = render_brief(OWNER, "2026-09-17", {}, [], {}, hour=15)
        self.assertIn("本次新增：待确认（缺 10:00 档口径）", text)

    def test_evening_keeps_legacy_layout(self):
        text = render_brief(
            OWNER,
            "2026-09-16",
            parse_wa_summary(CUSTOMERS),
            parse_wa_chats(CUSTOMERS),
            parse_cohort(COHORT),
        )
        self.assertIn("【海外渠道督战官｜20:00 C转B晚追｜2026-09-16】", text)
        self.assertIn("WA总触达", text)
        self.assertNotIn("本档动作", text)
        self.assertNotIn("10:00 C转B早追", text)


class CtobSnapshotTests(unittest.TestCase):
    def test_snapshot_roundtrip_and_missing(self):
        import tempfile
        from pathlib import Path

        from app import ctob

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake_settings = type("S", (), {"data_dir": Path(tmp.name)})()
        with patch("app.ctob.get_settings", return_value=fake_settings):
            self.assertEqual(ctob.load_snapshot("2026-09-17", 10), {})
            ctob.save_snapshot("2026-09-17", 10, {"ch": {"summary": {"reached": 3}}})
            loaded = ctob.load_snapshot("2026-09-17", 10)
        self.assertEqual(loaded["ch"]["summary"]["reached"], 3)

    def test_prev_slot_mapping(self):
        from app.ctob import _prev_slot

        self.assertEqual(_prev_slot("2026-09-17", 10), ("2026-09-16", 20))
        self.assertEqual(_prev_slot("2026-09-17", 15), ("2026-09-17", 10))
        self.assertIsNone(_prev_slot("2026-09-17", 20))

    def test_idempotency_key_carries_slot_hour(self):
        import tempfile
        from datetime import datetime
        from pathlib import Path
        from zoneinfo import ZoneInfo

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake_settings = type("S", (), {"data_dir": Path(tmp.name)})()
        with patch("app.ctob.get_settings", return_value=fake_settings), patch(
            "app.ctob.collect_owner",
            return_value={"summary": {}, "chats": [], "cohort": {}},
        ), patch("app.ctob.push_duzhan_message", return_value=True) as push:
            result = run_ctob(
                "2026-09-17",
                datetime(2026, 9, 17, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
                hour=10,
            )
        from app.ctob import OWNERS as CTOB_OWNERS

        keys = [call.kwargs["idempotency_key"] for call in push.call_args_list]
        self.assertEqual(len(keys), len(CTOB_OWNERS))
        self.assertTrue(all("-1000-" in key for key in keys))
        self.assertEqual(result["hour"], 10)


class CtobRegistrationTests(unittest.TestCase):
    def test_three_slots_with_backups(self):
        from types import SimpleNamespace

        from app.scheduler import jobs as scheduler_jobs

        class RecordingScheduler:
            def __init__(self):
                self.jobs = []

            def add_job(self, func, *args, **kwargs):
                self.jobs.append((func, args, kwargs))

            def start(self):
                return None

        settings = SimpleNamespace(
            scheduler_enabled=True,
            sync_cron="0 6 * * *",
            daily_report_enabled=False,
            todo_remind_enabled=False,
            todo_remind_times=[],
            todo_group_notice_enabled=False,
            todo_group_channel_id="",
            todo_scoring_enabled=False,
            todo_ledger_sync_enabled=False,
            todo_brief_enabled=False,
            todo_okr_link_enabled=False,
            mto_temp_cleanup_enabled=False,
            daily_digest_enabled=False,
            ctob_enabled=True,
            ctob_times=["10:00", "15:00", "20:00"],
        )
        original = scheduler_jobs._scheduler
        scheduler_jobs._scheduler = None
        try:
            with patch.object(
                scheduler_jobs, "get_settings", return_value=settings
            ), patch.object(
                scheduler_jobs, "BackgroundScheduler", RecordingScheduler
            ):
                scheduler = scheduler_jobs.start_scheduler()
        finally:
            scheduler_jobs._scheduler = original
        slots = {
            kwargs["id"]: (kwargs["hour"], kwargs["minute"])
            for _, _, kwargs in scheduler.jobs
            if str(kwargs.get("id", "")).startswith("ctob")
        }
        # 备份任务按“兜底时刻”命名，20:00 档保持历史 id ctob_backup_2030
        self.assertEqual(slots["ctob_1000"], (10, 0))
        self.assertEqual(slots["ctob_backup_1030"], (10, 30))
        self.assertEqual(slots["ctob_1500"], (15, 0))
        self.assertEqual(slots["ctob_backup_1530"], (15, 30))
        self.assertEqual(slots["ctob_2000"], (20, 0))
        self.assertEqual(slots["ctob_backup_2030"], (20, 30))


if __name__ == "__main__":
    unittest.main()
