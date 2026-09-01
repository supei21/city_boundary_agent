from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from boundary_agent.llm import user_models
from boundary_agent.llm.client import get_model_settings, list_model_options


class UserModelsStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        user_models._cache = None
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_file = Path(self._tmpdir.name) / "user_models.json"
        self._patcher = patch.object(user_models, "USER_MODELS_FILE", self.tmp_file)
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        user_models._cache = None
        self._tmpdir.cleanup()

    def _add(self, label: str = "DeepSeek 自定义", model: str = "deepseek-chat",
             base_url: str = "https://api.deepseek.com", api_key: str = "sk-test-1234567890") -> dict:
        return user_models.add_user_model(label, model, base_url, api_key)

    def test_add_appears_in_catalog_and_settings(self):
        record = self._add()
        option = next(item for item in list_model_options() if item["id"] == record["id"])
        self.assertTrue(option["ready"])
        self.assertEqual(option["kind"], "llm")
        self.assertEqual(option["base_url"], "https://api.deepseek.com")
        settings = get_model_settings(record["id"])
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["api_key"], "sk-test-1234567890")
        self.assertEqual(settings["api_key_env"], "")
        self.assertEqual(settings["base_url"], "https://api.deepseek.com")

    def test_persists_across_reload(self):
        record = self._add()
        user_models._cache = None  # 模拟重启后重新从磁盘加载
        self.assertIsNotNone(user_models.find_user_model(record["id"]))
        self.assertEqual(
            user_models.find_user_model(record["id"])["api_key"],
            "sk-test-1234567890",
        )

    def test_duplicate_label_conflicts(self):
        self._add(label="DeepSeek 自定义")
        with self.assertRaises(user_models.UserModelConflictError):
            self._add(label="DeepSeek 自定义")

    def test_duplicate_model_and_base_url_conflicts(self):
        self._add(label="第一个")
        with self.assertRaises(user_models.UserModelConflictError):
            self._add(label="第二个")

    def test_chinese_only_label_falls_back_to_uuid(self):
        record = self._add(label="我的模型")
        self.assertRegex(record["id"], r"^custom-[0-9a-f]{8}$")

    def test_delete(self):
        record = self._add()
        self.assertTrue(user_models.delete_user_model(record["id"]))
        self.assertFalse(user_models.delete_user_model(record["id"]))
        self.assertIsNone(user_models.find_user_model(record["id"]))


class UserModelsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        user_models._cache = None
        self._tmpdir = tempfile.TemporaryDirectory()
        self._patcher = patch.object(
            user_models, "USER_MODELS_FILE", Path(self._tmpdir.name) / "user_models.json"
        )
        self._patcher.start()
        from backend.main import app  # 延迟导入，保证 patch 先生效

        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._patcher.stop()
        user_models._cache = None
        self._tmpdir.cleanup()

    def _payload(self, **overrides) -> dict:
        payload = {
            "label": "DeepSeek 自定义",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-test-1234567890",
        }
        payload.update(overrides)
        return payload

    def test_create_returns_masked_key_and_workspace(self):
        response = self.client.post("/api/models", json=self._payload())
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["model"]["api_key_masked"], "••••7890")
        self.assertNotIn("api_key", data["model"])
        self.assertNotIn("sk-test-1234567890", json.dumps(data))
        model_ids = [item["id"] for item in data["workspace"]["models"]]
        self.assertIn(data["model"]["id"], model_ids)
        option = next(
            item for item in data["workspace"]["models"] if item["id"] == data["model"]["id"]
        )
        self.assertTrue(option["ready"])
        self.assertEqual(option["base_url"], "https://api.deepseek.com")

    def test_duplicate_create_conflicts(self):
        first = self.client.post("/api/models", json=self._payload())
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post("/api/models", json=self._payload())
        self.assertEqual(second.status_code, 409)

    def test_invalid_base_url_rejected(self):
        response = self.client.post(
            "/api/models", json=self._payload(base_url="api.deepseek.com")
        )
        self.assertEqual(response.status_code, 422)

    def test_delete_flow(self):
        created = self.client.post("/api/models", json=self._payload()).json()
        model_id = created["model"]["id"]
        self.assertEqual(self.client.delete(f"/api/models/{model_id}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/models/{model_id}").status_code, 404)

    def test_delete_builtin_rejected(self):
        self.assertEqual(self.client.delete("/api/models/default").status_code, 400)

    def test_unknown_model_chat_still_422(self):
        response = self.client.post(
            "/api/chat/stream",
            json={"query": "查看全部街区数据", "model_id": "not-configured"},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
