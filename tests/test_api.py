from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app, workspace
from boundary_agent.agent.events import emit
from boundary_agent.models import BlockDecision, ChatIntent, ChatTurnResult
from boundary_agent.paths import SAMPLE_DATA_FILE
from test_spatial import make_shapefile_zip


class ApiTest(unittest.TestCase):
    def setUp(self):
        workspace.reset()
        self.client = TestClient(app)

    def test_workspace_uses_sample_data(self):
        response = self.client.get("/api/workspace")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["block_count"], 3)
        self.assertIn("dataset_name", payload)

    def test_dataset_endpoint_search_and_pagination(self):
        response = self.client.get("/api/dataset")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["block_count"], 3)
        self.assertEqual(payload["matched_count"], 3)
        self.assertEqual(len(payload["blocks"]), 3)
        self.assertFalse(payload["has_more"])

        page = self.client.get("/api/dataset", params={"offset": 0, "limit": 2}).json()
        self.assertEqual(len(page["blocks"]), 2)
        self.assertTrue(page["has_more"])
        tail = self.client.get("/api/dataset", params={"offset": 2, "limit": 2}).json()
        self.assertEqual(len(tail["blocks"]), 1)
        self.assertFalse(tail["has_more"])

        found = self.client.get("/api/dataset", params={"q": "WH2025_DEMO001"}).json()
        self.assertEqual(found["matched_count"], 1)
        self.assertEqual(found["blocks"][0]["block_id"], "WH2025_DEMO001")
        self.assertEqual(found["block_count"], 3)

        none = self.client.get("/api/dataset", params={"q": "不存在的街区"}).json()
        self.assertEqual(none["matched_count"], 0)
        self.assertEqual(none["blocks"], [])
        self.assertFalse(none["has_more"])

    def test_upload_json_dataset(self):
        response = self.client.post(
            "/api/datasets/upload",
            files={"file": ("blocks.json", SAMPLE_DATA_FILE.read_bytes(), "application/json")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["block_count"], 3)
        self.assertEqual(response.json()["artifacts"][0]["kind"], "dataset")

    def test_upload_shapefile_zip_dataset(self):
        response = self.client.post(
            "/api/datasets/upload",
            files={"file": ("blocks.shp.zip", make_shapefile_zip(), "application/zip")},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["block_count"], 2)
        self.assertTrue(payload["has_spatial_data"])
        self.assertEqual(payload["artifacts"][0]["kind"], "spatial")
        artifact_id = payload["artifacts"][0]["id"]
        stored = workspace.get_artifact(artifact_id)
        self.assertIsNotNone(stored)
        self.assertIsNotNone(stored.file_path if stored else None)
        self.assertTrue(stored.file_path.exists() if stored and stored.file_path else False)
        detail = self.client.get(f"/api/artifacts/{artifact_id}").json()
        self.assertEqual(detail["preview"]["spatial"]["type"], "FeatureCollection")
        download = self.client.get(f"/api/artifacts/{artifact_id}/download")
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.content.startswith(b"PK"))

        replacement = self.client.post(
            "/api/datasets/upload",
            files={"file": ("replacement.shp.zip", make_shapefile_zip(), "application/zip")},
        ).json()
        self.assertEqual(len(replacement["artifacts"]), 1)
        self.assertFalse(stored.file_path.exists() if stored and stored.file_path else True)

    def test_upload_constraint_dataset_does_not_replace_blocks(self):
        response = self.client.post(
            "/api/constraints/upload",
            files={"file": ("三线约束数据.zip", make_shapefile_zip(), "application/zip")},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["block_count"], 3)
        self.assertFalse(payload["has_spatial_data"])
        self.assertTrue(payload["constraint_dataset"]["loaded"])
        self.assertGreaterEqual(payload["constraint_dataset"]["layer_count"], 1)
        self.assertEqual(payload["artifacts"], [])

    def test_upload_policy_and_generate_pdf_report(self):
        upload = self.client.post(
            "/api/policies/upload",
            files={"files": ("policy.txt", "严格落实城镇开发边界管控，避让生态保护红线和永久基本农田。", "text/plain")},
        )
        self.assertEqual(upload.status_code, 200)
        self.assertGreaterEqual(len(upload.json()["policy_documents"]), 1)

        workspace.add_analysis({
            "response": "测试分析",
            "block_records": [],
            "decisions": [
                BlockDecision(
                    block_id="WH2025_DEMO001",
                    block_name="测试街区",
                    inside_boundary=False,
                    decision="一级优先调入",
                    overall_suitability=88,
                    development_probability=90,
                    planning_match=86,
                    constraint_cost=8,
                    dynamic_score=89,
                    confidence=0.91,
                    constraint_status="通过",
                    key_reasons=["建设基础较好"],
                    risks=[],
                    missing_information=[],
                    manual_review_required=False,
                    recommendation="建议纳入优先复核",
                    rule_version="test",
                ).model_dump(mode="json")
            ],
        })

        report = self.client.post("/api/reports/policy-pdf", json={"model_id": "local-rules"})
        self.assertEqual(report.status_code, 200)
        payload = report.json()
        self.assertEqual(payload["kind"], "report")
        self.assertEqual(payload["mime"], "application/pdf")
        artifacts = self.client.get("/api/artifacts").json()
        self.assertTrue(any(item["name"].endswith(".docx") for item in artifacts))
        download = self.client.get(f"/api/artifacts/{payload['id']}/download")
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.content.startswith(b"%PDF"))
        preview = self.client.get(f"/api/artifacts/{payload['id']}/preview")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers["content-type"], "application/pdf")
        self.assertIn("inline", preview.headers["content-disposition"])
        detail = self.client.get(f"/api/artifacts/{payload['id']}").json()
        self.assertIn("candidate_blocks", detail["preview"]["evidence"])

    def test_reference_policy_pdf_preview(self):
        response = self.client.get("/api/policies/reference-pdf/preview")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_stream_returns_process_and_result_packets(self):
        captured_settings = {}

        def fake_handle_chat(query, blocks, settings=None, event_callback=None):
            captured_settings.update(settings or {})
            emit(event_callback, "action", "运行测试工具", "测试观察", status="completed")
            return ChatTurnResult(
                intent=ChatIntent(action="help"),
                response=f"已处理：{query}",
            )

        with patch("backend.main.handle_chat", side_effect=fake_handle_chat):
            response = self.client.post(
                "/api/chat/stream",
                json={"query": "测试问题", "model_id": "local-rules"},
            )

        self.assertEqual(response.status_code, 200)
        packets = [json.loads(line) for line in response.text.splitlines() if line]
        self.assertEqual(packets[0]["type"], "process")
        self.assertEqual(packets[-1]["type"], "result")
        self.assertIn("测试问题", packets[-1]["result"]["response"])
        self.assertEqual(packets[-1]["result"]["model_id"], "local-rules")
        self.assertFalse(captured_settings["enabled"])

    def test_workspace_returns_safe_model_catalog(self):
        payload = self.client.get("/api/workspace").json()
        self.assertIn("default_model_id", payload)
        self.assertTrue(any(item["id"] == "local-rules" for item in payload["models"]))
        self.assertNotIn("api_key", json.dumps(payload))

    def test_unknown_model_is_rejected(self):
        response = self.client.post(
            "/api/chat/stream",
            json={"query": "测试问题", "model_id": "not-configured"},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
