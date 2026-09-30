# -*- coding: utf-8 -*-
"""2026-09-22：C转B 10:00 档「本档动作」与「回复格式」字段必须一致（老板指出原两处不一致）。"""
from __future__ import annotations

import pathlib
import unittest


class CtobMorningWordingTests(unittest.TestCase):
    def test_morning_reply_format_matches_the_ask(self):
        from app import ctob

        ask = ctob._CTOB_SLOT_FOCUS[10]
        reply = ctob._CTOB_REPLY_FORMAT_MORNING
        for token in ("客户名", "品类", "第一动作", "截止"):
            self.assertIn(token, ask, "本档动作缺字段 " + token)
            self.assertIn(token, reply, "回复格式缺字段 " + token)

    def test_afternoon_evening_keep_change_fields(self):
        from app import ctob

        generic = ctob._CTOB_REPLY_FORMAT
        self.assertNotEqual(generic, ctob._CTOB_REPLY_FORMAT_MORNING)
        for token in ("几轮", "进度", "卡点"):
            self.assertIn(token, generic)

    def test_morning_renderer_uses_the_morning_format(self):
        source = pathlib.Path(ctob_path()).read_text(encoding="utf-8")
        marker = '        f"可能要的支持：{support}",\n        _CTOB_REPLY_FORMAT_MORNING,'
        self.assertIn(marker, source, "早档渲染必须用早档专用回复格式")


def ctob_path() -> str:
    from app import ctob

    return ctob.__file__


if __name__ == "__main__":
    unittest.main()
