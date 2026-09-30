# -*- coding: utf-8 -*-
"""小黑屋开单晒单：识别、档位、文案、去重（都不碰网络）。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import heiwu

#: 群里真实出现过的几种晒单写法
REAL_MESSAGES = [
    ("成交：META2浅金款橙色小牛皮 一台，13800", 13800),
    ("成交：META2浅金款巴黎钉黑牛 一台，13800", 13800),
    ("成交AQ胡桃木一台        AQ超跑二台        AQ橙鳄一台 合计136000", 136000),
    ("成交QT橙色马头绗线一台 3.8w", 38000),
    ("已开单", None),
]

NOISE_MESSAGES = [
    "9/28-9/30作业 一、有效客户跟进情况 客户：王老板 意向产品：AQ 蓝色鳄鱼皮 当前进展：进入价格谈判环节，卡在价格。",
    "9.22-9.24作业 VERTU ALPHAFOLD大折叠｜屏幕耐用性 & 售后维修说明 首先，正常爱惜使用不容易坏 铰链经过65万次开合耐久测试",
    "22-24作业：Vertu Signature手机在偏远地区或无网络区域如何保持通讯？",
    "9/28-9/30的学习打卡任务，请大家按时完成并提交到群内 @所有人",
    "意向客户为商务人士，到店体验VERTU ALPHAFOLD大折叠，预估开单时间下周",
]


class ParseTests(unittest.TestCase):
    def test_real_deal_messages(self):
        for text, expected in REAL_MESSAGES:
            with self.subTest(text=text[:20]):
                parsed = heiwu.parse_deal(text)
                self.assertTrue(parsed["is_deal"])
                self.assertEqual(parsed["amount"], expected)

    def test_noise_messages_are_not_deals(self):
        for text in NOISE_MESSAGES:
            with self.subTest(text=text[:20]):
                self.assertFalse(heiwu.parse_deal(text)["is_deal"])

    def test_empty_body(self):
        self.assertFalse(heiwu.parse_deal("")["is_deal"])

    def test_small_numbers_are_not_amounts(self):
        parsed = heiwu.parse_deal("成交小折叠 2 台 送 3 个表带")
        self.assertTrue(parsed["is_deal"])
        self.assertIsNone(parsed["amount"])
        self.assertTrue(parsed["declared_only"])


class RewardTests(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(heiwu.reward_for(13800), "18.8 元")
        self.assertEqual(heiwu.reward_for(50000), "38.8 元")
        self.assertEqual(heiwu.reward_for(136000), "68.8 元")
        self.assertIsNone(heiwu.reward_for(8000))
        self.assertIsNone(heiwu.reward_for(None))

    def test_money_text(self):
        self.assertEqual(heiwu.money_text(13800), "13,800 元")
        self.assertEqual(heiwu.money_text(136000), "136,000 元")
        self.assertEqual(heiwu.money_text(None), "金额待确认")


class ReplyTests(unittest.TestCase):
    def test_qualified_gets_exit_hint(self):
        text = heiwu.reply_text("门店成员丁", heiwu.parse_deal("成交AQ胡桃木一台 合计136000"))
        self.assertIn("恭喜门店成员丁", text)
        self.assertIn("136,000 元", text)
        self.assertIn("可以退出小黑屋", text)
        self.assertIn("68.8", text)

    def test_one_wan_also_qualifies(self):
        text = heiwu.reply_text("小高", heiwu.parse_deal("成交：META2 一台，13800"))
        self.assertIn("可以退出小黑屋", text)
        self.assertIn("18.8", text)

    def test_below_threshold_gets_encouragement(self):
        text = heiwu.reply_text("小高", heiwu.parse_deal("成交：配件一套，8800"))
        self.assertIn("18.8", text)
        self.assertNotIn("可以退出小黑屋", text)

    def test_unknown_amount_asks_for_it(self):
        text = heiwu.reply_text("小高", heiwu.parse_deal("已开单"))
        self.assertIn("金额待确认", text)


class PollTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="heiwu-"))
        for item in (
            patch.object(heiwu, "_data_dir", return_value=self.tmp),
            patch.object(heiwu, "_reply", return_value=True),
        ):
            item.start()
            self.addCleanup(item.stop)

    def _messages(self):
        return [
            {"id": "m1", "created_at": "2026-09-30T02:00:00Z", "sender_type": "user", "sender_user_id": 1,
             "body": "成交：META2浅金款橙色小牛皮 一台，13800"},
            {"id": "m2", "created_at": "2026-09-30T02:01:00Z", "sender_type": "user", "sender_user_id": 2,
             "body": "9/28-9/30作业 客户：王老板 预估开单时间"},
            {"id": "m3", "created_at": "2026-09-30T02:02:00Z", "sender_type": "bot", "sender_bot_id": "b1",
             "body": "成交机器人自己的消息不该被回"},
        ]

    def test_credentials_fall_back_to_existing_bot(self):
        base_env = {"PDCA_HEIWU_BOT_APP_ID": "", "PDCA_HEIWU_BOT_APP_SECRET": "",
                    "PDCA_DUZHAN_BOT_APP_ID": "vbot_duzhan", "PDCA_DUZHAN_BOT_APP_SECRET": "ds"}
        with patch.dict("os.environ", base_env, clear=False):
            self.assertEqual(heiwu.bot_credentials_effective(), ("vbot_duzhan", "ds"))
            self.assertTrue(heiwu.configured())
        base_env.update({"PDCA_HEIWU_BOT_APP_ID": "vbot_hei", "PDCA_HEIWU_BOT_APP_SECRET": "hs"})
        with patch.dict("os.environ", base_env, clear=False):
            self.assertEqual(heiwu.bot_credentials_effective(), ("vbot_hei", "hs"), "专用凭证优先")

    def _seed_cursor(self, last: str = "2026-09-30T01:00:00Z") -> None:
        """已有游标 = 不是首轮（首轮只记游标不回历史）。"""
        (self.tmp / "heiwu_cursor.json").write_text(
            json.dumps({"last_created_at": last, "replied_ids": []}), encoding="utf-8"
        )

    def test_first_run_only_seeds_cursor(self):
        with patch.object(heiwu, "bot_credentials_effective", return_value=("id", "secret")), \
             patch.object(heiwu, "fetch_recent", return_value=self._messages()), \
             patch.object(heiwu, "_reply", return_value=True) as reply:
            result = heiwu.poll_once()
        self.assertTrue(result.get("seeded"))
        self.assertEqual(result["replied"], [])
        self.assertEqual(reply.call_count, 0, "首轮不该回历史晒单")
        cursor = json.loads((self.tmp / "heiwu_cursor.json").read_text(encoding="utf-8"))
        self.assertEqual(cursor["last_created_at"], "2026-09-30T02:02:00Z")

    def test_no_credentials_skips(self):
        with patch.object(heiwu, "bot_credentials_effective", return_value=("", "")):
            self.assertEqual(heiwu.poll_once(), {"skipped": "not_configured"})

    def test_replies_once_per_message(self):
        self._seed_cursor()
        with patch.object(heiwu, "bot_credentials_effective", return_value=("id", "secret")), \
             patch.object(heiwu, "fetch_recent", return_value=self._messages()), \
             patch.object(heiwu, "refresh_names", return_value={"1": "门店成员丁"}), \
             patch.object(heiwu, "_reply", return_value=True) as reply:
            first = heiwu.poll_once()
            self.assertEqual(first["replied"], ["m1"])
            self.assertEqual(reply.call_count, 1)
            self.assertIn("门店成员丁", reply.call_args[0][0])
            second = heiwu.poll_once()
            self.assertEqual(second["replied"], [])
            self.assertEqual(reply.call_count, 1, "同一条晒单不该回两次")

    def test_bare_declaration_reuses_previous_amount(self):
        self._seed_cursor()
        messages = [
            {"id": "a1", "created_at": "2026-09-30T02:00:00Z", "sender_type": "user", "sender_user_id": 7,
             "body": "成交：META2浅金款 一台，13800"},
            {"id": "a2", "created_at": "2026-09-30T02:05:00Z", "sender_type": "user", "sender_user_id": 7,
             "body": "已开单"},
        ]
        with patch.object(heiwu, "bot_credentials_effective", return_value=("id", "secret")), \
             patch.object(heiwu, "fetch_recent", return_value=messages), \
             patch.object(heiwu, "refresh_names", return_value={}), \
             patch.object(heiwu, "_reply", return_value=True) as reply:
            result = heiwu.poll_once()
        self.assertEqual(result["replied"], ["a1", "a2"])
        second_body = reply.call_args_list[1][0][0]
        self.assertIn("13,800 元", second_body)
        self.assertIn("按你上一条报的金额", second_body)

    def test_stale_amount_is_not_reused(self):
        self._seed_cursor()
        messages = [
            {"id": "b1", "created_at": "2026-09-30T02:00:00Z", "sender_type": "user", "sender_user_id": 7,
             "body": "成交：META2浅金款 一台，13800"},
            {"id": "b2", "created_at": "2026-09-30T06:00:00Z", "sender_type": "user", "sender_user_id": 7,
             "body": "已开单"},
        ]
        with patch.object(heiwu, "bot_credentials_effective", return_value=("id", "secret")), \
             patch.object(heiwu, "fetch_recent", return_value=messages), \
             patch.object(heiwu, "refresh_names", return_value={}), \
             patch.object(heiwu, "_reply", return_value=True) as reply:
            heiwu.poll_once()
        self.assertIn("金额待确认", reply.call_args_list[1][0][0])

    def test_dry_run_does_not_send_or_move_cursor(self):
        self._seed_cursor()
        with patch.object(heiwu, "bot_credentials_effective", return_value=("id", "secret")), \
             patch.object(heiwu, "fetch_recent", return_value=self._messages()), \
             patch.object(heiwu, "refresh_names", return_value={}), \
             patch.object(heiwu, "_reply", return_value=True) as reply:
            result = heiwu.poll_once(dry_run=True)
            self.assertEqual(len(result["replied"]), 1)
            self.assertEqual(reply.call_count, 0)
            cursor = json.loads((self.tmp / "heiwu_cursor.json").read_text(encoding="utf-8"))
            self.assertEqual(cursor["last_created_at"], "2026-09-30T01:00:00Z", "dry-run 不该推进游标")


if __name__ == "__main__":
    unittest.main()
