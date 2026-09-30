# -*- coding: utf-8 -*-
"""等价性验收（任务 8）：配置跑出来的东西，必须和写死的群逐字一致。

做法：seed_from_code() 把写死的群导成配置 → 用配置翻译出 DuzhanGroup →
和原来的常量逐个字段比、再逐档比渲染出来的消息体。
不一致就说明翻译层漏了东西（那时绝不能切 db 源）。
"""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import duzhan
from app.duzhan import GROUPS, render_brief
from app.duzhan_admin import runtime
from app.duzhan_admin.service import seed_from_code
from app.duzhan_ledger import empty_ledger
from app.models.duzhan_agent import DuzhanAgent

DAY = "2026-09-29"
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


class ParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="duzhan-parity-"))
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        self.engine = engine
        for item in (
            patch("app.database.get_engine", return_value=engine),
            patch("app.duzhan_admin.runtime.using_db", return_value=True),
            patch.object(duzhan, "get_settings", return_value=SimpleNamespace(
                data_dir=self.tmp, duzhan_compact=True, duzhan_config_source="db")),
        ):
            item.start()
            self.addCleanup(item.stop)
        with Session(engine) as session:
            seed_from_code(session)
        # 导入的配置默认停用；这里把对应写死群的那 5 条打开，模拟"切换 db 源"后的状态
        known = {group.channel_id for group in GROUPS}
        with Session(engine) as session:
            rows = session.exec(select(DuzhanAgent)).all()
            for row in rows:
                agent_group = runtime.group_of(row)
                if agent_group is not None and agent_group.channel_id in known:
                    row.enabled = True
                    session.add(row)
            session.commit()
        with Session(engine) as session:
            self.seeded = {row.name: row for row in session.exec(select(DuzhanAgent)).all()}

    def _agent_for(self, group):
        agent = self.seeded.get(f"{group.name}督战官")
        self.assertIsNotNone(agent, f"没有导入 {group.name} 的配置")
        return agent

    def test_group_fields_are_identical(self):
        for group in GROUPS:
            got = runtime.group_of(self._agent_for(group))
            self.assertEqual(
                (got.name, got.channel_id, got.lang, got.tz),
                (group.name, group.channel_id, group.lang, group.tz),
                group.name,
            )

    def test_slots_match_engine_slots(self):
        for group in GROUPS:
            self.assertEqual(runtime.slots_of(self._agent_for(group)), [10, 15, 20], group.name)

    def test_rendered_bodies_are_byte_identical(self):
        ledger = empty_ledger(DAY)
        for group in GROUPS:
            config_group = runtime.group_of(self._agent_for(group))
            for hour in (10, 15, 20):
                code_body = render_brief(group, hour, NOW, ledger, None)
                config_body = render_brief(config_group, hour, NOW, ledger, None)
                self.assertEqual(config_body, code_body, f"{group.name} {hour:02d}:00 文案不一致")

    def test_db_groups_equal_code_groups(self):
        for group in GROUPS:
            self.assertIn(group, duzhan.groups_for_tz(group.tz), group.name)
        self.assertEqual(sorted(duzhan.cron_timezones()), sorted(set(group.tz for group in GROUPS)))


class ChannelFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="duzhan-filter-"))
        self.pushes = []
        for item in (
            patch.object(duzhan, "get_settings", return_value=SimpleNamespace(
                data_dir=self.tmp, duzhan_compact=True, duzhan_config_source="code")),
            patch.object(duzhan, "push_duzhan_message",
                         side_effect=lambda body, channel_id, **kw: self.pushes.append(channel_id) or True),
        ):
            item.start()
            self.addCleanup(item.stop)

    def test_run_duzhan_only_pushes_given_channels(self):
        target = GROUPS[0]
        result = duzhan.run_duzhan("Asia/Shanghai", 10, now=NOW, channel_ids=[target.channel_id])
        self.assertEqual(self.pushes, [target.channel_id])
        self.assertEqual(result["sent"], [target.name])

    def test_run_duzhan_without_filter_pushes_all_groups_of_tz(self):
        duzhan.run_duzhan("Asia/Shanghai", 10, now=NOW)
        self.assertEqual(len(self.pushes), len([g for g in GROUPS if g.tz == "Asia/Shanghai"]))

    def test_slot_key_keeps_code_mode_filename(self):
        self.assertEqual(duzhan._slot_key(None), "")
        self.assertEqual(duzhan._slot_key([GROUPS[0].channel_id]), GROUPS[0].channel_id[:8])


if __name__ == "__main__":
    unittest.main()
