from __future__ import annotations

import json
import unittest

from boundary_agent.tools.landscape_tools import build_landscape_tools


def fake_landscape_result(block_ids: list[str]) -> dict:
    return {
        "type": "landscape_analysis",
        "summary": {
            "evaluated_count": len(block_ids),
            "improved_count": len(block_ids),
            "worsened_count": 0,
            "balanced_count": 0,
            "grade_counts": {"slightly_improved": len(block_ids)},
        },
        "results": [
            {
                "block_id": block_id,
                "block_name": "测试地块",
                "action": "include",
                "action_label": "调入",
                "changed_area_km2": 1.0,
                "overall_grade": "slightly_improved",
                "overall_label": "轻微改善",
                "before": {"NP": 2},
                "after": {"NP": 1},
                "delta": {"NP": -1},
                "metric_assessments": {"NP": {"state": "improved"}},
                "dimensions": {},
                "topology_check": {"risk": False, "label": "保持连接"},
                "conclusion": "连续性有所改善。",
            }
            for block_id in block_ids
        ],
        "spatial": {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {}, "geometry": None}],
        },
        "artifact": {
            "id": "artifact-1",
            "name": "landscape.json",
            "kind": "landscape",
            "mime": "application/json",
            "size": 10,
            "created_at": "2026-10-08T12:00:00",
        },
    }


class LandscapeToolTest(unittest.TestCase):
    def test_tool_calls_injected_evaluator_and_keeps_geometry_out_of_model_observation(self):
        calls = []
        captured = []

        def evaluator(**kwargs):
            calls.append(kwargs)
            return fake_landscape_result(kwargs["block_ids"])

        tool = build_landscape_tools(
            {"A", "B"},
            evaluator,
            on_result=captured.append,
        )[0]
        payload = json.loads(tool.invoke({
            "block_ids": ["A"],
            "action": "include",
            "limit": 20,
        }))

        self.assertEqual(calls[0]["block_ids"], ["A"])
        self.assertEqual(calls[0]["action"], "include")
        self.assertEqual(payload["tool"], "evaluate_boundary_morphology")
        self.assertNotIn("spatial", payload)
        self.assertEqual(captured[0]["spatial"]["type"], "FeatureCollection")

    def test_tool_reports_unknown_blocks_without_calling_evaluator(self):
        called = False

        def evaluator(**kwargs):
            nonlocal called
            called = True
            return fake_landscape_result(kwargs["block_ids"])

        tool = build_landscape_tools({"A"}, evaluator)[0]
        payload = json.loads(tool.invoke({"block_ids": ["UNKNOWN"]}))
        self.assertIn("error", payload)
        self.assertFalse(called)

    def test_tool_reuses_latest_adjustment_decisions_without_rerunning_candidate_analysis(self):
        calls = []

        def evaluator(**kwargs):
            calls.append(kwargs)
            return fake_landscape_result(kwargs["block_ids"])

        latest = {
            "decisions": [
                {"block_id": "KEEP", "decision": "保持现状", "rank_city": 1, "dynamic_score": 99},
                {"block_id": "OUT", "decision": "建议调出", "rank_city": 3, "dynamic_score": 70},
                {"block_id": "IN2", "decision": "二级优先调入", "rank_city": 2, "dynamic_score": 80},
                {"block_id": "IN1", "decision": "一级优先调入", "rank_city": 1, "dynamic_score": 90},
                {"block_id": "UPDATE", "decision": "内部更新", "rank_city": 4, "dynamic_score": 60},
            ],
        }
        tool = build_landscape_tools(
            {"KEEP", "OUT", "IN2", "IN1", "UPDATE"},
            evaluator,
            latest_analysis_provider=lambda: latest,
        )[0]
        payload = json.loads(tool.invoke({"target": "全部", "limit": 20}))

        self.assertEqual(calls[0]["block_ids"], ["IN1", "IN2", "OUT"])
        self.assertEqual(calls[0]["block_actions"], {
            "IN1": "include",
            "IN2": "include",
            "OUT": "exclude",
        })
        self.assertEqual(payload["source"], "latest_analysis")
        self.assertNotIn("KEEP", payload["selected_block_ids"])
        self.assertNotIn("UPDATE", payload["selected_block_ids"])

    def test_tool_requires_a_prior_analysis_when_blocks_are_not_explicit(self):
        tool = build_landscape_tools(
            {"A"},
            lambda **kwargs: fake_landscape_result(kwargs["block_ids"]),
            latest_analysis_provider=lambda: None,
        )[0]
        payload = json.loads(tool.invoke({"limit": 20}))

        self.assertIn("请先完成候选地块分析", payload["error"])

    def test_tool_evaluates_all_latest_adjustment_candidates_and_compacts_llm_details(self):
        calls = []
        decisions = [
            {
                "block_id": f"IN-{index:02d}",
                "decision": "一级优先调入" if index % 2 else "二级优先调入",
                "rank_city": index,
                "dynamic_score": 90 - index,
            }
            for index in range(1, 26)
        ]

        def evaluator(**kwargs):
            calls.append(kwargs)
            return fake_landscape_result(kwargs["block_ids"])

        tool = build_landscape_tools(
            {item["block_id"] for item in decisions},
            evaluator,
            latest_analysis_provider=lambda: {"decisions": decisions},
        )[0]
        payload = json.loads(tool.invoke({"target": "全部"}))

        self.assertEqual(len(calls[0]["block_ids"]), 25)
        self.assertEqual(calls[0]["limit"], 25)
        self.assertEqual(len(payload["selected_block_ids"]), 25)
        self.assertEqual(len(payload["results"]), 20)
        self.assertEqual(payload["omitted_result_count"], 5)


if __name__ == "__main__":
    unittest.main()
