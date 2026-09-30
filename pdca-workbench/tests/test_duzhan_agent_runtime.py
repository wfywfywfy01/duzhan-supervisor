# -*- coding: utf-8 -*-
"""配置 → 运行时（db 源）：翻译、过滤、台账分档、调度注册。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.duzhan import GROUPS, DuzhanGroup, groups_for_tz
from app.duzhan_admin import runtime
from app.duzhan_admin.service import dump_blocks, parse_blocks
from app.models.duzhan_agent import DuzhanAgent

NEW_GROUP = GROUPS[0]
FOLLOW_GROUP = "ccccccc1-cccc-4ccc-8ccc-ccccccccccc1"  # app.ctob.OWNERS[0]


def _blocks(**over):
    return {
        "blocks": [
            {"type": "group", "channel_id": over.get("channel_id", NEW_GROUP.channel_id),
             "label": over.get("label", NEW_GROUP.name)},
            {"type": "times", "slots": over.get("slots", ["10:00", "15:00", "20:00"])},
            {"type": "style", "lang": over.get("lang", NEW_GROUP.lang)},
        ]
    }


class FakeScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, func, **kwargs):
        self.jobs.append({"func": func.__name__, **kwargs})

    def ids(self):
        return [job["id"] for job in self.jobs]


class RuntimeTranslationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.patch_engine = patch("app.database.get_engine", return_value=self.engine)
        self.patch_engine.start()
        self.addCleanup(self.patch_engine.stop)

    def add(self, name, blocks, *, enabled=True, timezone="Asia/Shanghai"):
        with Session(self.engine) as session:
            row = DuzhanAgent(name=name, enabled=enabled, timezone=timezone,
                              blocks_json=dump_blocks(parse_blocks(blocks)))
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def test_group_of_matches_hardcoded_group(self):
        agent = self.add("新人组官", _blocks())
        group = runtime.group_of(agent)
        self.assertEqual(group.name, NEW_GROUP.name)
        self.assertEqual(group.channel_id, NEW_GROUP.channel_id)
        self.assertEqual(group.lang, NEW_GROUP.lang)
        self.assertEqual(group.tz, NEW_GROUP.tz)

    def test_group_of_uses_agent_name_and_default_tz(self):
        agent = self.add("没名字的群", {"blocks": [
            {"type": "group", "channel_id": NEW_GROUP.channel_id},
            {"type": "times", "slots": ["10:00"]},
        ]}, timezone="")
        group = runtime.group_of(agent)
        self.assertEqual(group.name, "没名字的群")
        self.assertEqual(group.tz, "Asia/Shanghai")
        self.assertEqual(group.lang, "zh")

    def test_slots_only_accept_supported_hours(self):
        agent = self.add("档位官", {"blocks": [
            {"type": "group", "channel_id": NEW_GROUP.channel_id},
            {"type": "times", "slots": ["10:00", "12:00", "20:00"]},
        ]})
        self.assertEqual(runtime.slots_of(agent), [10, 20])

    def test_duzhan_agents_skips_follow_and_invalid(self):
        self.add("跟进群官", _blocks(channel_id=FOLLOW_GROUP, label="跟进群"))
        self.add("坏配置官", {"blocks": [{"type": "times", "slots": ["10:00"]}]})
        self.add("停用官", _blocks(), enabled=False)
        good = self.add("好配置官", _blocks())
        picked = runtime.duzhan_agents()
        self.assertEqual([agent.id for agent, _group in picked], [good.id])

    def test_group_of_returns_none_without_group_block(self):
        agent = self.add("无群官", {"blocks": [{"type": "times", "slots": ["10:00"]}]})
        self.assertIsNone(runtime.group_of(agent))

    def test_ledger_job_name_is_per_agent(self):
        first = self.add("甲官", _blocks())
        second = self.add("乙官", _blocks())
        self.assertNotEqual(runtime.ledger_job_name(first, collect=True),
                            runtime.ledger_job_name(second, collect=True))
        self.assertNotEqual(runtime.ledger_job_name(first, collect=True),
                            runtime.ledger_job_name(first, collect=False))

    def test_config_source_falls_back_to_code(self):
        with patch("app.config.get_settings", return_value=SimpleNamespace(duzhan_config_source="weird")):
            self.assertEqual(runtime.config_source(), "code")
            self.assertFalse(runtime.using_db())
        with patch("app.config.get_settings", return_value=SimpleNamespace(duzhan_config_source="DB")):
            self.assertTrue(runtime.using_db())


class DbGroupsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.stack = [
            patch("app.database.get_engine", return_value=self.engine),
            patch("app.config.get_settings", return_value=SimpleNamespace(
                duzhan_config_source="db", duzhan_times=["10:00", "15:00", "20:00"], duzhan_lead_minutes=15)),
        ]
        for item in self.stack:
            item.start()
            self.addCleanup(item.stop)
        with Session(self.engine) as session:
            session.add(DuzhanAgent(name="巴黎官", enabled=True, timezone="Europe/Paris",
                                    blocks_json=dump_blocks(parse_blocks(_blocks(lang="en", label="巴黎群")))))
            session.add(DuzhanAgent(name="上海官", enabled=True, timezone="Asia/Shanghai",
                                    blocks_json=dump_blocks(parse_blocks(_blocks(label="上海群")))))
            session.commit()

    def test_groups_for_tz_uses_config_when_db(self):
        # 群名以 group 块的 label 为准（配置页上写的是什么就发到哪个名字）
        paris = groups_for_tz("Europe/Paris")
        self.assertEqual([group.name for group in paris], ["巴黎群"])
        self.assertEqual(paris[0].lang, "en")
        shanghai = groups_for_tz("Asia/Shanghai")
        self.assertEqual([group.name for group in shanghai], ["上海群"])

    def test_channel_filter(self):
        self.assertEqual(len(groups_for_tz("Asia/Shanghai", [NEW_GROUP.channel_id])), 1)
        self.assertEqual(groups_for_tz("Asia/Shanghai", ["eeeeeee2-eeee-4eee-8eee-eeeeeeeeeee2"]), [])

    def test_cron_timezones_from_config(self):
        self.assertEqual(runtime.cron_timezones(), ["Europe/Paris", "Asia/Shanghai"])

    def test_register_agent_jobs(self):
        from app.duzhan import cron_timezones as code_timezones

        self.assertEqual(code_timezones(), ["Europe/Paris", "Asia/Shanghai"])
        scheduler = FakeScheduler()
        registered = runtime.register_agent_jobs(scheduler)
        self.assertEqual(len(registered), 2)
        ids = scheduler.ids()
        self.assertIn("duzhan_agent1_10", ids)
        self.assertIn("duzhan_collect_agent1_20", ids)
        self.assertIn("duzhan_backup_agent1_15", ids)
        push = next(job for job in scheduler.jobs if job["id"] == "duzhan_agent1_10")
        self.assertEqual(push["kwargs"]["channel_ids"], [NEW_GROUP.channel_id])
        self.assertTrue(push["kwargs"]["ledger_job"].startswith("duzhan_push:agent:"))

    def test_register_agent_jobs_warns_when_nothing_enabled(self):
        with Session(self.engine) as session:
            for row in session.exec(__import__("sqlmodel").select(DuzhanAgent)).all():
                session.delete(row)
            session.commit()
        scheduler = FakeScheduler()
        self.assertEqual(runtime.register_agent_jobs(scheduler), [])
        self.assertEqual(scheduler.jobs, [])


if __name__ == "__main__":
    unittest.main()
