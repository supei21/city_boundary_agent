from __future__ import annotations

import json
import unittest

from boundary_agent.io import parse_json
from boundary_agent.paths import SAMPLE_DATA_FILE
from boundary_agent.workflow.graph import run_workflow


class WorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_workflow(
            parse_json(SAMPLE_DATA_FILE.read_bytes()),
            include_ai_explanations=False,
        )

    def test_count(self):
        self.assertEqual(self.result["summary"]["total"], 3)

    def test_hard_constraint_never_included(self):
        decisions = {item["block_id"]: item for item in self.result["decisions"]}
        self.assertEqual(decisions["WH2025_DEMO002"]["decision"], "人工复核")

    def test_high_risk_low_suitability_excluded(self):
        decisions = {item["block_id"]: item for item in self.result["decisions"]}
        self.assertEqual(decisions["WH2025_DEMO003"]["decision"], "建议调出")

    def test_json_serializable(self):
        json.dumps(self.result, ensure_ascii=False)

    def test_dynamic_adaptation_fields_and_ranks(self):
        decisions = self.result["decisions"]
        self.assertEqual(sorted(item["rank_city"] for item in decisions), [1, 2, 3])
        for item in decisions:
            self.assertGreaterEqual(item["dynamic_score"], 0)
            self.assertLessEqual(item["dynamic_score"], 100)
            self.assertIn("development_probability", item)
            self.assertIn("planning_match", item)
            self.assertIn("constraint_cost", item)
            self.assertTrue(item["key_drivers"])

    def test_summary_contains_top_candidates(self):
        self.assertTrue(self.result["summary"]["top_candidate_ids"])
        self.assertIn("dynamic_score_average", self.result["summary"])

    def test_llm_explanations_can_be_disabled(self):
        self.assertTrue(all(item["ai_explanation"] is None for item in self.result["decisions"]))


if __name__ == "__main__":
    unittest.main()
