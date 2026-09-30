# -*- coding: utf-8 -*-
"""脱敏示例策略：分类、噪声、到期、主题覆盖、渲染、发送与 08:00 调度。不联网。"""
from __future__ import annotations

import json
import re
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.strategy_wa_brief import (
    OwnerScan,
    Strategy,
    TZ,
    build_html,
    build_im_body,
    classify,
    strategies_for,
    topic_verdict,
    window_for,
    window_text,
)

BUNDLED = Path(__file__).resolve().parents[1] / "app" / "wa_strategies.json"
#: 脱敏示例策略的 id 与顺序（= app/wa_strategies.json）
SAMPLE_IDS = ["sample-watch-x1", "sample-bespoke-rebate", "demo-ring-s2"]
#: 允许出现在测试里的人名（脱敏白名单）
MASKED_NAMES = {
    "张三", "李四", "王五", "赵六", "Alice", "Bella", "Sofia",
    "成员A", "成员B", "成员C", "成员D", "成员E",
}
#: 旧真实策略/品牌词，脱敏文件里一个都不许留
LEGACY_TOKENS = [
    "agentq", "agent q", "smartjewel", "metawatch", "mto", "clear",
    "vertu", "apple", "crystal ring", "ai ring", "meta1",
]
#: 群/会话一律用占位
GROUP_ID = "11111111-1111-4111-8111-111111111111"
FOLLOW_ID = "22222222-2222-4222-8222-222222222222"


def bundled_raw() -> dict:
    """读包内策略文件（反斜杠转义写错会在这里直接炸）。"""
    return json.loads(BUNDLED.read_text(encoding="utf-8"))


class SampleStrategyFileTests(unittest.TestCase):
    """app/wa_strategies.json 必须是纯虚构示例，且 schema 不变。"""

    def test_file_is_utf8_without_bom_and_strict_json(self):
        raw_bytes = BUNDLED.read_bytes()
        self.assertFalse(raw_bytes.startswith(b"\xef\xbb\xbf"), "不能带 UTF-8 BOM")
        raw = json.loads(raw_bytes.decode("utf-8"))
        self.assertIsInstance(raw.get("title"), str)
        self.assertTrue(raw["title"].strip())

    def test_schema_matches_loader_contract(self):
        raw = bundled_raw()
        self.assertEqual(sorted(raw), ["noise", "strategies", "title"])
        for pattern in raw["noise"]:
            self.assertIsInstance(pattern, str)
            re.compile(pattern)
        for item in raw["strategies"]:
            self.assertEqual(
                sorted(item),
                sorted(set(item) & {"id", "label", "until", "skip_if_noise", "strong", "weak", "themes"}),
                "出现 schema 之外的字段",
            )
            for key in ("id", "label"):
                self.assertIsInstance(item[key], str)
                self.assertTrue(item[key].strip())
            for key in ("strong", "weak"):
                self.assertIsInstance(item[key], list)
                self.assertTrue(item[key], f"{item['id']} 的 {key} 不能为空")
                for pattern in item[key]:
                    re.compile(pattern)
            if "until" in item:
                datetime.strptime(item["until"], "%Y-%m-%d")
            if "skip_if_noise" in item:
                self.assertIsInstance(item["skip_if_noise"], bool)
            for theme in item.get("themes") or []:
                self.assertEqual(sorted(theme), ["id", "label", "patterns"])
                for pattern in theme["patterns"]:
                    re.compile(pattern)

    def test_only_sample_strategies_remain(self):
        self.assertEqual([item["id"] for item in bundled_raw()["strategies"]], SAMPLE_IDS)

    def test_no_real_brand_or_product_words(self):
        text = BUNDLED.read_text(encoding="utf-8").lower()
        for token in LEGACY_TOKENS:
            self.assertNotIn(token, text, f"脱敏文件里不该出现 {token!r}")

    def test_every_strategy_is_marked_as_sample(self):
        for item in bundled_raw()["strategies"]:
            self.assertTrue(item["id"].startswith(("sample-", "demo-")), f"{item['id']} 不是示例命名")
            self.assertIn("示例", item["label"])

    def test_until_dates_keep_live_and_expired_slots(self):
        raw = {item["id"]: item for item in bundled_raw()["strategies"]}
        self.assertEqual(raw["sample-watch-x1"]["until"], "2026-12-31")
        self.assertEqual(raw["demo-ring-s2"]["until"], "2026-11-30")
        self.assertNotIn("until", raw["sample-bespoke-rebate"], "没写 until 的策略一直查")

    def test_bundled_samples_carry_noise_skip_and_themes(self):
        raw = bundled_raw()
        self.assertTrue(raw["noise"], "噪声词表不能为空")
        rebate = next(item for item in raw["strategies"] if item["id"] == "sample-bespoke-rebate")
        self.assertTrue(rebate["skip_if_noise"])
        self.assertEqual([t["id"] for t in rebate["themes"]], ["quota", "deadline"])
        ring = next(item for item in raw["strategies"] if item["id"] == "demo-ring-s2")
        self.assertEqual(
            [t["id"] for t in ring["themes"]],
            ["price", "gift", "gen3", "deadline", "stable"],
        )

class ClassifyTests(unittest.TestCase):
    """强命中 / 弱命中 / 不命中，以及噪声词（noise + skip_if_noise）。"""

    def test_strong_hit_allocation_price_is_strong(self):
        labels = dict(
            classify(
                "Sample Watch X1: allocation price USD 1,234.56 per set; "
                "deposit to lock your allocation."
            )
        )
        self.assertEqual(labels["sample-watch-x1"], "strong")

    def test_strong_hit_worldwide_quota_is_strong(self):
        labels = dict(classify("Sample Watch X1 全球限量 50 套，仅限海外经销商，定金锁配额"))
        self.assertEqual(labels["sample-watch-x1"], "strong")

    def test_mention_only_is_weak(self):
        labels = dict(classify("Sample Watch X1 到货了，有兴趣的看看"))
        self.assertEqual(labels["sample-watch-x1"], "weak")

    def test_unrelated_text_not_matched(self):
        self.assertEqual(dict(classify("本季度例会纪要已上传，请查收")), {})

    def test_demo_ring_discount_is_strong(self):
        labels = dict(classify("Demo Ring S2 拿货 5 折，九月三十日截止"))
        self.assertEqual(labels["demo-ring-s2"], "strong")

    def test_demo_ring_order_gift_is_strong(self):
        labels = dict(classify("Demo Ring S2：一次提 30 台赠 3 台，可提前锁定第三代 5 台认购权"))
        self.assertEqual(labels["demo-ring-s2"], "strong")

    def test_english_policy_is_strong(self):
        """英文版政策必须能判强命中（不靠中文关键词）。"""
        labels = dict(
            classify(
                "DEMO RING S2: 50% of the retail price (50% off); buy 30 units, get 3 free; "
                "order by September 25."
            )
        )
        self.assertEqual(labels["demo-ring-s2"], "strong")

    def test_demo_ring_mention_only_is_weak(self):
        labels = dict(classify("Demo Ring S2 到货了，有兴趣的看看"))
        self.assertEqual(labels["demo-ring-s2"], "weak")

    def test_bespoke_rebate_is_strong(self):
        labels = dict(classify("BESPOKE SAMPLE ORDER +5% rebate within 72 hours"))
        self.assertEqual(labels["sample-bespoke-rebate"], "strong")

    def test_noise_word_skips_skip_if_noise_strategy(self):
        """noise + skip_if_noise：带维修/售后噪声词的消息不判这条策略。"""
        labels = dict(classify("BESPOKE SAMPLE ORDER +5% rebate within 72 hours，维修可以找他"))
        self.assertNotIn("sample-bespoke-rebate", labels)
        self.assertEqual(labels, {}, "噪声词命中时不该留下任何这条策略的判分")

    def test_noise_word_does_not_block_other_strategies(self):
        """噪声词只影响带 skip_if_noise 的策略。"""
        labels = dict(classify("Sample Watch X1 全球限量 50 套，维修找售后，定金锁配额"))
        self.assertEqual(labels["sample-watch-x1"], "strong")

    def test_removed_legacy_strategies_are_gone(self):
        """旧真实策略（agentq / mto / smartjewel 等）已删除，不再判分。"""
        from app.strategy_wa_brief import _strategy_by_id

        for legacy in ("agentq", "mto", "smartjewel", "watch", "clear"):
            self.assertIsNone(_strategy_by_id(legacy), f"{legacy} 应已从策略文件删除")
        self.assertEqual(dict(classify("本单满30万可加提腕表资格")), {})
        self.assertEqual(
            dict(classify("pay 30%, get full- price goods, 70% rest payment by three months.")),
            {},
        )


class ThemeTests(unittest.TestCase):
    """themes 主题覆盖率：覆盖 >= 2 点算「有」，只讲 1 点算「部分」。"""

    THEME_TEXT = (
        "Demo Ring S2: 50% of the retail price (50% off); buy 30 units, get 3 free. "
        "Your order secures advance purchase rights for 5 units of Gen 3. "
        "Website prices remain unchanged. Order by September 25."
    )

    def test_theme_coverage_hits_all_five(self):
        from app.strategy_wa_brief import _strategy_by_id, theme_hits

        hits = theme_hits(_strategy_by_id("demo-ring-s2"), self.THEME_TEXT)
        self.assertEqual(sorted(hits), ["deadline", "gen3", "gift", "price", "stable"])

    def test_strategy_without_themes_has_no_theme_hits(self):
        from app.strategy_wa_brief import _strategy_by_id, theme_hits

        self.assertEqual(
            theme_hits(_strategy_by_id("sample-watch-x1"), "配额价 定金锁配额"), []
        )

    def test_semantic_verdict_needs_two_points(self):
        scan = OwnerScan(display="成员A", employee_id=1, message_count=10)
        scan.theme_ids["demo-ring-s2"] = {"price"}
        self.assertEqual(topic_verdict(scan, "demo-ring-s2"), "部分")
        scan.theme_ids["demo-ring-s2"] = {"price", "deadline"}
        self.assertEqual(topic_verdict(scan, "demo-ring-s2"), "有")

    def test_theme_note_counts_coverage(self):
        from app.strategy_wa_brief import theme_note

        scan = OwnerScan(display="成员A", employee_id=1, message_count=3)
        scan.theme_ids["sample-bespoke-rebate"] = {"quota"}
        self.assertEqual(theme_note(scan, "sample-bespoke-rebate"), "1/2 点")
        scan.theme_ids["sample-bespoke-rebate"] = {"quota", "deadline"}
        self.assertEqual(theme_note(scan, "sample-bespoke-rebate"), "2/2 点")
        self.assertEqual(theme_note(scan, "sample-watch-x1"), "", "没配主题的策略不写要点")

class StrategyExpiryTests(unittest.TestCase):
    """until 到期后不再出现在当期；没写 until 的策略一直查。"""

    def _with(self, items):
        return mock.patch(
            "app.strategy_wa_brief.load_strategies", return_value=(items, [])
        )

    def test_until_filters_expired_only(self):
        items = [
            Strategy(id="sample-watch-x1", label="示例首发", strong=[], weak=[], until="2026-12-31"),
            Strategy(id="sample-bespoke-rebate", label="示例返点", strong=[], weak=[]),
        ]
        with self._with(items):
            self.assertEqual(
                [item.id for item in strategies_for("2026-12-30")],
                ["sample-watch-x1", "sample-bespoke-rebate"],
            )
            self.assertEqual(
                [item.id for item in strategies_for("2026-12-31")],
                ["sample-watch-x1", "sample-bespoke-rebate"],
                "until 当天仍算当期",
            )
            self.assertEqual(
                [item.id for item in strategies_for("2027-01-01")],
                ["sample-bespoke-rebate"],
                "过期策略当期不再出现，没写 until 的一直查",
            )

    def test_all_expired_produces_no_active_strategies(self):
        items = [Strategy(id="sample-old", label="旧示例", strong=[], weak=[], until="2026-01-01")]
        with self._with(items):
            self.assertEqual(strategies_for("2026-10-01"), [])

    def test_all_expired_stops_report_before_scanning_messages(self):
        from app.strategy_wa_brief import run_report

        items = [Strategy(id="sample-old", label="旧示例", strong=[], weak=[], until="2026-01-01")]
        with self._with(items), mock.patch("app.strategy_wa_brief.scan_owners") as scan:
            with self.assertRaisesRegex(RuntimeError, "没有有效策略"):
                run_report("2026-10-01")
        scan.assert_not_called()

    def test_bundled_samples_expire_by_their_own_until(self):
        self.assertEqual(
            [item.id for item in strategies_for("2026-11-30")], SAMPLE_IDS, "到期当天仍算当期"
        )
        self.assertEqual(
            [item.id for item in strategies_for("2026-12-01")],
            ["sample-watch-x1", "sample-bespoke-rebate"],
            "Demo Ring S2 11/30 到期后当期不再出现",
        )


class WindowTests(unittest.TestCase):
    def test_24h_before_8am(self):
        start, end = window_for("2026-09-19")
        self.assertEqual(start, datetime(2026, 9, 18, 8, 0, tzinfo=TZ))
        self.assertEqual(end, datetime(2026, 9, 19, 8, 0, tzinfo=TZ))
        self.assertIn("09-18 08:00 → 09-19 08:00", window_text(start, end))


class RenderTests(unittest.TestCase):
    """HTML + IM 正文渲染。"""

    def _scans(self):
        from app.strategy_wa_brief import Hit

        scans = [
            OwnerScan("李四", 47, message_count=2),
            OwnerScan("Alice", 171, message_count=0, wa_configured=False, note="触达 0"),
        ]
        scans[0].hits.append(
            Hit(
                "李四",
                "sample-watch-x1",
                "strong",
                "2026-09-18 12:00:00",
                GROUP_ID,
                "群发言",
                "text",
                "Sample Watch X1 全球限量 50 套，定金锁配额",
            )
        )
        scans[0].theme_ids["sample-bespoke-rebate"] = {"quota", "deadline"}
        return scans

    def test_html_and_im_body(self):
        start, end = window_for("2026-09-19")
        scans = self._scans()
        html_text = build_html("2026-09-19", start, end, scans)
        self.assertIn("Sample Watch X1 全球限量 50 套，定金锁配额", html_text)
        self.assertIn("无WA", html_text)
        self.assertIn("2/2 点", html_text)
        body = build_im_body("2026-09-19", start, end, scans)
        self.assertIn("①", body)
        self.assertIn("李四有", body)
        self.assertIn("Alice无WA", body)
        self.assertIn("（2/2 点）", body)
        self.assertEqual(topic_verdict(scans[0], "sample-watch-x1"), "有")
        self.assertEqual(topic_verdict(scans[1], "sample-bespoke-rebate"), "无WA")
        empty_incomplete = OwnerScan("李四", 47, message_count=0, complete=False)
        self.assertEqual(topic_verdict(empty_incomplete, "sample-watch-x1"), "待确认")
        scanned = OwnerScan("Bella", 216, message_count=40, complete=False)
        self.assertEqual(topic_verdict(scanned, "sample-bespoke-rebate"), "无")

    def test_english_policy_body_renders_verbatim_without_chinese(self):
        """英文政策正文：判强命中，HTML 原文照录，正文里不夹中文。"""
        from app.strategy_wa_brief import Hit

        english = (
            "Demo Ring S2: 50% of the retail price (50% off); buy 30 units, get 3 free; "
            "order by September 25."
        )
        self.assertEqual(dict(classify(english))["demo-ring-s2"], "strong")
        self.assertIsNone(re.search(r"[\u4e00-\u9fff]", english), "英文正文不该自带中文")
        scan = OwnerScan("Alice", 171, message_count=1)
        scan.hits.append(
            Hit(
                "Alice",
                "demo-ring-s2",
                "strong",
                "2026-09-18 12:00:00",
                GROUP_ID,
                "群发言",
                "text",
                english,
            )
        )
        start, end = window_for("2026-09-19")
        html_text = build_html("2026-09-19", start, end, [scan])
        last_row = html_text.rsplit("<tbody>", 1)[1]
        self.assertIn(english, last_row)
        quote = last_row.rsplit("<td>", 1)[1].split("</td>", 1)[0]
        self.assertEqual(quote, english, "原文单元格照录英文，不掺中文")

class VpsImTests(unittest.TestCase):
    """VPS IM（达标群 / 跟进群）也要算进策略核查。"""

    def test_html_shows_both_sources(self):
        """报告里要明写数据源与每人分源条数。"""
        start, end = window_for("2026-09-24")
        scan = OwnerScan("成员A", 36, message_count=7, wa_count=2, im_count=5)
        html_text = build_html("2026-09-24", start, end, [scan])
        self.assertIn("VPS IM 全域聊天记录", html_text)
        self.assertIn("（WhatsApp 2 · VPS IM 5）", html_text)

    def test_vps_im_messages_are_scanned(self):
        from app.duzhan_ledger import Owner
        from app.strategy_wa_brief import fetch_owner_im_messages, im_channels

        owner = Owner(
            "示例达标群",
            "成员A",
            im_user_id=914247,
            follow_channel_id=FOLLOW_ID,
        )
        labels = [label for label, _cid in im_channels(owner)]
        self.assertIn("客户跟进群", labels)
        messages = [
            {
                "id": "m1",
                "sender_user_id": 914247,
                "created_at": "2026-09-24T09:30:00Z",
                "body": "Sample Watch X1 全球限量 50 套，定金锁配额",
                "message_type": "text",
            },
            {
                "id": "m2",
                "sender_user_id": 999,
                "created_at": "2026-09-24T09:31:00Z",
                "body": "别人的发言",
                "message_type": "text",
            },
        ]

        def fake_history(channel_id, date_from, limit=100):
            # 目标群与跟进群各返回一次，验证只算本人发言、且两个会话都会扫
            return messages if channel_id == FOLLOW_ID else []

        with mock.patch("app.strategy_wa_brief._im_history", side_effect=fake_history):
            rows = fetch_owner_im_messages(
                owner,
                datetime(2026, 9, 24, 8, tzinfo=TZ),
                datetime(2026, 9, 25, 8, tzinfo=TZ),
            )
        self.assertEqual(len(rows), 1, "只算本人发言")
        self.assertEqual(rows[0]["_platform"], "VPS IM")
        self.assertIn("50 套", rows[0]["content"])

    def test_window_im_records_filter_by_sender_and_window(self):
        """全域聊天记录只留核查对象本人 + 窗口内。"""
        from app.duzhan_ledger import Owner
        from app.strategy_wa_brief import fetch_window_im_messages

        owners = [Owner("示例达标群", "成员A", im_user_id=914247)]
        page = {
            "messages": [
                {
                    "id": "m1",
                    "sender_user_id": 914247,
                    "created_at": "2026-09-24T09:00:00Z",
                    "body": "Sample Watch X1 全球限量 50 套",
                    "message_type": "text",
                    "channel": {"id": GROUP_ID, "name": "示例达标群", "type": "group"},
                },
                {
                    "id": "m2",
                    "sender_user_id": 999,
                    "created_at": "2026-09-24T09:01:00Z",
                    "body": "别人的发言",
                    "message_type": "text",
                    "channel": {"id": GROUP_ID, "name": "示例达标群", "type": "group"},
                },
                {
                    "id": "m3",
                    "sender_user_id": 914247,
                    "created_at": "2026-09-20T09:00:00Z",
                    "body": "窗口外的旧消息",
                    "message_type": "text",
                    "channel": {"id": FOLLOW_ID, "name": "私聊", "type": "direct"},
                },
            ],
            "pagination": {"has_next": False},
        }
        with mock.patch("app.strategy_wa_brief._im_records_page", return_value=page):
            got, complete = fetch_window_im_messages(
                datetime(2026, 9, 24, 8, tzinfo=TZ),
                datetime(2026, 9, 25, 8, tzinfo=TZ),
                owners,
            )
        self.assertEqual(list(got), ["成员A"])
        self.assertEqual(len(got["成员A"]), 1, "只留本人 + 窗口内")
        self.assertEqual(got["成员A"][0]["_platform"], "VPS IM")
        self.assertEqual(got["成员A"][0]["customer_display"], "示例达标群")
        self.assertTrue(complete, "首页有数据 → 视为完整")

    def test_window_im_mid_pagination_failure_marks_incomplete(self):
        """中途某页拿不到 → 标不完整（不静默当作扫完）。"""
        from app.duzhan_ledger import Owner
        from app.strategy_wa_brief import fetch_window_im_messages

        owners = [Owner("示例达标群", "成员A", im_user_id=914247)]
        first = {
            "messages": [
                {
                    "id": "m1",
                    "sender_user_id": 914247,
                    "created_at": "2026-09-24T09:00:00Z",
                    "body": "Sample Watch X1 全球限量 50 套",
                    "message_type": "text",
                    "channel": {"id": GROUP_ID, "name": "示例达标群", "type": "group"},
                }
            ],
            "pagination": {"has_next": True},
        }
        with mock.patch(
            "app.strategy_wa_brief._im_records_page", side_effect=[first, {}, {}]
        ), mock.patch("app.strategy_wa_brief.time.sleep"):
            got, complete = fetch_window_im_messages(
                datetime(2026, 9, 24, 8, tzinfo=TZ),
                datetime(2026, 9, 25, 8, tzinfo=TZ),
                owners,
            )
        self.assertEqual(len(got["成员A"]), 1)
        self.assertFalse(complete, "中途断页必须标不完整")

    def test_targets_cover_new_group_and_use_masked_names(self):
        """核查对象覆盖新人组，且只用脱敏名。"""
        from app.strategy_wa_brief import TARGETS

        self.assertGreaterEqual(len(TARGETS), 5)
        self.assertIn("成员A", TARGETS)
        self.assertGreaterEqual(
            len([name for name in TARGETS if name.startswith("成员")]), 4
        )
        for name in TARGETS:
            self.assertIn(name, MASKED_NAMES, f"{name} 不在脱敏名单里")


class JobRegistrationTests(unittest.TestCase):
    """08:00 策略核查任务：job id 必须是 campaign_wa_check，禁用不注册。"""

    def _register(self, **over: object):
        from app.scheduler import jobs as scheduler_jobs

        class RecordingScheduler:
            def __init__(self):
                self.jobs = []

            def add_job(self, func, *args, **kwargs):
                self.jobs.append((func, args, kwargs))

            def start(self):
                return None

        base = {
            "scheduler_enabled": True,
            "sync_cron": "0 6 * * *",
            "daily_report_enabled": False,
            "todo_remind_enabled": False,
            "todo_remind_times": [],
            "todo_group_notice_enabled": False,
            "todo_group_channel_id": "",
            "todo_scoring_enabled": False,
            "todo_ledger_sync_enabled": False,
            "todo_brief_enabled": False,
            "todo_okr_link_enabled": False,
            "mto_temp_cleanup_enabled": False,
            "daily_digest_enabled": False,
            "evidence_report_enabled": False,
            "strategy_wa_brief_enabled": True,
            "strategy_wa_brief_time": "08:00",
            "campaign_wa_check_enabled": True,
            "campaign_wa_check_time": "08:00",
        }
        base.update(over)
        original = scheduler_jobs._scheduler
        scheduler_jobs._scheduler = None
        try:
            with mock.patch.object(
                scheduler_jobs, "get_settings", return_value=SimpleNamespace(**base)
            ), mock.patch.object(
                scheduler_jobs, "BackgroundScheduler", RecordingScheduler
            ):
                scheduler = scheduler_jobs.start_scheduler()
        finally:
            scheduler_jobs._scheduler = original
        return [kwargs["id"] for _, _, kwargs in scheduler.jobs]

    def test_strategy_check_registers_campaign_wa_check_id(self):
        ids = self._register()
        self.assertIn("campaign_wa_check", ids)
        self.assertNotIn("strategy_wa_brief", ids, "旧 job id 不得再注册")

    def test_campaign_slot_can_be_turned_off(self):
        """开关关掉就不能注册：两个开关都关一定不注册，且旧 id 永远不许出现。"""
        ids = self._register(campaign_wa_check_enabled=False, strategy_wa_brief_enabled=False)
        self.assertNotIn("campaign_wa_check", ids)
        self.assertNotIn("strategy_wa_brief", ids)

    def test_campaign_slot_follows_its_own_switch(self):
        """这条任务的开关是 strategy_wa_brief_enabled（历史名），关掉就必须不注册。"""
        ids = self._register(strategy_wa_brief_enabled=False, campaign_wa_check_enabled=True)
        self.assertNotIn("campaign_wa_check", ids)
        self.assertNotIn("strategy_wa_brief", ids, "旧 job id 不得出现")

    def test_campaign_job_alias_points_at_the_strategy_job(self):
        from app.scheduler import jobs as scheduler_jobs

        self.assertIs(scheduler_jobs.campaign_wa_check_job, scheduler_jobs.strategy_wa_brief_job)

    def test_job_ledger_key_is_campaign_wa_check(self):
        """跑批留痕也必须用 campaign_wa_check，不然日报去重会对不上。"""
        import inspect

        from app.scheduler import jobs as scheduler_jobs

        source = inspect.getsource(scheduler_jobs.strategy_wa_brief_job)
        self.assertIn('claim_run("campaign_wa_check"', source)
        self.assertNotIn('claim_run("strategy_wa_brief"', source)


class RobustnessTests(unittest.TestCase):
    def test_bad_regex_does_not_drop_other_strategies(self):
        import tempfile

        from app import strategy_wa_brief as mod

        mod._loaded = None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wa_strategies.json"
            path.write_text(
                json.dumps(
                    {
                        "strategies": [
                            {"id": "sample-bad", "label": "坏示例", "strong": ["("]},
                            {"id": "sample-ok", "label": "好示例", "strong": ["圣诞"]},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(mod, "strategies_path", return_value=path):
                labels = dict(mod.classify("圣诞备货"))
        mod._loaded = None
        self.assertEqual(labels, {"sample-ok": "strong"})

    def test_one_owner_error_keeps_the_rest(self):
        from app.duzhan_ledger import Owner
        from app.strategy_wa_brief import scan_owners

        owners = [
            Owner("示例达标群", "张三", employee_id=45),
            Owner("示例达标群", "李四", employee_id=47),
        ]

        def fetch(employee_id, start, end):
            if employee_id == 45:
                raise RuntimeError("mcp down")
            return [], True, ""

        with mock.patch("app.strategy_wa_brief.OWNERS", owners), mock.patch(
            "app.strategy_wa_brief.fetch_owner_messages", side_effect=fetch
        ), mock.patch(
            "app.strategy_wa_brief.mcp_call", return_value={"summary": "客户 0"}
        ):
            scans = scan_owners(
                datetime(2026, 9, 18, 8, tzinfo=TZ),
                datetime(2026, 9, 19, 8, tzinfo=TZ),
            )
        self.assertEqual([item.display for item in scans], ["张三", "李四"])
        self.assertIn("采集失败", scans[0].note)
        self.assertEqual(topic_verdict(scans[0], "sample-bespoke-rebate"), "待确认")
        self.assertEqual(topic_verdict(scans[1], "sample-bespoke-rebate"), "无WA")


class SendAttachTests(unittest.TestCase):
    """dry_run 不发；run_report 只出 HTML/正文，发送在调度任务里。"""

    def test_dry_run_skips_send(self):
        from app import strategy_wa_brief as mod

        fake = OwnerScan("张三", 45, message_count=0, complete=True)
        with mock.patch.object(mod, "scan_owners", return_value=[fake]), mock.patch.object(
            mod, "save_html", return_value=Path("x.html")
        ), mock.patch("app.im_files.send_files") as send:
            result = mod.run_brief("2026-09-19", dry_run=True)
        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "dry_run")
        send.assert_not_called()

    def test_run_report_never_sends_by_itself(self):
        from app import strategy_wa_brief as mod

        fake = OwnerScan("成员A", 1, message_count=0, complete=True)
        with mock.patch.object(mod, "scan_owners", return_value=[fake]), mock.patch.object(
            mod, "save_html", return_value=Path("x.html")
        ), mock.patch("app.im_files.send_files") as send:
            result = mod.run_report("2026-09-19")
        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "")
        send.assert_not_called()
        self.assertEqual(
            [item["display"] for item in result["scans"]], ["成员A"]
        )
        for topic in SAMPLE_IDS:
            self.assertIn(topic, result["scans"][0])


if __name__ == "__main__":
    unittest.main()
