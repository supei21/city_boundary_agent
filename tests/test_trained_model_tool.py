from __future__ import annotations

import unittest
from pathlib import Path

from boundary_agent.config import load_config
from boundary_agent.io import parse_json
from boundary_agent.paths import SAMPLE_DATA_FILE
from boundary_agent.spatial.shapefile import read_shapefile_zip_path
from boundary_agent.tools.trained_model import (
    TrainedBoundaryModelError,
    enrich_blocks_with_model_predictions,
    model_status,
    resolve_model_dir,
)
from boundary_agent.workflow.graph import run_workflow


class TrainedModelToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config()
        cls.model_dir = resolve_model_dir(cls.config)
        cls.blocks = parse_json(SAMPLE_DATA_FILE.read_bytes())

    def test_model_status_reports_configured_model(self):
        status = model_status(self.config)
        self.assertTrue(status["enabled"])
        self.assertEqual(Path(status["model_dir"]), self.model_dir)
        if (self.model_dir / "feature_names.json").exists():
            self.assertGreater(status["feature_count"], 0)

    @unittest.skipUnless(
        Path(r"E:\VscodeMyPrograma\boundary_agent_demo\models\xgb_boundary.json").exists(),
        "trained model JSON is not available on this machine",
    )
    def test_sparse_demo_blocks_fall_back_instead_of_forcing_model(self):
        with self.assertRaises(TrainedBoundaryModelError):
            enrich_blocks_with_model_predictions(self.blocks, self.config)

    @unittest.skipUnless(
        Path(r"E:\VscodeMyPrograma\boundary_agent_demo\models\xgb_boundary.json").exists(),
        "trained model JSON is not available on this machine",
    )
    def test_workflow_falls_back_for_sparse_demo_blocks(self):
        result = run_workflow(self.blocks, self.config, include_ai_explanations=False)
        trained_model = result["summary"]["trained_model"]
        self.assertFalse(trained_model["applied"])
        self.assertIn("回退", trained_model["warning"])
        self.assertEqual(result["summary"]["total"], len(self.blocks))

    @unittest.skipUnless(
        Path(r"E:\VscodeMyPrograma\boundary_agent_demo\models\xgb_boundary.json").exists()
        and Path(r"D:\downloadzhuomian\城镇开发边界\初步筛选调整地块\初步筛选调整地块.zip").exists(),
        "trained model or candidate parcel ZIP is not available on this machine",
    )
    def test_enrich_blocks_adds_development_probability(self):
        dataset = read_shapefile_zip_path(
            "初步筛选调整地块",
            Path(r"D:\downloadzhuomian\城镇开发边界\初步筛选调整地块\初步筛选调整地块.zip"),
        )
        self.addCleanup(dataset.close)
        bundle = enrich_blocks_with_model_predictions(dataset.blocks[:10], self.config)
        self.assertEqual(len(bundle.blocks), 10)
        self.assertGreater(bundle.feature_count, 0)
        for block in bundle.blocks:
            self.assertIn("development_probability", block)
            self.assertGreaterEqual(block["development_probability"], 0)
            self.assertLessEqual(block["development_probability"], 100)

    @unittest.skipUnless(
        Path(r"E:\VscodeMyPrograma\boundary_agent_demo\models\xgb_boundary.json").exists()
        and Path(r"D:\downloadzhuomian\城镇开发边界\初步筛选调整地块\初步筛选调整地块.zip").exists(),
        "trained model or candidate parcel ZIP is not available on this machine",
    )
    def test_workflow_uses_model_probability_when_enriched(self):
        dataset = read_shapefile_zip_path(
            "初步筛选调整地块",
            Path(r"D:\downloadzhuomian\城镇开发边界\初步筛选调整地块\初步筛选调整地块.zip"),
        )
        self.addCleanup(dataset.close)
        bundle = enrich_blocks_with_model_predictions(dataset.blocks[:10], self.config)
        result = run_workflow(dataset.blocks[:10], self.config, include_ai_explanations=False)
        model_scores = {
            str(block["block_id"]): round(float(block["development_probability"]), 2)
            for block in bundle.blocks
        }
        self.assertTrue(result["summary"]["trained_model"]["applied"])
        for decision in result["decisions"]:
            self.assertEqual(
                round(float(decision["development_probability"]), 2),
                model_scores[str(decision["block_id"])],
            )


if __name__ == "__main__":
    unittest.main()
