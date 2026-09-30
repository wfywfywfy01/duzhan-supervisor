# -*- coding: utf-8 -*-
"""长假严格模式：列进 PDCA_DUZHAN_STRICT_HOLIDAY_CHANNELS 的群，长假也照常评价。"""
import os
import unittest
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.duzhan import GROUPS, DuzhanGroup, render_brief, strict_holiday_channels
from app.duzhan_ledger import empty_ledger

HOLIDAY = "2026-10-01"          # 国庆长假第一天
HOLIDAY_NOW = datetime(2026, 10, 1, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
WORKDAY = "2026-09-30"
WORKDAY_NOW = datetime(2026, 9, 30, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


@contextmanager
def env(**values):
    old = {key: os.environ.get(key) for key in values}
    os.environ.update({key: value for key, value in values.items() if value is not None})
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def ledger_for(day: str) -> dict:
    """带红黑榜的台账（长假标记由 collect_ledger 写，这里手工模拟）。"""
    from app.workday_calendar import penalty_exempt

    entry = empty_ledger(day)
    entry["_penalty_exempt"] = penalty_exempt(day)
    entry["red"] = [{"display": GROUPS[0].name, "reason": "示例理由"}]
    entry["black"] = [{"display": "示例成员A", "reason": "未回复"}]
    entry["penalties"] = [{"zh": "示例扣罚", "en": "sample penalty"}]
    return entry


@contextmanager
def compact_off():
    """生产是 PDCA_DUZHAN_COMPACT=0（长版）；测试里固定住，别依赖跑测试的机器环境。"""
    with patch("app.duzhan.get_settings", return_value=SimpleNamespace(duzhan_compact=False)):
        yield


def body_for(group: DuzhanGroup, day: str, now: datetime) -> str:
    with compact_off():
        return render_brief(group, 20, now, ledger_for(day), None)


class StrictHolidayTests(unittest.TestCase):
    def test_env_parsing(self):
        with env(PDCA_DUZHAN_STRICT_HOLIDAY_CHANNELS=None):
            self.assertEqual(strict_holiday_channels(), set())
        with env(PDCA_DUZHAN_STRICT_HOLIDAY_CHANNELS=" a , b ,, "):
            self.assertEqual(strict_holiday_channels(), {"a", "b"})

    def test_long_holiday_is_exempt_by_default(self):
        body = body_for(GROUPS[0], HOLIDAY, HOLIDAY_NOW)
        self.assertIn("长假期间不处罚", body)
        self.assertNotIn("@示例成员A", body)

    def test_strict_channel_keeps_penalty_on_holiday(self):
        with env(PDCA_DUZHAN_STRICT_HOLIDAY_CHANNELS=GROUPS[0].channel_id):
            body = body_for(GROUPS[0], HOLIDAY, HOLIDAY_NOW)
        self.assertNotIn("长假期间不处罚", body)
        self.assertNotIn("长假期间不记扣罚", body)
        self.assertIn("@示例成员A", body)
        self.assertIn("示例扣罚", body)

    def test_strict_only_affects_listed_channel(self):
        with env(PDCA_DUZHAN_STRICT_HOLIDAY_CHANNELS=GROUPS[0].channel_id):
            other = body_for(GROUPS[1], HOLIDAY, HOLIDAY_NOW)
        self.assertIn("长假期间不处罚", other)

    def test_normal_workday_unchanged(self):
        normal = body_for(GROUPS[0], WORKDAY, WORKDAY_NOW)
        with env(PDCA_DUZHAN_STRICT_HOLIDAY_CHANNELS=GROUPS[0].channel_id):
            strict = body_for(GROUPS[0], WORKDAY, WORKDAY_NOW)
        self.assertEqual(normal, strict, "普通工作日不该因为严格模式而变样")

    def test_strict_matches_workday_body_shape(self):
        """长假严格模式出来的红黑榜那两行，与普通工作日完全一致。"""
        workday_lines = [line for line in body_for(GROUPS[0], WORKDAY, WORKDAY_NOW).splitlines()
                         if line.startswith("黑榜") or line.startswith("扣罚台账")]
        with env(PDCA_DUZHAN_STRICT_HOLIDAY_CHANNELS=GROUPS[0].channel_id):
            holiday_lines = [line for line in body_for(GROUPS[0], HOLIDAY, HOLIDAY_NOW).splitlines()
                             if line.startswith("黑榜") or line.startswith("扣罚台账")]  # noqa: E501
        self.assertEqual(holiday_lines, workday_lines)


if __name__ == "__main__":
    unittest.main()
