# -*- coding: utf-8 -*-
"""督战官子 Agent 配置 API：CRUD / 启用闸门 / 结构试跑不发消息 / 从代码导入。"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.adapters.groups import FOLLOW_GROUPS, GROUPS
from app.db import get_session
from app.duzhan_blocks import BLOCK_TYPES
from app.main import app
from app.models.duzhan_agent import DuzhanAgent
from app.security import require_admin

GOOD_BLOCKS = {
    "blocks": [
        {"type": "group", "channel_id": GROUPS[0]["channel_id"], "label": GROUPS[0]["name"]},
        {"type": "times", "slots": ["10:00", "15:00", "20:00"]},
        {"type": "people", "names": ["示例成员B"]},
        {"type": "source", "key": "msg_summary"},
        {"type": "rule", "text": "本档动作：核验交付物与证据", "when": "always"},
        {"type": "condition", "key": "workday", "value": True},
        {"type": "style", "lang": "zh", "title": "每日三追进度表"},
    ]
}

BAD_BLOCKS = {"blocks": [{"type": "group", "channel_id": "c1", "label": "随便一个群"}]}


class DuzhanAgentApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(self.engine)

        def session_dep():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_dep
        app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
            role="admin", username="duzhan-api-test"
        )
        self.client = TestClient(app, base_url="https://testserver", follow_redirects=False)
        self.headers = {"Origin": "https://testserver", "Sec-Fetch-Site": "same-origin"}

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    # --- 基础 CRUD ---

    def test_create_and_list(self):
        response = self.client.post(
            "/api/duzhan-agents",
            headers=self.headers,
            json={"name": "新人组官", "timezone": "Asia/Shanghai", "blocks": GOOD_BLOCKS},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["id"])
        self.assertFalse(body["enabled"])
        self.assertEqual(body["errors"], [])
        self.assertEqual(body["renderer"], "group_brief.renderer")

        listed = self.client.get("/api/duzhan-agents").json()["items"]
        self.assertEqual([item["name"] for item in listed], ["新人组官"])

    def test_duplicate_name_is_conflict(self):
        payload = {"name": "重名官", "blocks": GOOD_BLOCKS}
        self.assertEqual(self.client.post("/api/duzhan-agents", headers=self.headers, json=payload).status_code, 200)
        second = self.client.post("/api/duzhan-agents", headers=self.headers, json=payload)
        self.assertEqual(second.status_code, 409)

    def test_update_and_delete(self):
        agent_id = self.client.post(
            "/api/duzhan-agents", headers=self.headers, json={"name": "改我", "blocks": GOOD_BLOCKS}
        ).json()["id"]
        updated = self.client.put(
            f"/api/duzhan-agents/{agent_id}",
            headers=self.headers,
            json={"name": "改完了", "timezone": "Europe/Paris", "blocks": GOOD_BLOCKS},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["timezone"], "Europe/Paris")
        self.assertEqual(self.client.delete(f"/api/duzhan-agents/{agent_id}", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/api/duzhan-agents").json()["items"], [])
        self.assertEqual(self.client.delete(f"/api/duzhan-agents/{agent_id}", headers=self.headers).status_code, 404)

    # --- 启用闸门 ---

    def test_bad_config_saves_as_draft_but_cannot_enable(self):
        created = self.client.post(
            "/api/duzhan-agents",
            headers=self.headers,
            json={"name": "坏配置官", "enabled": True, "blocks": BAD_BLOCKS},
        )
        self.assertEqual(created.status_code, 400, created.text)
        self.assertIn("channel_id", str(created.json()["detail"]))

        draft = self.client.post(
            "/api/duzhan-agents",
            headers=self.headers,
            json={"name": "坏配置官", "blocks": BAD_BLOCKS},
        )
        self.assertEqual(draft.status_code, 200, draft.text)
        agent_id = draft.json()["id"]
        self.assertTrue(draft.json()["errors"])

        toggled = self.client.post(
            f"/api/duzhan-agents/{agent_id}/toggle", headers=self.headers, json={"enabled": True}
        )
        self.assertEqual(toggled.status_code, 400)
        listed = self.client.get("/api/duzhan-agents").json()["items"]
        self.assertFalse(listed[0]["enabled"])

    def test_good_config_can_enable_and_disable(self):
        agent_id = self.client.post(
            "/api/duzhan-agents", headers=self.headers, json={"name": "好配置官", "blocks": GOOD_BLOCKS}
        ).json()["id"]
        on = self.client.post(
            f"/api/duzhan-agents/{agent_id}/toggle", headers=self.headers, json={"enabled": True}
        )
        self.assertEqual(on.status_code, 200, on.text)
        self.assertTrue(on.json()["enabled"])
        off = self.client.post(
            f"/api/duzhan-agents/{agent_id}/toggle", headers=self.headers, json={"enabled": False}
        )
        self.assertFalse(off.json()["enabled"])

    def test_timezone_case_is_normalised(self):
        body = self.client.post(
            "/api/duzhan-agents",
            headers=self.headers,
            json={"name": "小写时区官", "timezone": "asia/shanghai", "blocks": GOOD_BLOCKS},
        ).json()
        self.assertEqual(body["timezone"], "Asia/Shanghai")

    def test_preview_rejects_bad_day(self):
        agent_id = self.client.post(
            "/api/duzhan-agents", headers=self.headers, json={"name": "试跑日期官", "blocks": GOOD_BLOCKS}
        ).json()["id"]
        bad = self.client.post(
            f"/api/duzhan-agents/{agent_id}/preview", headers=self.headers, json={"day": "不是日期"}
        )
        self.assertEqual(bad.status_code, 422)
        ok = self.client.post(
            f"/api/duzhan-agents/{agent_id}/preview", headers=self.headers, json={"day": "2026-09-29"}
        )
        self.assertEqual(ok.status_code, 200)

    def test_unknown_timezone_is_rejected(self):
        response = self.client.post(
            "/api/duzhan-agents",
            headers=self.headers,
            json={"name": "时区官", "timezone": "Mars/Olympus", "blocks": GOOD_BLOCKS},
        )
        self.assertEqual(response.status_code, 422)

    def test_update_without_enabled_keeps_state(self):
        agent_id = self.client.post(
            "/api/duzhan-agents", headers=self.headers, json={"name": "保状态官", "blocks": GOOD_BLOCKS}
        ).json()["id"]
        self.client.post(
            f"/api/duzhan-agents/{agent_id}/toggle", headers=self.headers, json={"enabled": True}
        )
        body = self.client.put(
            f"/api/duzhan-agents/{agent_id}",
            headers=self.headers,
            json={"name": "保状态官", "blocks": GOOD_BLOCKS},
        ).json()
        self.assertTrue(body["enabled"], "PUT 不带 enabled 时不该把在跑的配置停掉")

    # --- 结构试跑 ---

    def test_preview_returns_structure_without_sending(self):
        agent_id = self.client.post(
            "/api/duzhan-agents", headers=self.headers, json={"name": "试跑官", "blocks": GOOD_BLOCKS}
        ).json()["id"]
        before = self.client.get("/api/duzhan-agents").json()["items"][0]
        response = self.client.post(
            f"/api/duzhan-agents/{agent_id}/preview",
            headers=self.headers,
            json={"day": "2026-09-24", "hour": 10},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["errors"], [])
        self.assertEqual(body["spec"]["slots"], ["10:00", "15:00", "20:00"])
        self.assertEqual(body["spec"]["people"], ["示例成员B"])
        self.assertEqual(body["spec"]["renderer"], "group_brief.renderer")
        joined = "\n".join(body["summary"])
        self.assertIn(GROUPS[0]["name"], joined)
        self.assertIn("渲染器：group_brief.renderer", joined)
        self.assertIn("2026-09-24", joined)
        self.assertEqual(body["hour"], 10)
        self.assertIn("不发消息", body["note"])
        after = self.client.get("/api/duzhan-agents").json()["items"][0]
        self.assertEqual(before["updated_at"], after["updated_at"], "试跑不该改动配置")

    def test_preview_picks_follow_group_renderer(self):
        owner = FOLLOW_GROUPS[0]
        blocks = {
            "blocks": [
                {"type": "group", "channel_id": owner["channel_id"], "label": f"{owner['display']}跟进群"},
                {"type": "times", "slots": ["20:00"]},
                {"type": "source", "key": "msg_summary"},
            ]
        }
        agent_id = self.client.post(
            "/api/duzhan-agents", headers=self.headers, json={"name": "跟进群官", "blocks": blocks}
        ).json()["id"]
        body = self.client.post(
            f"/api/duzhan-agents/{agent_id}/preview", headers=self.headers, json={}
        ).json()
        self.assertEqual(body["spec"]["renderer"], "follow_brief.renderer")
        self.assertIn("档位：20:00", "\n".join(body["summary"]))

    # --- 从代码导入 ---

    def test_seed_from_code_imports_every_group_disabled(self):
        expected = len(GROUPS) + len(FOLLOW_GROUPS)
        first = self.client.post("/api/duzhan-agents/seed-from-code", headers=self.headers, json={})
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertEqual(len(body["created"]), expected)
        self.assertEqual(body["skipped"], [])
        self.assertTrue(all(item["enabled"] is False for item in body["created"]))
        # 导入的配置本身不该带校验问题：跟进群主也要算进督战名单
        problems = [(item["name"], item["errors"]) for item in body["created"] if item["errors"]]
        self.assertEqual(problems, [])
        names = [item["name"] for item in body["created"]]
        self.assertIn(GROUPS[0]["name"] + "督战官", names)
        self.assertIn(FOLLOW_GROUPS[0]["display"] + "跟进群督战官", names)

        second = self.client.post("/api/duzhan-agents/seed-from-code", headers=self.headers, json={})
        self.assertEqual(second.json()["created"], [])
        self.assertEqual(len(second.json()["skipped"]), expected)

        with Session(self.engine) as session:
            rows = session.exec(select(DuzhanAgent)).all()
        self.assertEqual(len(rows), expected)
        self.assertTrue(all(row.enabled is False for row in rows))

    def test_block_schema_lists_all_types(self):
        body = self.client.get("/api/duzhan-agents/block-schema").json()
        self.assertEqual([item["type"] for item in body["blocks"]], list(BLOCK_TYPES))

    # --- 权限 ---

    def test_dependency_is_wired_to_require_admin(self):
        """接口确实挂在 require_admin 上：覆盖依赖后放行，覆盖掉就回到鉴权实现。"""
        self.assertEqual(self.client.get("/api/duzhan-agents").status_code, 200)
        app.dependency_overrides.pop(require_admin)
        try:
            response = self.client.get("/api/duzhan-agents")
            self.assertEqual(response.status_code, 200, "默认放行（演示用），打开 DUZHAN_REQUIRE_AUTH 才拦")
        finally:
            app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
                role="admin", username="duzhan-api-test"
            )

    def test_auth_flag_blocks_anonymous(self):
        app.dependency_overrides.pop(require_admin, None)
        os.environ["DUZHAN_REQUIRE_AUTH"] = "1"
        os.environ["DUZHAN_ADMIN_TOKEN"] = "demo-token"
        try:
            self.assertEqual(self.client.get("/api/duzhan-agents").status_code, 401)
            self.assertEqual(
                self.client.get("/api/duzhan-agents", headers={"Authorization": "Bearer demo-token"}).status_code,
                200,
            )
        finally:
            os.environ.pop("DUZHAN_REQUIRE_AUTH", None)
            os.environ.pop("DUZHAN_ADMIN_TOKEN", None)
            app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
                role="admin", username="duzhan-api-test"
            )


class TestAgentModel(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_default_disabled_and_roundtrip(self):
        with Session(self.engine) as session:
            row = DuzhanAgent(name="模型官", timezone="Asia/Shanghai", blocks_json='{"blocks": []}')
            session.add(row)
            session.commit()
            session.refresh(row)
            self.assertTrue(row.id)
            self.assertFalse(row.enabled)
            self.assertTrue(row.created_at and row.updated_at)
            self.assertEqual(session.get(DuzhanAgent, row.id).name, "模型官")

    def test_name_is_unique(self):
        from sqlalchemy.exc import IntegrityError

        with Session(self.engine) as session:
            session.add(DuzhanAgent(name="唯一官"))
            session.commit()
        with Session(self.engine) as session:
            session.add(DuzhanAgent(name="唯一官"))
            with self.assertRaises(IntegrityError):
                session.commit()


if __name__ == "__main__":
    unittest.main()
