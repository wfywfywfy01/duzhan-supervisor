# -*- coding: utf-8 -*-
"""督战官：Alice 用巴黎时间/英文，其余北京时间/中文。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import unittest

from app.duzhan import (
    TZ_PARIS,
    TZ_SHANGHAI,
    collect_clock,
    cron_timezones,
    draft_at_reply,
    groups_for_tz,
    is_at_duzhan,
    load_prepared,
    parse_hours,
    poll_at_mentions,
    prepare_duzhan,
    render_brief,
    run_duzhan,
    strip_at,
)


class LongFormatMixin:
    """断言老长版文案时，把精简档位关掉（2026-09-19 起默认精简）。"""

    def setUp(self):
        super().setUp()
        from app.config import get_settings

        settings = get_settings()
        self._compact_backup = getattr(settings, "duzhan_compact", True)
        settings.duzhan_compact = False
        self.addCleanup(self._restore_compact)

    def _restore_compact(self):
        from app.config import get_settings

        get_settings().duzhan_compact = self._compact_backup


class DuzhanGroupTests(LongFormatMixin, unittest.TestCase):
    def test_alice_is_paris_english(self):
        paris = groups_for_tz(TZ_PARIS)
        self.assertEqual([g.name for g in paris], ["Alice业绩达标群"])
        self.assertEqual(paris[0].lang, "en")

    def test_other_groups_are_shanghai_chinese(self):
        shanghai = groups_for_tz(TZ_SHANGHAI)
        names = [g.name for g in shanghai]
        self.assertEqual(
            names,
            [
                "新人小组业绩达标群",
                "张三业绩达标群",
                "李四业绩达标群",
                "Bella业绩达标群",
            ],
        )
        self.assertTrue(all(g.lang == "zh" for g in shanghai))

    def test_cron_registers_both_timezones(self):
        self.assertEqual(cron_timezones(), [TZ_SHANGHAI, TZ_PARIS])

    def test_parse_hours_default_slots(self):
        self.assertEqual(parse_hours(["10:00", "15:00", "20:00"]), [10, 15, 20])

    def test_collect_runs_before_push(self):
        self.assertEqual(collect_clock(10, 15), (9, 45))
        self.assertEqual(collect_clock(15, 15), (14, 45))
        self.assertEqual(collect_clock(20, 15), (19, 45))
        self.assertEqual(collect_clock(10, 10), (9, 50))

    def test_alice_brief_uses_paris_clock(self):
        group = groups_for_tz(TZ_PARIS)[0]
        now = datetime(2026, 9, 15, 10, 0, tzinfo=ZoneInfo(TZ_PARIS))
        text = render_brief(group, 10, now)
        self.assertIn("Paris time", text)
        self.assertIn("10:00", text)
        self.assertIn("Morning chase", text)
        self.assertIn("pending", text)
        self.assertIn("wan", text)
        self.assertIn("Department campaign", text)
        self.assertIn("No plan posted", text)
        self.assertNotIn("北京", text)
        self.assertNotIn("待确认", text)
        self.assertNotIn("万", text)
        self.assertNotIn("客户A", text)

    def test_alice_ledger_body_is_english(self):
        group = groups_for_tz(TZ_PARIS)[0]
        now = datetime(2026, 9, 15, 20, 0, tzinfo=ZoneInfo(TZ_PARIS))
        ledger = {
            "today_target": "部门月度战役",
            "people": [
                {
                    "group": "Alice业绩达标群",
                    "display": "Alice",
                    "target_wan": 400,
                    "mtd_wan": 55,
                    "collections": [
                        {
                            "title": "土耳其合同与伊斯坦布尔市场保护 今天与迪拜经销商讨论了土耳其合同。",
                            "amount": "",
                            "progress": "待确认",
                        }
                    ],
                    "blockers": ["hi Gary关于土耳其合同，他们让我先向你确认并获得你对以下两点的批准，然后再加入合同： 1. 折扣："],
                    "evidence": [],
                    "vemory_ok": False,
                    "mto_count": 4,
                    "mto_names": ["a.jpg"],
                }
            ],
            "red": [{"display": "Alice", "mtd_wan": 55, "reason": "WhatsApp未覆盖"}],
            "black": [{"display": "Sofia", "reason": "WhatsApp未覆盖；本月已录单0"}],
            "penalties": [],
        }
        text = render_brief(group, 20, now, ledger)
        self.assertIn("Turkey contract", text)
        self.assertIn("discount", text)
        self.assertIn("WhatsApp not covered", text)
        self.assertIn("MTD booked 0", text)
        self.assertNotIn("待确认", text)
        self.assertNotIn("土耳其", text)
        # 英文档里不允许漏出中文：文案映射表（duzhan.py 的 zh→en 映射）与生成端
        # 必须成对改名，否则会像 2026-09-20 的「本月回款0→本月已录单0」那样只改一半。
        leftovers = sorted({ch for ch in text if "\u4e00" <= ch <= "\u9fff"})
        self.assertEqual(leftovers, [], f"英文正文里仍有中文: {leftovers}")
        self.assertNotIn("折扣", text)
        self.assertNotIn("本月回款", text)

    def test_shanghai_brief_uses_beijing_clock(self):
        group = groups_for_tz(TZ_SHANGHAI)[0]
        now = datetime(2026, 9, 15, 10, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        text = render_brief(group, 10, now)
        self.assertIn("北京时间", text)
        self.assertIn("早追·定任务", text)
        self.assertIn("每日三追进度表", text)
        self.assertIn("待确认", text)
        self.assertIn("部门月度战役", text)

    def test_midday_and_evening_slot_titles(self):
        group = groups_for_tz(TZ_SHANGHAI)[0]
        now = datetime(2026, 9, 15, 15, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        self.assertIn("中追·追变化", render_brief(group, 15, now))
        self.assertIn("晚追·验兑现", render_brief(group, 20, now))

    def test_weekend_skips_push(self):
        saturday = datetime(2026, 9, 19, 10, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        with patch("app.duzhan.push_duzhan_message") as push:
            result = run_duzhan(TZ_SHANGHAI, 10, saturday)
        self.assertEqual(result["sent"], [])
        self.assertEqual(result["skipped"], "weekend")
        push.assert_not_called()

    def test_midday_renders_only_changes(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 16, 15, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        prev = {
            "people": [
                {
                    "group": "张三业绩达标群",
                    "display": "张三",
                    "collections": [
                        {"title": "柬埔寨", "progress": "待确认", "status": "planned"},
                        {"title": "越南尾款", "progress": "催收", "status": "progress"},
                        {"title": "马来Derict", "progress": "已询问", "status": "planned"},
                    ],
                }
            ]
        }
        curr = {
            "people": [
                {
                    "group": "张三业绩达标群",
                    "display": "张三",
                    "target_wan": 200,
                    "mtd_wan": 170.2,
                    "collections": [
                        {"title": "柬埔寨", "progress": "已到账", "status": "done"},
                        {"title": "越南尾款", "progress": "催收", "status": "progress"},
                    ],
                }
            ]
        }
        text = render_brief(group, 15, now, curr, prev)
        self.assertIn("完成", text)
        self.assertIn("柬埔寨", text)
        self.assertIn("停滞", text)
        self.assertIn("越南尾款", text)
        self.assertIn("未回复", text)
        self.assertIn("马来Derict", text)
        self.assertNotIn("VPS 留痕", text)
        self.assertIn("VPS 留痕", render_brief(group, 10, now, curr))

    def test_missing_snapshot_collects_live_not_empty(self):
        """快照缺失时必须现场补采，不能把整屏“待确认”推给群（2026-09-18 20:00 事故）。"""
        group = groups_for_tz(TZ_SHANGHAI)[1]
        ledger = {
            "day": "2026-09-18",
            "today_target": "部门月度战役",
            "people": [
                {
                    "group": "张三业绩达标群",
                    "display": "张三",
                    "target_wan": 200,
                    "mtd_wan": 170.2,
                    "daily_target_wan": 6.67,
                    "rolling_target_wan": 120.1,
                    "target_gap_wan": 50.1,
                    "target_ahead": True,
                }
            ],
            "red": [],
            "black": [],
        }
        with patch("app.duzhan.load_prepared", return_value=None), patch(
            "app.duzhan.collect_ledger", return_value=ledger
        ) as collect, patch("app.duzhan.push_duzhan_message", return_value=True) as push:
            result = run_duzhan(
                TZ_SHANGHAI,
                20,
                datetime(2026, 9, 18, 20, 0, tzinfo=ZoneInfo(TZ_SHANGHAI)),
            )
        collect.assert_called_once_with("2026-09-18")
        bodies = {
            call.args[1]: call.args[0] for call in push.call_args_list
        }
        yu_bing = bodies.get("22222222-2222-4222-8222-222222222222") or ""
        self.assertTrue(yu_bing, "必须推到张三群")
        self.assertIn("已录单（开单额）：170.2 万", yu_bing, "兜底采集后必须是真实数字")
        self.assertIn("滚动日目标", yu_bing)
        self.assertFalse(result["from_snapshot"])

    def test_run_duzhan_paris_only_hits_alice(self):
        empty = {"day": "2026-09-15", "people": [], "red": [], "black": []}
        with patch("app.duzhan.push_duzhan_message", return_value=True) as push, patch(
            "app.duzhan.collect_ledger", return_value=empty
        ):
            result = run_duzhan(
                TZ_PARIS,
                10,
                datetime(2026, 9, 15, 10, 0, tzinfo=ZoneInfo(TZ_PARIS)),
            )
        self.assertEqual(result["sent"], ["Alice业绩达标群"])
        self.assertEqual(len(push.call_args_list), 1)
        body = push.call_args.args[0]
        channel = push.call_args.args[1]
        self.assertIn("Paris time", body)
        self.assertIn("Daily Triple Chase", body)
        self.assertEqual(channel, "55555555-5555-4555-8555-555555555555")

    def test_prepare_then_push_uses_snapshot(self):
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake_settings = type("S", (), {"data_dir": Path(tmp.name)})()
        now = datetime(2026, 9, 15, 9, 45, tzinfo=ZoneInfo(TZ_PARIS))
        with patch("app.duzhan.get_settings", return_value=fake_settings), patch(
            "app.duzhan.collect_ledger", return_value={"day": "2026-09-15", "people": [], "red": [], "black": [], "today_target": "部门月度战役"}
        ):
            prepared = prepare_duzhan(TZ_PARIS, 10, now)
            loaded = load_prepared(TZ_PARIS, 10, "2026-09-15")
        self.assertEqual(prepared["hour"], 10)
        self.assertIn("55555555-5555-4555-8555-555555555555", prepared["messages"])
        self.assertEqual(loaded["prepared_at"], prepared["prepared_at"])
        with patch("app.duzhan.get_settings", return_value=fake_settings), patch(
            "app.duzhan.push_duzhan_message", return_value=True
        ) as push, patch("app.duzhan.render_brief") as render:
            result = run_duzhan(
                TZ_PARIS,
                10,
                datetime(2026, 9, 15, 10, 0, tzinfo=ZoneInfo(TZ_PARIS)),
            )
        render.assert_not_called()
        self.assertTrue(result["from_snapshot"])
        self.assertEqual(push.call_args.args[0], prepared["messages"]["55555555-5555-4555-8555-555555555555"])


class DuzhanPushTests(unittest.TestCase):
    def test_duzhan_push_uses_own_bot_not_daily_report(self):
        from app import vps_im_push

        with patch("app.vps_im_push.httpx.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"ok": True}
            with patch.dict(
                "os.environ",
                {
                    "PDCA_DUZHAN_BOT_APP_ID": "vbot_duzhan",
                    "PDCA_DUZHAN_BOT_APP_SECRET": "secret_duzhan",
                    "PDCA_VPS_BOT_APP_ID": "vbot_daily",
                    "PDCA_VPS_BOT_APP_SECRET": "secret_daily",
                },
            ):
                ok = vps_im_push.push_duzhan_message("hi", "chan-alice")
        self.assertTrue(ok)
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["x-vertu-bot-app-id"], "vbot_duzhan")
        self.assertEqual(post.call_args.kwargs["json"]["channel_id"], "chan-alice")
        self.assertNotIn("idempotency_key", post.call_args.kwargs["json"])

    def test_duzhan_push_sends_idempotency_key(self):
        from app import vps_im_push

        with patch("app.vps_im_push.httpx.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"ok": True}
            with patch.dict(
                "os.environ",
                {
                    "PDCA_DUZHAN_BOT_APP_ID": "vbot_duzhan",
                    "PDCA_DUZHAN_BOT_APP_SECRET": "secret_duzhan",
                },
            ):
                ok = vps_im_push.push_duzhan_message(
                    "hi",
                    "chan-alice",
                    idempotency_key="duzhan-20260915-1000-x",
                )
        self.assertTrue(ok)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["idempotency_key"], "duzhan-20260915-1000-x")
        self.assertEqual(payload["client_message_id"], "duzhan-20260915-1000-x")
        self.assertEqual(post.call_args.kwargs["headers"]["Idempotency-Key"], "duzhan-20260915-1000-x")


class DuzhanAtReplyTests(unittest.TestCase):
    def test_only_at_bot_triggers(self):
        self.assertTrue(is_at_duzhan("@海外渠道督战官 who is the president"))
        self.assertTrue(is_at_duzhan("进度如何 ＠海外渠道督战官"))
        self.assertFalse(is_at_duzhan("三角形内角和=？"))
        self.assertFalse(is_at_duzhan("@李四 同步进度"))

    def test_strip_leaves_the_question(self):
        self.assertEqual(strip_at("@海外渠道督战官 1+1=？"), "1+1=？")

    def test_math_reply(self):
        self.assertEqual(draft_at_reply("2+1=？", "zh"), "3")
        self.assertEqual(draft_at_reply("1+1", "en"), "2")

    def test_empty_at_is_ack(self):
        self.assertEqual(draft_at_reply("", "zh"), "在。")
        self.assertEqual(draft_at_reply("", "en"), "Here.")


    def test_reply_save_keeps_same_cursor_schema(self):
        """2026-09-20 修：逐条落盘的游标曾写成 last_seen/answered，读取端只认
        last_created_at/answered_ids → 下一次轮询会把游标当空的重初始化（漏答 @），
        并丢掉已答 id 集合（重答）。"""
        import json
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        runtime = root / "runtime"
        runtime.mkdir()
        channel = "55555555-5555-4555-8555-555555555555"
        cursor = runtime / "duzhan_at_cursor.json"
        cursor.write_text(
            json.dumps(
                {"channels": {channel: {"last_created_at": "2026-09-15T07:40:00Z", "answered_ids": []}}}
            ),
            encoding="utf-8",
        )
        fake_settings = type("S", (), {"data_dir": root})()
        first = [
            {
                "id": "q1",
                "created_at": "2026-09-15T07:50:00Z",
                "body": "@海外渠道督战官 1+1=？",
            }
        ]
        later = first + [
            {
                "id": "q2",
                "created_at": "2026-09-15T07:55:00Z",
                "body": "@海外渠道督战官 2+1=？",
            }
        ]
        replies: list[str] = []

        def fake_fetch(channel_id, date_from):
            if channel_id != channel:
                return []
            return list(later if replies else first)

        with patch("app.duzhan.get_settings", return_value=fake_settings), patch(
            "app.duzhan._fetch_recent", side_effect=fake_fetch
        ), patch(
            "app.duzhan.push_duzhan_message", side_effect=lambda body, cid, **kw: replies.append(kw.get("parent_message_id")) or True
        ) as push:
            first_result = poll_at_mentions()
            saved = json.loads(cursor.read_text(encoding="utf-8"))["channels"][channel]
            self.assertIn("last_created_at", saved, "落盘必须沿用读取端的 schema")
            self.assertIn("answered_ids", saved)
            self.assertIn("q1", saved["answered_ids"])
            self.assertEqual(saved["last_created_at"], "2026-09-15T07:50:00Z")
            second_result = poll_at_mentions()

        self.assertEqual(len(first_result["replied"]), 1, first_result)
        self.assertEqual(len(second_result["replied"]), 1, second_result)
        self.assertTrue(second_result["replied"][0].endswith(":q2"), second_result)
        self.assertEqual(replies, ["q1", "q2"], "同一条 @ 只能答一次")
        self.assertEqual(push.call_count, 2)

    def test_answered_ids_keep_insertion_order(self):
        from app.duzhan import _remember_answered

        answered: list[str] = []
        for item in ("b", "a", "c"):
            answered = _remember_answered(answered, item, limit=2)
        self.assertEqual(answered, ["a", "c"], "按写入顺序 FIFO 淘汰，不按字典序")
        self.assertEqual(_remember_answered(answered, "c", limit=2), ["a", "c"], "重复 id 不重复记账")

    def test_first_poll_does_not_answer_history(self):
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake_settings = type("S", (), {"data_dir": Path(tmp.name)})()
        history = [
            {
                "id": "old-at",
                "created_at": "2026-09-15T07:46:56Z",
                "body": "@海外渠道督战官 old question",
            }
        ]
        with patch("app.duzhan.get_settings", return_value=fake_settings), patch(
            "app.duzhan._fetch_recent", return_value=history
        ), patch("app.duzhan.push_duzhan_message") as push:
            result = poll_at_mentions()
        self.assertEqual(len(result["initialized"]), 5)
        push.assert_not_called()

    def test_later_poll_replies_only_new_at(self):
        import json
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        runtime = root / "runtime"
        runtime.mkdir()
        (runtime / "duzhan_at_cursor.json").write_text(
            json.dumps(
                {
                    "channels": {
                        "55555555-5555-4555-8555-555555555555": {
                            "last_created_at": "2026-09-15T07:40:00Z",
                            "answered_ids": [],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        fake_settings = type("S", (), {"data_dir": root})()

        def fake_fetch(channel_id: str, date_from: str):
            if channel_id != "55555555-5555-4555-8555-555555555555":
                return []
            return [
                {
                    "id": "noise",
                    "created_at": "2026-09-15T07:50:00Z",
                    "body": "三角形内角和=？",
                },
                {
                    "id": "hit",
                    "created_at": "2026-09-15T07:51:00Z",
                    "body": "@海外渠道督战官 1+1=？",
                },
            ]

        with patch("app.duzhan.get_settings", return_value=fake_settings), patch(
            "app.duzhan._fetch_recent", side_effect=fake_fetch
        ), patch("app.duzhan.push_duzhan_message", return_value=True) as push:
            result = poll_at_mentions()
        self.assertEqual(result["replied"], ["Alice业绩达标群:hit"])
        self.assertEqual(len(push.call_args_list), 1)
        self.assertEqual(push.call_args.kwargs["parent_message_id"], "hit")
        self.assertEqual(push.call_args.args[0], "2")


class DuzhanLedgerTests(LongFormatMixin, unittest.TestCase):
    def test_parse_wa_none_when_not_included(self):
        from app.duzhan_ledger import parse_wa_reached

        value, lower = parse_wa_reached(
            {
                "rows": [
                    {
                        "row_type": "summary",
                        "platform_metrics": {
                            "whatsapp": {"included": False, "reached_customer_count": 0}
                        },
                    }
                ]
            }
        )
        self.assertIsNone(value)
        self.assertFalse(lower)

    def test_parse_wa_lower_bound(self):
        from app.duzhan_ledger import parse_wa_reached

        value, lower = parse_wa_reached(
            {
                "is_complete": False,
                "rows": [
                    {
                        "row_type": "summary",
                        "data_freshness": {"overall_status": "partial"},
                        "platform_metrics": {
                            "whatsapp": {"included": True, "reached_customer_count": 5}
                        },
                    }
                ],
            }
        )
        self.assertEqual(value, 5)
        self.assertTrue(lower)

    def test_red_top3_black_two_to_improve(self):
        from app.duzhan_ledger import PersonRow, rank_red_black, score_row

        people = [
            score_row(PersonRow(
                "张三业绩达标群",
                "张三",
                collections=[
                    {"title": "柬埔寨", "status": "done", "amount": "$1"},
                    {"title": "越南", "status": "done"},
                    {"title": "马来", "status": "done"},
                ],
                evidence=["XSD-1"],
            )),
            score_row(PersonRow(
                "Bella业绩达标群",
                "Bella",
                collections=[
                    {"title": "伊朗", "status": "done"},
                    {"title": "科威特", "status": "done"},
                ],
            )),
            score_row(PersonRow(
                "李四业绩达标群",
                "李四",
                collections=[
                    {"title": "索契", "status": "done"},
                    {"title": "Naya", "status": "progress"},
                ],
            )),
            score_row(PersonRow("Alice业绩达标群", "Alice", collections=[{"title": "迪拜", "status": "planned"}])),
            score_row(PersonRow(
                "新人小组业绩达标群",
                "成员A",
                collections=[{"title": "沉睡老客", "progress": "今天可付款", "status": "planned"}],
            )),
        ]
        red, black = rank_red_black(people)
        self.assertEqual([item["display"] for item in red], ["张三", "Bella", "李四"])
        self.assertEqual([item["display"] for item in black], ["成员A", "Alice"])
        self.assertEqual(people[0].score, 110.0)
        self.assertEqual(people[4].score, -10.0)
        self.assertIn("逾期1项", people[4].gaps)
        empty = score_row(PersonRow("新人小组业绩达标群", "Sofia", collections=[]))
        self.assertEqual(empty.score, 0.0)
        self.assertIn("未报今日任务", empty.gaps)

    def test_estimate_hours_caps_per_chat(self):
        from app.duzhan_ledger import estimate_hours, hours_text, parse_wa_hour_chats

        chats = parse_wa_hour_chats(
            {
                "rows": [
                    {
                        "row_type": "detail",
                        "outbound_message_count": 40,
                        "inbound_message_count": 40,
                        "last_message_time": "2026-09-16 15:00:00",
                    }
                ]
            }
        )
        est = estimate_hours(chats, {"mto_count": 0, "collections": [], "vemory": [], "vemory_ok": True})
        self.assertEqual(est["minutes"], 25.0)
        self.assertEqual(est["band"], "证据不足")
        self.assertEqual(est["parts"]["gap"], 455.0)

    def test_estimate_hours_near_8h_from_evidence(self):
        from app.duzhan_ledger import estimate_hours, hours_text

        chats = [{"outbound": 10, "inbound": 10, "last": "10:00"}] * 12
        person = {
            "mto_count": 4,
            "mto_quotes": [{"raw_ok": True}] * 4,
            "collections": [{"title": "a"}] * 8,
            "vemory": [
                {"name": "m1", "duration_minutes": 60},
                {"name": "m2", "duration_minutes": 60},
            ],
            "vemory_ok": True,
        }
        est = estimate_hours(chats, person)
        self.assertGreaterEqual(est["hours"], 6.0)
        self.assertLessEqual(est["hours"], 8.0)
        self.assertEqual(est["band"], "近满勤")
        text = hours_text(
            {
                "hours_minutes": est["minutes"],
                "hours_band": est["band"],
                "hours_window": est["window"],
                "hours_parts": est["parts"],
            }
        )
        self.assertIn("标准8h", text)
        self.assertIn("缺口", text)

    def test_mto_hours_cap_half_hour(self):
        from app.duzhan_ledger import estimate_hours

        est = estimate_hours(
            [],
            {
                "mto_count": 8,
                "mto_quotes": [{"raw_ok": True}] * 8,
                "collections": [],
                "vemory": [],
                "vemory_ok": True,
            },
        )
        self.assertEqual(est["parts"]["mto"], 30.0)

    def test_vemory_uses_recorded_duration(self):
        from app.duzhan_ledger import estimate_hours, meeting_minutes

        self.assertEqual(
            meeting_minutes(
                {
                    "start_time": "2026-09-16 10:00:00",
                    "end_time": "2026-09-16 10:45:00",
                }
            ),
            45.0,
        )
        est = estimate_hours(
            [],
            {
                "mto_count": 0,
                "collections": [],
                "vemory": [{"name": "call", "duration_minutes": 45}],
                "vemory_ok": True,
            },
        )
        self.assertEqual(est["parts"]["meeting"], 45.0)
        est_zero = estimate_hours(
            [],
            {
                "mto_count": 0,
                "collections": [],
                "vemory": [{"name": "no-clock"}],
                "vemory_ok": True,
            },
        )
        self.assertEqual(est_zero["parts"]["meeting"], 0.0)
        self.assertEqual(meeting_minutes({"duration_seconds": 2700}), 45.0)
        self.assertEqual(
            meeting_minutes({"start_time": 1789521917265, "end_time": 1789523717265}),
            30.0,
        )
        self.assertEqual(meeting_minutes({"duration_minutes": 450}), 7.5)

    def test_records_to_vemory_rows_uses_participant_owner(self):
        from app.duzhan_ledger import records_to_vemory_rows

        rows = records_to_vemory_rows(
            [
                {
                    "title": "回款会",
                    "external_id": "m1",
                    "duration_minutes": 61,
                    "participants_json": '[{"name": "张三"}]',
                }
            ]
        )
        self.assertEqual(rows[0]["owner"], "张三")
        self.assertEqual(rows[0]["duration_minutes"], 61.0)
        skipped = records_to_vemory_rows(
            [
                {
                    "title": "办公室秘书2的快速会议",
                    "external_id": "vps:eeeeeee1-eeee-4eee-8eee-eeeeeeeeeee1",
                    "duration_minutes": 450,
                    "participants_json": '[{"name": "张三"}]',
                }
            ]
        )
        self.assertEqual(skipped, [])

    def test_estimate_hours_does_not_fill_gap_with_idle_window(self):
        from app.duzhan_ledger import estimate_hours

        est = estimate_hours(
            [{"outbound": 1, "inbound": 0, "last": "09:00"}],
            {"mto_count": 0, "collections": [], "vemory": [], "vemory_ok": True},
        )
        self.assertLess(est["hours"], 1)
        self.assertGreater(est["parts"]["gap"], 400)

    def test_vps_hours_from_weekly_turns(self):
        from app.duzhan_ledger import estimate_hours, vps_minutes, workdays_this_week

        self.assertEqual(workdays_this_week("2026-09-16"), 3)
        self.assertEqual(workdays_this_week("2026-09-14"), 1)
        self.assertEqual(vps_minutes({"vps_turns": 15, "vps_weekdays": 3}), 30.0)
        self.assertEqual(vps_minutes({"vps_turns": 100, "vps_weekdays": 1}), 150.0)
        est = estimate_hours(
            [],
            {
                "mto_count": 0,
                "collections": [],
                "vemory": [],
                "vemory_ok": True,
                "vps_turns": 15,
                "vps_weekdays": 3,
            },
        )
        self.assertEqual(est["parts"]["vps"], 30.0)

    def test_render_fills_ledger_and_evening_board(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 15, 20, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        ledger = {
            "today_target": "部门月度战役",
            "people": [
                {
                    "group": "张三业绩达标群",
                    "display": "张三",
                    "target_wan": 200,
                    "mtd_wan": 170.2,
                    "wa_reached": 0,
                    "wa_lower_bound": True,
                    "intent_count": 0,
                    "mto_count": 8,
                    "mto_names": ["share-image.webp", "share-image2.webp"],
                    "hours_minutes": 396.0,
                    "hours_band": "近满勤",
                    "hours_window": "09:01–15:40",
                    "hours_parts": {
                        "wa": 180,
                        "mto": 80,
                        "collect": 90,
                        "meeting": 46,
                        "vps": 0,
                        "gap": 84,
                        "std": 480,
                    },
                    "vps_im_sent": 56,
                    "vps_agent_calls": 103,
                    "vps_turns": 2,
                    "vemory_ok": True,
                    "vemory": [
                        {
                            "name": "迪拜 Billionaire 0915",
                            "link": "https://audio/m1.wav",
                        }
                    ],
                    "collections": [
                        {
                            "title": "柬埔寨支付订单 38246$ XSD-DL26091502472",
                            "amount": "$38246",
                            "progress": "待确认",
                        }
                    ],
                    "blockers": ["马来Derict D 已询问未回复"],
                    "evidence": ["XSD-DL26091502472"],
                }
            ],
            "red": [
                {"display": "张三", "mtd_wan": 170.2, "reason": "过程与回款均有数"},
                {"display": "Bella", "mtd_wan": 147.4, "reason": "今日WhatsApp触达0"},
                {"display": "李四", "mtd_wan": 86.5, "reason": "今日明确意向0"},
            ],
            "black": [
                {"display": "新人小组", "reason": "本月已录单0"},
                {"display": "Alice", "reason": "WhatsApp未覆盖"},
            ],
            "penalties": [],
        }
        text = render_brief(group, 20, now, ledger)
        self.assertIn("月度目标：200 万", text)
        self.assertIn("已录单（开单额）：170.2 万", text)
        # 部门口号是部门月目标，只作“战役”展示；今日目标位改为滚动日目标
        self.assertIn("战役：部门月度战役", text)
        self.assertIn("滚动日目标：", text)
        self.assertNotIn("今日目标：部门月度战役", text)
        self.assertIn("已交8/4（达标）：share-image.webp、share-image2.webp", text)
        self.assertIn("VPS 留痕：IM发送56条 | Agent轮数2（本周累计，工时按日均×6分钟）", text)
        self.assertIn("WhatsApp 沟通户数：0（已同步下限） 户", text)
        self.assertIn("工时（对照标准8h）：6.6h / 标准8h｜近满勤｜WA 3.0h + MTO 1.33h + 催收跟进 1.5h + 会议 0.77h + VPS 0.0h｜缺口 1.4h；WA窗口 09:01–15:40（跨度≠工时）", text)
        self.assertIn("产生明确意向：0 户", text)
        self.assertIn("Vemory 会议录音：1场 迪拜 Billionaire 0915 https://audio/m1.wav", text)
        self.assertIn("柬埔寨支付订单", text)
        self.assertIn("XSD-DL26091502472", text)
        # 红榜双口径：综合 = 过程 50% + 业绩 50%（业绩缺口径时写“业绩待确认”）
        self.assertIn("红榜 TOP3（部门口径·全员可见｜综合=过程50%+业绩50%）：", text)
        self.assertIn("@张三", text)
        self.assertIn("业绩待确认", text)
        self.assertIn("黑榜 待改进（部门口径·全员可见）：@新人小组 本月已录单0 / @Alice WhatsApp未覆盖", text)
        self.assertIn("扣罚台账：今日无扣罚记录", text)
        self.assertNotIn("红榜", render_brief(group, 10, now, ledger))

    def test_parse_mto_skips_bot_and_revoked(self):
        from app.duzhan_ledger import DUZHAN_BOT_ID, parse_mto_images

        count, names = parse_mto_images(
            [
                {
                    "message_type": "image",
                    "sender_bot_id": DUZHAN_BOT_ID,
                    "attachments": [{"attachment_type": "image", "name": "bot.png"}],
                },
                {
                    "message_type": "image",
                    "revoked_at": "2026-09-15T03:00:00Z",
                    "attachments": [{"attachment_type": "image", "name": "gone.png"}],
                },
                {
                    "message_type": "image",
                    "sender_bot_id": None,
                    "attachments": [{"attachment_type": "image", "name": "0915 Agent Q.webp"}],
                },
            ]
        )
        self.assertEqual(count, 1)
        self.assertEqual(names, ["0915 Agent Q.webp"])

    def test_parse_agent_im_current_week(self):
        from app.duzhan_ledger import _vps_for_owner, parse_agent_im_activity
        from app.duzhan_ledger import OWNERS

        html = (
            '<script id="payload" type="application/json">'
            '{"memberRows":['
            '{"period":"current_week","name":"张三","department":"经销商一部",'
            '"directSent":19,"groupSent":37,"openCodeCalls":101,"harnessCalls":0,"standardTurns":2,'
            '"firstAt":"2026-09-14T02:00:09.172Z","lastAt":"2026-09-15T09:19:34.181Z"},'
            '{"period":"complete_week","name":"张三","department":"经销商一部",'
            '"directSent":79,"groupSent":219,"openCodeCalls":324,"harnessCalls":0,"standardTurns":0},'
            '{"period":"current_week","name":"成员C","department":"海外渠道中台",'
            '"directSent":20,"groupSent":8,"openCodeCalls":0,"harnessCalls":0,"standardTurns":0},'
            '{"period":"current_week","name":"Sofia Ben Hamed","department":"经销商三部",'
            '"directSent":11,"groupSent":0,"openCodeCalls":0,"harnessCalls":0,"standardTurns":0}'
            "]}</script>"
        )
        rows = parse_agent_im_activity(html, "current_week")
        self.assertEqual(rows["张三"]["im_sent"], 56)
        self.assertEqual(rows["张三"]["agent_calls"], 103)
        self.assertEqual(rows["张三"]["turns"], 103)
        yu = next(item for item in OWNERS if item.display == "张三")
        wang = next(item for item in OWNERS if item.display == "成员C")
        sofia = next(item for item in OWNERS if item.display == "Sofia")
        self.assertEqual(_vps_for_owner(yu, rows)[:2], (56, 103))
        self.assertEqual(_vps_for_owner(yu, rows)[4], 103)
        self.assertFalse(_vps_for_owner(yu, rows)[5])
        self.assertEqual(_vps_for_owner(wang, rows)[:2], (28, 0))
        self.assertEqual(_vps_for_owner(sofia, rows)[:2], (11, 0))

    def test_parse_agent_im_daily_members(self):
        from app.duzhan_ledger import parse_agent_im_activity, vps_minutes

        html = (
            '<script id="payload" type="application/json">'
            '{"members":['
            '{"name":"成员C","directSent":7,"groupSent":2,"standardTurns":22,'
            '"openCode":0,"harness":0},'
            '{"name":"张三","directSent":13,"groupSent":14,"standardTurns":0,'
            '"openCode":32,"harness":0}'
            "]}</script>"
        )
        rows = parse_agent_im_activity(html)
        self.assertTrue(rows["成员C"]["daily"])
        self.assertEqual(rows["成员C"]["turns"], 22)
        self.assertEqual(rows["张三"]["im_sent"], 27)
        self.assertEqual(rows["张三"]["turns"], 32)
        self.assertEqual(vps_minutes({"vps_turns": 22, "vps_weekdays": 1}), 132.0)

    def test_match_vemory_by_owner_aliases(self):
        from app.duzhan_ledger import OWNERS, match_vemory

        rows = [
            {
                "name": "迪拜代理",
                "owner": "张三",
                "link": "https://audio/m1.wav",
                "id": "m1",
            },
            {
                "name": "科威特跟进",
                "owner": "王五",
                "link": "https://audio/m2.wav",
                "id": "m2",
            },
            {
                "name": "新部早会",
                "owner": "成员C",
                "link": "",
                "id": "m3",
            },
        ]
        yu = next(item for item in OWNERS if item.display == "张三")
        Bella = next(item for item in OWNERS if item.display == "Bella")
        wang = next(item for item in OWNERS if item.display == "成员C")
        alice = next(item for item in OWNERS if item.display == "Alice")
        hits, ok = match_vemory(yu, rows)
        self.assertTrue(ok)
        self.assertEqual([item["name"] for item in hits], ["迪拜代理"])
        self.assertEqual(match_vemory(Bella, rows)[0][0]["name"], "科威特跟进")
        self.assertEqual(match_vemory(wang, rows)[0][0]["name"], "新部早会")
        self.assertEqual(match_vemory(alice, rows)[0], [])
        self.assertFalse(match_vemory(yu, None)[1])

    def test_alice_alias_includes_chinese_name(self):
        """老板 2026-09-18 确认：Alice = Alice，会议/日报按别名要能对上。"""
        from app.duzhan_ledger import OWNERS, match_daily_report, vemory_aliases

        alice = [item for item in OWNERS if item.display == "Alice"][0]
        self.assertIn("Alice", vemory_aliases(alice))
        matched = match_daily_report(alice, {"Alice": {"item_count": 3, "spent_hours": 8}})
        self.assertEqual(matched.get("item_count"), 3)

    def test_xinren_owners_exclude_zhangqian(self):
        """老板 2026-09-18 拍板：加上成员E（Nora）；吴楠、群主甲、管理员戊不加。"""
        from app.duzhan_ledger import OWNERS

        xin = [item.display for item in OWNERS if item.group == "新人小组业绩达标群"]
        self.assertEqual(xin, ["成员A", "Sofia", "成员C", "成员D", "成员E"])
        member_e = [item for item in OWNERS if item.display == "成员E"][0]
        self.assertEqual(member_e.employee_id, 988)
        self.assertEqual(member_e.im_user_id, 914549)
        self.assertIsNone(member_e.target_wan, "新人 100 万是小组目标，不摊到个人")
        self.assertEqual(
            member_e.follow_channel_id,
            "aaaaaaa5-aaaa-4aaa-8aaa-aaaaaaaaaaa5",
            "成员E（Nora）要带上 Nora客户跟进群",
        )
        for name in ("吴楠", "群主甲", "管理员戊"):
            self.assertNotIn(name, xin, name + " 按老板口径不纳入")
        by_target = {item.display: item.target_wan for item in OWNERS}
        self.assertEqual(by_target["张三"], 200)
        self.assertEqual(by_target["李四"], 333)
        self.assertEqual(by_target["赵六"], 95)
        self.assertEqual(by_target["Bella"], 100)
        self.assertEqual(by_target["Alice"], 400)
        self.assertIsNone(by_target["成员A"])
        self.assertNotIn("管理员戊", xin)
        self.assertNotIn("李浩然", xin)
        self.assertNotIn("邢哲夫", xin)
        self.assertNotIn("陈鹏飞", xin)
        follow = {item.display: item.follow_channel_id for item in OWNERS}
        self.assertEqual(follow["成员A"], "aaaaaaa1-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
        self.assertEqual(follow["Sofia"], "aaaaaaa2-aaaa-4aaa-8aaa-aaaaaaaaaaa2")
        self.assertEqual(follow["成员C"], "aaaaaaa3-aaaa-4aaa-8aaa-aaaaaaaaaaa3")
        self.assertEqual(follow["成员D"], "aaaaaaa4-aaaa-4aaa-8aaa-aaaaaaaaaaa4")
        ids = {item.display: item.im_user_id for item in OWNERS}
        self.assertEqual(ids["成员A"], 914247)
        self.assertEqual(ids["成员D"], 914660)

    def test_parse_follow_group_outreach_evidence(self):
        from app.duzhan_ledger import parse_owner_reports

        collections, blockers, evidence = parse_owner_reports(
            [
                {
                    "message_type": "text",
                    "sender_user_id": 914247,
                    "created_at": "2026-09-16T11:08:17Z",
                    "body": "客户名单地区德国，今日触达15人，添加WhatsApp7人，邮箱触达8人其中5封退信，有效触达共计10人，暂无回复",
                },
                {
                    "message_type": "text",
                    "sender_user_id": 914247,
                    "created_at": "2026-09-16T11:57:42Z",
                    "body": "【金山文档 | WPS云文档】 客户跟进台账 https://www.kdocs.cn/l/cct0uNIdy2iO",
                },
                {
                    "message_type": "text",
                    "sender_user_id": 914460,
                    "created_at": "2026-09-16T10:01:41Z",
                    "body": "Contacted 15 customers from the provided list. 7 emails were returned/undelivered.",
                },
                {
                    "message_type": "text",
                    "sender_user_id": 914344,
                    "created_at": "2026-09-16T12:23:27Z",
                    "body": "已填写",
                },
                {
                    "message_type": "link",
                    "sender_user_id": 914660,
                    "created_at": "2026-09-16T10:58:43Z",
                    "body": "https://my.feishu.cn/wiki/TOU3wNUjOiZtzzkmD9ZceBafnPh?table=tblvH9RLYFbLpDiR",
                },
            ],
            914247,
        )
        self.assertTrue(any("今日触达15人" in item["title"] for item in collections))
        self.assertTrue(any("暂无回复" in item or "退信" in item for item in blockers))
        self.assertTrue(any("kdocs.cn" in item for item in evidence))
        sofia, _, sofia_ev = parse_owner_reports(
            [
                {
                    "message_type": "text",
                    "sender_user_id": 914460,
                    "created_at": "2026-09-16T10:01:41Z",
                    "body": "Contacted 15 customers from the provided list. 7 emails were returned/undelivered.",
                }
            ],
            914460,
        )
        self.assertTrue(any("Contacted 15" in item["title"] for item in sofia))
        self.assertTrue(any("Contacted 15" in item for item in sofia_ev))
        _, _, wang_ev = parse_owner_reports(
            [
                {
                    "message_type": "text",
                    "sender_user_id": 914344,
                    "created_at": "2026-09-16T12:23:27Z",
                    "body": "已填写",
                }
            ],
            914344,
        )
        self.assertIn("跟进表已填写", wang_ev)
        _, _, yue_ev = parse_owner_reports(
            [
                {
                    "message_type": "link",
                    "sender_user_id": 914660,
                    "created_at": "2026-09-16T10:58:43Z",
                    "body": "https://my.feishu.cn/wiki/TOU3wNUjOiZtzzkmD9ZceBafnPh?table=tblvH9RLYFbLpDiR",
                }
            ],
            914660,
        )
        self.assertTrue(any("feishu.cn" in item for item in yue_ev))

    def test_messages_on_day_uses_group_timezone(self):
        from app.duzhan_ledger import messages_on_day

        messages = [
            {"created_at": "2026-09-15T15:59:59Z", "id": "old"},
            {"created_at": "2026-09-15T16:00:00Z", "id": "today"},
        ]
        rows = messages_on_day(messages, "2026-09-16", "Asia/Shanghai")
        self.assertEqual([item["id"] for item in rows], ["today"])

    def test_parse_daily_report_card(self):
        from app.duzhan_ledger import parse_daily_reports

        rows = parse_daily_reports(
            [
                {
                    "created_at": "2026-09-16T12:41:19Z",
                    "metadata": {
                        "kind": "daily_report_submission",
                        "work_date": "2026-09-16",
                        "submitter_name": "王五",
                        "today": [
                            {"progress": 100, "spent_hours": 1.5},
                            {"progress": 50, "spent_hours": 3},
                        ],
                    },
                }
            ],
            "2026-09-16",
        )
        self.assertEqual(rows["王五"]["item_count"], 2)
        self.assertEqual(rows["王五"]["done_count"], 1)
        self.assertEqual(rows["王五"]["spent_hours"], 4.5)

    def test_parse_owner_reports_from_group_text(self):
        from app.duzhan_ledger import parse_owner_reports

        collections, blockers, evidence = parse_owner_reports(
            [
                {
                    "message_type": "text",
                    "sender_user_id": 913063,
                    "created_at": "2026-09-15T01:22:09Z",
                    "body": "1.柬埔寨新订单30000$ 2.越南9620$补款",
                },
                {
                    "message_type": "text",
                    "sender_user_id": 913063,
                    "created_at": "2026-09-15T11:48:14Z",
                    "body": (
                        "1. 柬埔寨支付订单  38246$  XSD-DL26091502472\n"
                        "2. 越南激活 11台手机，催收尾款\n"
                        "3.马来Derict D 已询问未回复"
                    ),
                }
            ],
            913063,
        )
        titles = [item["title"] for item in collections]
        self.assertTrue(any("9620" in title for title in titles))
        paid = next(item for item in collections if "38246" in (item.get("amount") or "") or "38246" in item["title"])
        self.assertEqual(paid["amount"], "$38246")
        self.assertIn("XSD-DL26091502472", evidence)
        self.assertTrue(any("未回复" in item for item in blockers))
        stalled = next(item for item in collections if "马来" in item["title"])
        self.assertEqual(stalled["status"], "stalled")
        self.assertIn("XSD-DL26091502472", paid.get("evidence") or evidence)

    def test_parse_water_slip_and_loose_plan(self):
        from app.duzhan_ledger import parse_owner_reports

        collections, _, evidence = parse_owner_reports(
            [
                {
                    "message_type": "text",
                    "sender_user_id": 913122,
                    "created_at": "2026-09-15T14:31:13Z",
                    "body": "俄罗斯索契：10台Alpha、10台Meta 2，水单已回传43万RMB。",
                },
                {
                    "message_type": "text",
                    "sender_user_id": 914113,
                    "created_at": "2026-09-15T14:55:53Z",
                    "body": (
                        "明日工作安排\n"
                        "继续寻找印度人托运的运输方式，协助Velocity成单；\n"
                        "CEO of ULTAVO 二轮会议讨论合作事宜"
                    ),
                },
            ],
            913122,
        )
        sochi = next(item for item in collections if "索契" in item["title"])
        self.assertEqual(sochi["status"], "done")
        self.assertIn("水单", "".join(evidence) + sochi["title"])
        haiwen, _, _ = parse_owner_reports(
            [
                {
                    "message_type": "text",
                    "sender_user_id": 914113,
                    "created_at": "2026-09-15T14:55:53Z",
                    "body": (
                        "明日工作安排\n"
                        "继续寻找印度人托运的运输方式，协助Velocity成单；\n"
                        "CEO of ULTAVO 二轮会议讨论合作事宜"
                    ),
                }
            ],
            914113,
        )
        self.assertTrue(any("Velocity" in item["title"] for item in haiwen))
        self.assertTrue(any("ULTAVO" in item["title"] for item in haiwen))

    def test_run_duzhan_passes_idempotency_key(self):
        empty = {"day": "2026-09-15", "people": [], "red": [], "black": []}
        with patch("app.duzhan.push_duzhan_message", return_value=True) as push, patch(
            "app.duzhan.collect_ledger", return_value=empty
        ):
            run_duzhan(
                TZ_PARIS,
                10,
                datetime(2026, 9, 15, 10, 0, tzinfo=ZoneInfo(TZ_PARIS)),
            )
        key = push.call_args.kwargs["idempotency_key"]
        self.assertTrue(key.startswith("duzhan-20260915-1000-"))
        self.assertIn("55555555", key)


class DuzhanCompactSlotTests(unittest.TestCase):
    """2026-09-19 起三档只出总结性内容（明细走每天 08:00 的证据 HTML）。"""

    def setUp(self):
        from app.config import get_settings

        settings = get_settings()
        self._compact_backup = settings.duzhan_compact
        settings.duzhan_compact = True
        self.addCleanup(self._restore_compact)

    def _restore_compact(self):
        from app.config import get_settings

        get_settings().duzhan_compact = self._compact_backup

    def _person(self, **over: object) -> dict:
        row = {
            "group": "张三业绩达标群",
            "display": "张三",
            "target_wan": 200,
            "mtd_wan": 170.2,
            "daily_target_wan": 6.67,
            "rolling_target_wan": 123.4,
            "target_gap_wan": 46.8,
            "target_ahead": True,
            "perf_arrived_wan": 170.2,
            "perf_slip": [{"amount_text": "USD 45,022", "wan": 32.0, "snippet": "水单已回传"}],
            "perf_intent": [{"amount_text": "120万", "wan": 120.0, "snippet": "明确意向"}],
            "wa_reached": 15,
            "wa_lower_bound": False,
            "intent_count": 2,
            "hours_minutes": 396.0,
            "hours_band": "近满勤",
            "mto_count": 4,
            "mto_names": ["2.webp"],
            "collections": [
                {"title": "迪拜 Billionaire 订单确认", "status": "progress"},
                {"title": "马来 Derict D 回复", "status": "stalled"},
            ],
            "blockers": [],
            "evidence": ["$38246"],
        }
        row.update(over)
        return row

    def _ledger(self, person: dict) -> dict:
        return {"day": "2026-09-19", "today_target": "部门月度战役", "people": [person], "red": [], "black": []}

    def test_compact_is_default_and_short(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 19, 10, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        text = render_brief(group, 10, now, self._ledger(self._person()))
        self.assertIn("今日目标：日目标 6.67 万/天，累计应达 123.4 万", text)
        self.assertIn("请回：今日 3–5 项（对象 / 交付物 / 截止时间）", text)
        # 明细不该出现在档位里
        self.assertNotIn("业绩三关键词", text)
        self.assertNotIn("VPS 留痕", text)
        self.assertNotIn("Vemory", text)
        self.assertLessEqual(len(text.splitlines()), 12)

    def test_compact_midday_evening_lines(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now15 = datetime(2026, 9, 19, 15, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        prev = self._person(perf_arrived_wan=150.2)
        text15 = render_brief(group, 15, now15, self._ledger(self._person()), self._ledger(prev))
        self.assertIn("本档新增：+20.0 万", text15)
        self.assertIn("请回：相对 10:00 的变化", text15)
        now20 = datetime(2026, 9, 19, 20, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        text20 = render_brief(group, 20, now20, self._ledger(self._person()), self._ledger(prev))
        self.assertIn("已录单 170.2 万｜水单 USD 45,022｜意向 120万", text20)
        self.assertIn("WhatsApp 15 户（明确意向 2 户）", text20)
        self.assertIn("明日第一动作：迪拜 Billionaire 订单确认", text20)
        self.assertNotIn("附件证据", text20)

    def test_compact_keeps_amount_ping(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 19, 15, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        person = self._person(perf_intent=[{"amount_text": "", "wan": None, "snippet": "意向金额待定"}])
        text = render_brief(group, 15, now, self._ledger(person), self._ledger(self._person()))
        self.assertIn("补一句：水单/意向有 1 条没写金额", text)

    def test_long_format_still_available(self):
        from app.config import get_settings

        settings = get_settings()
        backup = getattr(settings, "duzhan_compact", True)
        settings.duzhan_compact = False
        try:
            group = groups_for_tz(TZ_SHANGHAI)[1]
            now = datetime(2026, 9, 19, 10, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
            text = render_brief(group, 10, now, self._ledger(self._person()))
            self.assertIn("业绩三关键词", text)
        finally:
            settings.duzhan_compact = backup


class DuzhanSlotStructureTests(LongFormatMixin, unittest.TestCase):
    """A 档位结构增量：10:00 定目标 / 15:00 上午总结 / 20:00 兑现核对。"""

    def _person(self, **over: object) -> dict:
        row = {
            "group": "张三业绩达标群",
            "display": "张三",
            "target_wan": 200,
            "mtd_wan": 170.2,
            "daily_target_wan": 6.67,
            "rolling_target_wan": 120.1,
            "days_elapsed": 18,
            "days_in_month": 30,
            "target_gap_wan": 50.1,
            "target_ahead": True,
            "perf_arrived_wan": 170.2,
            "perf_slip": [{"amount_text": "USD 45,022", "wan": 32.0, "snippet": "水单已回传"}],
            "perf_intent": [{"amount_text": "120万", "wan": 120.0, "snippet": "明确意向"}],
            "wa_reached": 15,
            "wa_lower_bound": False,
            "intent_count": 2,
            "hours_minutes": 396.0,
            "hours_band": "近满勤",
            "mto_count": 4,
            "mto_names": ["2.webp"],
            "collections": [
                {"title": "越南尾款 38246$ XSD", "status": "done"},
                {"title": "迪拜 Billionaire 订单确认", "status": "progress"},
                {"title": "马来 Derict D 回复", "status": "stalled"},
            ],
            "blockers": [],
            "evidence": ["$38246"],
        }
        row.update(over)
        return row

    def _ledger(self, person: dict, day: str = "2026-09-18") -> dict:
        return {
            "day": day,
            "today_target": "部门月度战役",
            "people": [person],
            "red": [],
            "black": [],
        }

    def test_morning_slot_locks_target_and_carries_yesterday(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 18, 10, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        prev = self._person(
            perf_arrived_wan=140.0,
            collections=[
                {"title": "越南尾款 38246$ XSD", "status": "progress"},
                {"title": "迪拜 Billionaire 订单确认", "status": "planned"},
            ],
        )
        text = render_brief(group, 10, now, self._ledger(self._person()), self._ledger(prev))
        self.assertIn("0. 今日目标（本档先定）", text)
        self.assertIn("日目标 6.67 万/天，累计应达 120.1 万", text)
        self.assertIn("领先 50.1 万", text)
        self.assertIn("昨日未闭环结转（今日第一动作）", text)
        self.assertIn("越南尾款 38246$ XSD", text)

    def test_morning_slot_without_target_says_pending(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 18, 10, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        person = self._person(
            target_wan=None,
            daily_target_wan=None,
            rolling_target_wan=None,
            target_gap_wan=None,
        )
        text = render_brief(group, 10, now, self._ledger(person))
        self.assertIn("0. 今日目标（本档先定）：待确认（缺月度目标）", text)

    def test_morning_slot_uses_group_level_target_for_newcomers(self):
        group = groups_for_tz(TZ_SHANGHAI)[0]
        now = datetime(2026, 9, 18, 10, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        person = self._person(
            group="新人小组业绩达标群",
            display="成员A",
            target_wan=None,
            daily_target_wan=None,
            rolling_target_wan=None,
            target_gap_wan=None,
            group_target_wan=100.0,
            group_target_name="新部",
        )
        text = render_brief(group, 10, now, self._ledger(person))
        self.assertIn("0. 今日目标（本档先定）：小组口径 新部 100 万/月", text)
        self.assertNotIn("缺月度目标", text)

    def test_morning_pings_missing_amounts_from_prev_slot(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 18, 10, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        prev = self._person(
            perf_slip=[{"amount_text": "", "wan": None, "snippet": "水单已回传，金额在邮件里"}],
            perf_intent=[],
        )
        person = self._person(perf_slip=[], perf_intent=[])
        text = render_brief(group, 10, now, self._ledger(person), self._ledger(prev))
        self.assertIn("补一句：水单/意向有 1 条没写金额", text)
        self.assertIn("按「客户 / 金额+币种 / 预计到账日 / 品类」补一句", text)

    def test_midday_pings_missing_amounts_from_current_slot(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 18, 15, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        person = self._person(
            perf_intent=[{"amount_text": "", "wan": None, "snippet": "客户有明确意向，金额待定"}],
        )
        text = render_brief(group, 15, now, self._ledger(person), self._ledger(self._person()))
        self.assertIn("补一句：水单/意向有 1 条没写金额", text)

    def test_no_ping_when_amounts_are_clear(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 18, 15, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        text = render_brief(group, 15, now, self._ledger(self._person()), self._ledger(self._person()))
        self.assertNotIn("补一句", text)

    def test_evening_slot_has_no_amount_ping(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 18, 20, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        person = self._person(perf_intent=[{"amount_text": "", "wan": None, "snippet": "意向待定"}])
        text = render_brief(group, 20, now, self._ledger(person), self._ledger(person))
        self.assertNotIn("补一句", text)

    def test_midday_slot_reports_delta_not_month_over_day(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 18, 15, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        prev = self._person(perf_arrived_wan=150.2)
        text = render_brief(group, 15, now, self._ledger(self._person()), self._ledger(prev))
        self.assertIn("【目标梳理｜上午总结】", text)
        self.assertIn("本次新增 +20.0 万", text)
        self.assertIn("累计已录单 170.2 万｜累计应达 120.1 万（领先 50.1 万）｜今日日目标 6.67 万", text)
        self.assertIn("上午工作：6.6h / 标准8h（近满勤）", text)
        # 月累计 ÷ 日目标 的荒唐完成率不允许再出现
        self.assertNotIn("完成率", text)

    def test_midday_slot_without_prev_says_pending(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 18, 15, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        text = render_brief(group, 15, now, self._ledger(self._person()), None)
        self.assertIn("本次新增 待确认（缺上一档口径）", text)

    def test_evening_slot_covers_perf_whatsapp_hours_and_tomorrow(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 18, 20, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        prev = self._person(
            collections=[
                {"title": "越南尾款 38246$ XSD", "status": "progress"},
                {"title": "迪拜 Billionaire 订单确认", "status": "progress"},
            ]
        )
        text = render_brief(group, 20, now, self._ledger(self._person()), self._ledger(prev))
        self.assertIn("【当天总结｜业绩核对】", text)
        self.assertIn("已录单", text)
        self.assertIn("水单", text)
        self.assertIn("意向", text)
        self.assertIn("WhatsApp：15 户｜明确意向 2 户", text)
        self.assertIn("当日工时：", text)
        self.assertIn("【明日预告】", text)
        self.assertIn("迪拜 Billionaire 订单确认", text)

    def test_meeting_todos_block_is_appended_once(self):
        """回归：第 8 节早会待办只允许拼一次（工作区曾出现重复的一行）。"""
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 18, 10, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        person = self._person(meeting_todos="\n【第 8 节｜早会待办】\n   • 事项A")
        text = render_brief(group, 10, now, self._ledger(person), self._ledger(person))
        self.assertEqual(text.count("【第 8 节｜早会待办】"), 1)

    def test_evening_slot_shows_mto_scoring(self):
        """MTO 打分放在 20:00 验兑现（2026-09-23 老板要求：10:00 那档早上没图必然 0）。"""
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 18, 20, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        person = self._person(
            mto_quotes=[
                {"model": "Sample Watch X1", "wan": 63.3, "qualifies": True},
                {"model": "Demo Ring S2", "wan": 20.2, "qualifies": False},
                {"model": "", "wan": None, "qualifies": False},
            ]
        )
        text = render_brief(group, 20, now, self._ledger(person), self._ledger(person))
        self.assertIn("• MTO 今日达标：1/3 款达标（Sample Watch X1 63.3万）", text)
        self.assertIn("1 款未满 30 万（Demo Ring S2 20.2万）", text)
        self.assertIn("1 张读不出金额", text)

    def test_evening_slot_mto_pending_when_no_data(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 18, 20, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        person = self._person(mto_count=None, mto_quotes=None)
        text = render_brief(group, 20, now, self._ledger(person), self._ledger(person))
        self.assertIn("• MTO 今日达标：待确认", text)

    def test_morning_slot_keeps_summary_without_wrapup_line(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 18, 10, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        text = render_brief(
            group, 10, now, self._ledger(self._person()), self._ledger(self._person())
        )
        self.assertIn("• 摘要：", text)
        self.assertNotIn("• MTO 今日达标：", text)

    def test_evening_slot_gives_first_action_when_nothing_open(self):
        group = groups_for_tz(TZ_SHANGHAI)[1]
        now = datetime(2026, 9, 18, 20, 0, tzinfo=ZoneInfo(TZ_SHANGHAI))
        person = self._person(collections=[{"title": "已办完", "status": "done"}])
        text = render_brief(group, 20, now, self._ledger(person), self._ledger(person))
        self.assertIn("无未完成事项，按日目标继续推进", text)

    def test_alice_slot_sections_are_english(self):
        group = groups_for_tz(TZ_PARIS)[0]
        now = datetime(2026, 9, 18, 20, 0, tzinfo=ZoneInfo(TZ_PARIS))
        person = self._person(group="Alice业绩达标群", display="Alice")
        text = render_brief(group, 20, now, self._ledger(person), self._ledger(person))
        self.assertIn("【Day wrap-up | Performance check】", text)
        self.assertIn("WhatsApp:", text)
        self.assertIn("Hours today:", text)
        self.assertIn("【Tomorrow】First action:", text)
        self.assertNotIn("【当天总结｜业绩核对】", text)


if __name__ == "__main__":
    unittest.main()
