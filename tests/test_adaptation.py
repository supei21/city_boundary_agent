from __future__ import annotations

import unittest

from boundary_agent.config import load_config
from boundary_agent.models import BlockInput
from boundary_agent.workflow.agents.adaptation_agent import evaluate_adaptation
from boundary_agent.workflow.agents.constraint_agent import evaluate_constraints
from boundary_agent.workflow.agents.suitability_agent import evaluate_blocks


class AdaptationAgentTest(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        self.blocks = [
            BlockInput(
                block_id="A",
                block_name="甲区",
                inside_boundary=False,
                area_km2=1,
                road_density=8,
                poi_density=40,
                poi_industry=30,
                poi_public=20,
                poi_transport=10,
                dem_relief=5,
                construction_ratio=0.75,
                construction_change=0.30,
                night_light_mean=0.08,
                planning_match=90,
                ecological_redline=False,
                permanent_farmland=False,
                prohibited_disaster_zone=False,
            ),
            BlockInput(
                block_id="B",
                block_name="甲区",
                inside_boundary=False,
                area_km2=1,
                road_density=1,
                poi_density=2,
                poi_industry=1,
                poi_public=1,
                poi_transport=0,
                dem_relief=35,
                construction_ratio=0.10,
                construction_change=-0.10,
                night_light_mean=0.005,
                planning_match=30,
                ecological_redline=False,
                permanent_farmland=False,
                prohibited_disaster_zone=False,
            ),
        ]

    def test_high_signal_block_ranks_first(self):
        suitability = evaluate_blocks(self.blocks, self.config)
        constraints = evaluate_constraints(self.blocks, self.config)
        results = evaluate_adaptation(self.blocks, suitability, constraints, self.config)
        indexed = {item.block_id: item for item in results}
        self.assertGreater(indexed["A"].dynamic_score, indexed["B"].dynamic_score)
        self.assertEqual(indexed["A"].rank_city, 1)
        self.assertEqual(indexed["A"].rank_district, 1)
        self.assertEqual(indexed["A"].recommended_use, "第二产业")


if __name__ == "__main__":
    unittest.main()
