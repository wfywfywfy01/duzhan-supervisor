# -*- coding: utf-8 -*-
"""AI 规则块：生成「本档动作」、缓存、限额、失败回落。"""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.duzhan import GROUPS, render_brief
from app.duzhan_admin import ai_rules, runtime
from app.duzhan_admin.service import dump_blocks, parse_blocks
from app.duzhan_ledger import empty_ledger
from app.models.duzhan_agent import DuzhanAgent

GROUP = GROUPS[0]
DAY = "2026-09-30"
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
AI_TEXT = "十点先补昨天的三条未闭环，再报今天每人三件要交付的事"


def blocks_with(*, mode: str = "ai", prompt: str = "盯住未闭环和今日交付物", extra=None):
    items = [
        {"type": "group", "channel_id": GROUP.channel_id, "label": GROUP.name},
        {"type": "times", "slots": ["10:00", "15:00", "20:00"]},
        {"type": "rule", "mode": mode, "text": "固定文案：核验交付物", "prompt": prompt},
    ]
    if extra:
        items.append(extra)
    return {"blocks": items}


class CleanTests(unittest.TestCase):
    def test_clean_strips_prefix_and_noise(self):
        self.assertEqual(ai_rules._clean("  本档动作：请每人报三件事。 "), "请每人报三件事")
        self.assertEqual(ai_rules._clean('"Focus: 盯住未闭环"'), "盯住未闭环")

    def test_clean_rejects_garbage(self):
        self.assertEqual(ai_rules._clean(""), "")
        self.assertEqual(ai_rules._clean("好"), "")
        self.assertEqual(ai_rules._clean("本档动作："), "")

    def test_clean_caps_length(self):
        self.assertLessEqual(len(ai_rules._clean("长" * 300)), ai_rules.MAX_CHARS)

    def test_ai_rule_of(self):
        text_agent = DuzhanAgent(name="固定文案官", blocks_json=dump_blocks(parse_blocks(blocks_with(mode="text"))))
        self.assertIsNone(ai_rules.ai_rule_of(text_agent))
        ai_agent = DuzhanAgent(name="AI 官", blocks_json=dump_blocks(parse_blocks(blocks_with())))
        self.assertIsNotNone(ai_rules.ai_rule_of(ai_agent))
        empty_prompt = DuzhanAgent(name="空提示词", blocks_json=dump_blocks(parse_blocks(blocks_with(prompt=" "))))
        self.assertIsNone(ai_rules.ai_rule_of(empty_prompt))


class FocusForTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="duzhan-ai-"))
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        self.calls = []

        def fake_generate(messages):
            self.calls.append(messages)
            return AI_TEXT

        for item in (
            patch("app.database.get_engine", return_value=engine),
            patch("app.duzhan_admin.runtime.using_db", return_value=True),
            patch.object(ai_rules, "_cache_dir", return_value=self.tmp),
            patch.object(ai_rules, "_generate", side_effect=fake_generate),
        ):
            item.start()
            self.addCleanup(item.stop)
        self.engine = engine

    def add_agent(self, name="AI 督战官", blocks=None, enabled=True):
        with Session(self.engine) as session:
            row = DuzhanAgent(name=name, enabled=enabled,
                              blocks_json=dump_blocks(parse_blocks(blocks or blocks_with())))
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def test_generates_and_caches(self):
        agent = self.add_agent()
        first = ai_rules.focus_for(GROUP, 10, DAY, empty_ledger(DAY))
        self.assertEqual(first, AI_TEXT)
        self.assertEqual(len(self.calls), 1)
        second = ai_rules.focus_for(GROUP, 10, DAY, empty_ledger(DAY))
        self.assertEqual(second, AI_TEXT)
        self.assertEqual(len(self.calls), 1, "同一天同一档不该重复调模型")
        self.assertTrue(ai_rules._cache_path(agent.id, DAY, 10).is_file())

    def test_code_source_never_calls_model(self):
        self.add_agent()
        with patch("app.duzhan_admin.runtime.using_db", return_value=False):
            self.assertIsNone(ai_rules.focus_for(GROUP, 10, DAY, empty_ledger(DAY)))
        self.assertEqual(self.calls, [])

    def test_no_ai_rule_returns_none(self):
        self.add_agent(blocks=blocks_with(mode="text"))
        self.assertIsNone(ai_rules.focus_for(GROUP, 10, DAY, empty_ledger(DAY)))
        self.assertEqual(self.calls, [])

    def test_model_failure_falls_back(self):
        self.add_agent()
        with patch.object(ai_rules, "_generate", side_effect=RuntimeError("model down")):
            self.assertIsNone(ai_rules.focus_for(GROUP, 10, DAY, empty_ledger(DAY)))

    def test_daily_limit_stops_calling(self):
        self.add_agent(name="AI 一号")
        with patch.dict("os.environ", {"PDCA_DUZHAN_AI_DAILY_LIMIT": "1"}):
            self.assertEqual(ai_rules.focus_for(GROUP, 10, DAY, empty_ledger(DAY)), AI_TEXT)
            self.assertIsNone(ai_rules.focus_for(GROUP, 15, DAY, empty_ledger(DAY)))
        self.assertEqual(len(self.calls), 1)

    def test_prompt_carries_user_words(self):
        self.add_agent()
        ai_rules.focus_for(GROUP, 10, DAY, empty_ledger(DAY))
        text = self.calls[0][-1]["content"]
        self.assertIn("盯住未闭环和今日交付物", text)
        self.assertIn(DAY, text)


class GenerateRetryTests(unittest.TestCase):
    """推理模型可能把预算全花在 reasoning 上：content 为空要重试一次（4 倍预算）。"""

    def test_retries_with_bigger_budget(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def chat(self, messages, *, max_tokens, temperature):
                self.calls.append(max_tokens)
                # 真实客户端返回的是归一化后的 {content, usage}
                if len(self.calls) == 1:
                    return {"content": "", "usage": {"reasoning_tokens": 1200}}
                return {"content": "先补三条未闭环", "usage": {}}

        fake = FakeClient()
        with patch("app.agents.llm_client.supervisor_client", return_value=fake):
            text = ai_rules._generate([{"role": "user", "content": "x"}])
        self.assertEqual(text, "先补三条未闭环")
        self.assertEqual(fake.calls, [ai_rules.DEFAULT_MAX_TOKENS, ai_rules.DEFAULT_MAX_TOKENS * 4])

    def test_returns_empty_when_still_empty(self):
        class FakeClient:
            def chat(self, messages, *, max_tokens, temperature):
                return {"content": "", "usage": {"reasoning_tokens": 4800}}

        with patch("app.agents.llm_client.supervisor_client", return_value=FakeClient()):
            self.assertEqual(ai_rules._generate([{"role": "user", "content": "x"}]), "")

    def test_budget_covers_reasoning_models(self):
        self.assertGreaterEqual(ai_rules.DEFAULT_MAX_TOKENS, 1000)

    def test_accepts_openai_shaped_payload_too(self):
        class FakeClient:
            def chat(self, messages, *, max_tokens, temperature):
                return {"choices": [{"message": {"content": "标准形状也能取到"}}]}

        with patch("app.agents.llm_client.supervisor_client", return_value=FakeClient()):
            self.assertEqual(ai_rules._generate([{"role": "user", "content": "x"}]), "标准形状也能取到")


class RenderOverrideTests(unittest.TestCase):
    def test_override_replaces_only_the_action_line(self):
        ledger = empty_ledger(DAY)
        default = render_brief(GROUP, 10, NOW, ledger, None)
        custom = render_brief(GROUP, 10, NOW, ledger, None, focus_override="十点先补三条未闭环")
        self.assertIn("本档动作：十点先补三条未闭环", custom)
        self.assertNotIn("本档动作：十点先补三条未闭环", default)
        default_lines = [line for line in default.splitlines() if not line.startswith("- 本档动作：")]
        custom_lines = [line for line in custom.splitlines() if not line.startswith("- 本档动作：")]
        self.assertEqual(custom_lines, default_lines, "除了本档动作那一行，其余必须一字不动")

    def test_empty_override_keeps_default(self):
        ledger = empty_ledger(DAY)
        self.assertEqual(
            render_brief(GROUP, 10, NOW, ledger, None, focus_override=None),
            render_brief(GROUP, 10, NOW, ledger, None),
        )


if __name__ == "__main__":
    unittest.main()
