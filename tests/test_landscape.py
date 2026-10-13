from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
from fastapi.testclient import TestClient
from shapely import union_all
from shapely.geometry import LineString, box

from boundary_agent.spatial.landscape import (
    LandscapeBoundary,
    _dimension_assessments,
    _full_boundary_metrics,
    _metric_assessments,
    _overall_grade,
    _planning_evaluation_geometry,
    calculate_landscape_metrics,
    evaluate_landscape_change,
    evaluate_landscape_scenario,
    read_landscape_boundary_zip_path,
)
from backend.main import app, workspace


def make_boundary_zip(
    geometries: list[LineString] | None = None,
    stem: str = "boundary",
) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    owner = tempfile.TemporaryDirectory()
    root = Path(owner.name)
    boundary_geometries = geometries or [
            LineString(box(0, 0, 200, 200).exterior.coords),
            LineString(box(300, 0, 500, 200).exterior.coords),
        ]
    frame = gpd.GeoDataFrame(
        {"boundary_id": list(range(1, len(boundary_geometries) + 1))},
        geometry=boundary_geometries,
        crs="EPSG:32650",
    )
    shp_path = root / f"{stem}.shp"
    frame.to_file(shp_path, driver="ESRI Shapefile", encoding="UTF-8", engine="pyogrio")
    zip_path = root / f"{stem}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in root.glob(f"{stem}.*"):
            if path != zip_path:
                archive.write(path, path.name)
    return owner, zip_path


def make_candidate_zip() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    owner = tempfile.TemporaryDirectory()
    root = Path(owner.name)
    frame = gpd.GeoDataFrame(
        {"block_id": ["BRIDGE"], "block_name": ["连接地块"]},
        geometry=[box(200, 0, 300, 200)],
        crs="EPSG:32650",
    )
    shp_path = root / "candidates.shp"
    frame.to_file(shp_path, driver="ESRI Shapefile", encoding="UTF-8", engine="pyogrio")
    zip_path = root / "candidates.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in root.glob("candidates.*"):
            if path != zip_path:
                archive.write(path, path.name)
    return owner, zip_path


class LandscapeMetricsTest(unittest.TestCase):
    def test_three_hundred_meter_gap_is_closed_only_for_metric_geometry(self):
        actual = union_all([box(0, 0, 100, 100), box(400, 0, 500, 100)])
        planning = _planning_evaluation_geometry(actual, 300)

        self.assertEqual(actual.geom_type, "MultiPolygon")
        self.assertEqual(planning.geom_type, "Polygon")
        self.assertAlmostEqual(actual.area, 20_000)
        self.assertAlmostEqual(planning.area, 50_000, places=2)

        actual_metrics = _full_boundary_metrics(actual, 50, 0)
        planning_metrics = _full_boundary_metrics(planning, 50, 0)
        self.assertEqual(actual_metrics["NP"], 2)
        self.assertEqual(planning_metrics["NP"], 1)
        self.assertLess(planning_metrics["ED"], actual_metrics["ED"])
        self.assertLess(planning_metrics["LSI"], actual_metrics["LSI"])
        self.assertGreater(planning_metrics["AI"], actual_metrics["AI"])

        beyond_threshold = union_all([box(0, 0, 100, 100), box(401, 0, 501, 100)])
        beyond_planning = _planning_evaluation_geometry(beyond_threshold, 300)
        self.assertEqual(beyond_planning.geom_type, "MultiPolygon")

    def test_corner_proximity_is_not_forced_into_a_planning_connection(self):
        side_facing = union_all([box(0, 0, 100, 100), box(170, 0, 270, 100)])
        corner_facing = union_all([box(0, 0, 100, 100), box(170, 170, 270, 270)])

        side_planning = _planning_evaluation_geometry(side_facing, 100)
        corner_planning = _planning_evaluation_geometry(corner_facing, 100)

        self.assertEqual(side_planning.geom_type, "Polygon")
        self.assertEqual(corner_planning.geom_type, "MultiPolygon")
        self.assertEqual(_full_boundary_metrics(side_planning, 20, 0)["NP"], 1)
        self.assertEqual(_full_boundary_metrics(corner_planning, 20, 0)["NP"], 2)

    def test_citywide_thresholds_keep_tiny_changes_stable_and_topology_separate(self):
        before = {
            "NP": 358.0, "PD": 22.8436, "LPI": 28.1882,
            "ED": 16.8125, "LSI": 16.6392, "AI": 97.6652,
        }
        after = {
            "NP": 359.0, "PD": 22.9006, "LPI": 28.1799,
            "ED": 16.8539, "LSI": 16.6826, "AI": 97.6582,
        }
        assessments = _metric_assessments(
            before,
            after,
            stable_relative_threshold=0.0005,
            significant_relative_threshold=0.005,
            stable_percentage_point_threshold=0.01,
            significant_percentage_point_threshold=0.1,
            significant_np_change=2,
        )
        self.assertEqual(assessments["NP"]["strength"], "slight")
        self.assertEqual(assessments["PD"]["strength"], "slight")
        self.assertEqual(assessments["LPI"]["strength"], "stable")
        self.assertEqual(assessments["AI"]["strength"], "stable")
        dimensions = _dimension_assessments(assessments)
        self.assertTrue(all(
            item["grade"] != "significantly_worsened"
            for item in dimensions.values()
        ))
        grade, _ = _overall_grade(dimensions, {"risk": True})
        self.assertEqual(grade, "slightly_worsened")

    def test_single_block_pd_thresholds_do_not_change_other_metric_thresholds(self):
        before = {
            "NP": 355.0, "PD": 22.637058, "LPI": 28.1882,
            "ED": 16.8125, "LSI": 16.6392, "AI": 97.6652,
        }
        after = {
            **before,
            "PD": 22.6314,
            "ED": 16.8160,
            "LSI": 16.6420,
        }
        assessments = _metric_assessments(
            before,
            after,
            stable_relative_threshold=0.0005,
            significant_relative_threshold=0.005,
            stable_percentage_point_threshold=0.01,
            significant_percentage_point_threshold=0.1,
            significant_np_change=2,
            stable_pd_relative_threshold=0.00005,
            significant_pd_relative_threshold=0.001,
        )

        self.assertEqual(assessments["PD"]["state"], "improved")
        self.assertEqual(assessments["PD"]["strength"], "slight")
        self.assertEqual(assessments["ED"]["strength"], "stable")
        self.assertEqual(assessments["LSI"]["strength"], "stable")

    def test_single_block_ai_thresholds_do_not_change_lpi_thresholds(self):
        before = {
            "NP": 355.0, "PD": 22.637058, "LPI": 28.1882,
            "ED": 16.8125, "LSI": 16.6392, "AI": 97.6652,
        }
        after = {
            **before,
            "LPI": before["LPI"] + 0.002,
            "AI": before["AI"] + 0.002,
        }
        assessments = _metric_assessments(
            before,
            after,
            stable_relative_threshold=0.0005,
            significant_relative_threshold=0.005,
            stable_percentage_point_threshold=0.01,
            significant_percentage_point_threshold=0.1,
            significant_np_change=2,
            stable_ai_percentage_point_threshold=0.001,
            significant_ai_percentage_point_threshold=0.02,
        )

        self.assertEqual(assessments["AI"]["state"], "improved")
        self.assertEqual(assessments["AI"]["strength"], "slight")
        self.assertEqual(assessments["LPI"]["state"], "unchanged")
        self.assertEqual(assessments["LPI"]["strength"], "stable")

    def test_metrics_distinguish_split_and_compact_patterns(self):
        split = np.zeros((10, 10), dtype=bool)
        split[2:5, 1:4] = True
        split[2:5, 6:9] = True
        compact = np.zeros((10, 10), dtype=bool)
        compact[2:5, 1:9] = True
        split_metrics = calculate_landscape_metrics(split, 10, connectivity=8)
        compact_metrics = calculate_landscape_metrics(compact, 10, connectivity=8)
        self.assertEqual(split_metrics["NP"], 2)
        self.assertEqual(compact_metrics["NP"], 1)
        self.assertGreater(compact_metrics["LPI"], split_metrics["LPI"])
        self.assertGreater(compact_metrics["AI"], split_metrics["AI"])

    def test_line_boundary_reconstructs_and_bridge_improves_continuity(self):
        owner, zip_path = make_boundary_zip(
            [
                LineString(box(0, 0, 200, 200).exterior.coords),
                LineString(box(600, 0, 800, 200).exterior.coords),
            ]
        )
        self.addCleanup(owner.cleanup)
        boundary = read_landscape_boundary_zip_path("boundary.zip", zip_path)
        self.addCleanup(boundary.close)
        self.assertEqual(boundary.patch_count, 2)
        self.assertTrue(any("重建为面" in warning for warning in boundary.warnings))

        candidates = gpd.GeoDataFrame(
            {"block_id": ["BRIDGE"], "block_name": ["连接地块"]},
            geometry=[box(200, 0, 600, 200)],
            crs="EPSG:32650",
        )
        result = evaluate_landscape_change(
            boundary,
            candidates,
            ["BRIDGE"],
            block_actions={"BRIDGE": "include"},
            neighborhood_m=400,
            cell_size_m=20,
            connectivity=8,
        )[0]
        self.assertEqual(result["before"]["NP"], 2)
        self.assertEqual(result["after"]["NP"], 1)
        self.assertLess(result["delta"]["NP"], 0)
        self.assertGreater(result["delta"]["LPI"], 0)
        self.assertIn("NP", result["improved_metrics"])
        self.assertEqual(result["topology_check"]["status"], "connected")
        self.assertEqual(result["topology_check"]["risk_level"], "none")
        self.assertIn(result["expansion_pattern"]["pattern"], {"edge_expansion", "infill"})
        self.assertIn(result["overall_grade"], {"significantly_improved", "slightly_improved"})
        self.assertEqual(
            set(result["dimensions"]),
            {"continuity", "fragmentation", "regularity"},
        )

    def test_include_island_and_exclude_split_trigger_topology_risks(self):
        owner, zip_path = make_boundary_zip()
        self.addCleanup(owner.cleanup)
        boundary = read_landscape_boundary_zip_path("boundary.zip", zip_path)
        self.addCleanup(boundary.close)
        island_candidates = gpd.GeoDataFrame(
            {"block_id": ["ISLAND"]},
            geometry=[box(900, 0, 1000, 100)],
            crs="EPSG:32650",
        )
        island = evaluate_landscape_change(
            boundary,
            island_candidates,
            ["ISLAND"],
            block_actions={"ISLAND": "include"},
            neighborhood_m=800,
            cell_size_m=20,
        )[0]
        self.assertEqual(island["topology_check"]["status"], "island")
        self.assertTrue(island["topology_check"]["risk"])
        self.assertEqual(island["topology_check"]["risk_level"], "high")
        self.assertEqual(island["expansion_pattern"]["pattern"], "outlying")
        self.assertEqual(island["overall_grade"], "significantly_worsened")

        split_owner, split_zip = make_boundary_zip(
            [LineString(box(0, 0, 800, 200).exterior.coords)]
        )
        self.addCleanup(split_owner.cleanup)
        split_boundary = read_landscape_boundary_zip_path("single-boundary.zip", split_zip)
        self.addCleanup(split_boundary.close)
        cut_candidates = gpd.GeoDataFrame(
            {"block_id": ["CUT"]},
            geometry=[box(200, 0, 600, 200)],
            crs="EPSG:32650",
        )
        split = evaluate_landscape_change(
            split_boundary,
            cut_candidates,
            ["CUT"],
            block_actions={"CUT": "exclude"},
            neighborhood_m=400,
            cell_size_m=20,
        )[0]
        self.assertEqual(split["topology_check"]["status"], "split")
        self.assertTrue(split["topology_check"]["risk"])
        self.assertEqual(split["overall_grade"], "significantly_worsened")

    def test_selected_chain_is_conditionally_connected_as_one_scenario(self):
        owner, zip_path = make_boundary_zip(
            [LineString(box(0, 0, 100, 100).exterior.coords)]
        )
        self.addCleanup(owner.cleanup)
        boundary = read_landscape_boundary_zip_path("chain-boundary.zip", zip_path)
        self.addCleanup(boundary.close)
        candidates = gpd.GeoDataFrame(
            {
                "block_id": ["A", "B", "C"],
                "block_name": ["入口", "桥接", "末端"],
            },
            geometry=[box(350, 0, 450, 100), box(700, 0, 800, 100), box(1050, 0, 1150, 100)],
            crs="EPSG:32650",
        )

        independent = evaluate_landscape_change(
            boundary,
            candidates,
            ["C"],
            block_actions={"C": "include"},
            neighborhood_m=500,
            cell_size_m=20,
        )[0]
        scenario = evaluate_landscape_scenario(
            boundary,
            candidates,
            ["A", "B", "C"],
            action="include",
            neighborhood_m=500,
            cell_size_m=20,
        )

        self.assertEqual(independent["topology_check"]["status"], "island")
        self.assertEqual(independent["adjacency_tolerance_m"], 300.0)
        self.assertEqual(scenario["topology_check"]["status"], "connected")
        self.assertTrue(scenario["planning_evaluation"]["consistent_patch_count"])
        statuses = {item["block_id"]: item["status"] for item in scenario["block_connections"]}
        self.assertEqual(statuses, {"A": "direct", "B": "conditional", "C": "conditional"})
        self.assertEqual(len(scenario["groups"]), 1)

    def test_scenario_topology_follows_fifty_meter_nearby_chain(self):
        owner, zip_path = make_boundary_zip(
            [LineString(box(0, 0, 100, 100).exterior.coords)]
        )
        self.addCleanup(owner.cleanup)
        boundary = read_landscape_boundary_zip_path("nearby-chain-boundary.zip", zip_path)
        self.addCleanup(boundary.close)
        candidates = gpd.GeoDataFrame(
            {"block_id": ["A", "B", "C"]},
            geometry=[box(140, 0, 240, 100), box(280, 0, 380, 100), box(420, 0, 520, 100)],
            crs="EPSG:32650",
        )

        scenario = evaluate_landscape_scenario(
            boundary,
            candidates,
            ["A", "B", "C"],
            action="include",
            neighborhood_m=600,
            cell_size_m=20,
            adjacency_tolerance_m=50,
        )

        self.assertEqual(scenario["topology_check"]["status"], "connected")
        self.assertEqual(scenario["topology_check"]["label"], "组合连通")
        self.assertFalse(scenario["topology_check"]["risk"])

    def test_single_block_uses_fifty_meter_planning_connectivity(self):
        owner, zip_path = make_boundary_zip(
            [LineString(box(0, 0, 100, 100).exterior.coords)]
        )
        self.addCleanup(owner.cleanup)
        boundary = read_landscape_boundary_zip_path("nearby-boundary.zip", zip_path)
        self.addCleanup(boundary.close)
        candidates = gpd.GeoDataFrame(
            {"block_id": ["NEARBY", "ISOLATED"]},
            geometry=[box(140, 0, 240, 100), box(180, 300, 280, 400)],
            crs="EPSG:32650",
        )

        nearby = evaluate_landscape_change(
            boundary,
            candidates,
            ["NEARBY"],
            block_actions={"NEARBY": "include"},
            neighborhood_m=500,
            cell_size_m=20,
            adjacency_tolerance_m=50,
        )[0]
        isolated = evaluate_landscape_change(
            boundary,
            candidates,
            ["ISOLATED"],
            block_actions={"ISOLATED": "include"},
            neighborhood_m=500,
            cell_size_m=20,
            adjacency_tolerance_m=50,
        )[0]

        self.assertEqual(nearby["topology_check"]["status"], "nearby_connected")
        self.assertFalse(nearby["topology_check"]["risk"])
        self.assertEqual(nearby["delta"]["NP"], 0)
        self.assertEqual(isolated["topology_check"]["status"], "island")
        self.assertTrue(isolated["topology_check"]["risk"])

    def test_every_candidate_uses_the_same_complete_boundary_baseline(self):
        owner, zip_path = make_boundary_zip(
            [
                LineString(box(0, 0, 100, 100).exterior.coords),
                LineString(box(10_000, 0, 10_200, 200).exterior.coords),
            ]
        )
        self.addCleanup(owner.cleanup)
        boundary = read_landscape_boundary_zip_path("citywide-boundary.zip", zip_path)
        self.addCleanup(boundary.close)
        candidates = gpd.GeoDataFrame(
            {"block_id": ["WEST", "EAST"]},
            geometry=[box(100, 0, 180, 100), box(10_200, 0, 10_280, 200)],
            crs="EPSG:32650",
        )

        results = evaluate_landscape_change(
            boundary,
            candidates,
            ["WEST", "EAST"],
            block_actions={"WEST": "include", "EAST": "include"},
            neighborhood_m=200,
            cell_size_m=20,
        )

        self.assertEqual(results[0]["before"], results[1]["before"])
        self.assertEqual(results[0]["before"]["NP"], 2)
        self.assertEqual(results[0]["evaluation_scope"], "full_boundary")
        self.assertNotIn("window_area_km2", results[0])


class LandscapeApiTest(unittest.TestCase):
    def setUp(self):
        workspace.reset()
        self.client = TestClient(app)

    def test_upload_boundary_and_evaluate_candidate(self):
        boundary_owner, boundary_zip = make_boundary_zip(stem="urban_development_boundary")
        candidate_owner, candidate_zip = make_candidate_zip()
        self.addCleanup(boundary_owner.cleanup)
        self.addCleanup(candidate_owner.cleanup)
        candidate_upload = self.client.post(
            "/api/datasets/upload",
            files={"file": ("candidates.zip", candidate_zip.read_bytes(), "application/zip")},
        )
        self.assertEqual(candidate_upload.status_code, 200)
        boundary_upload = self.client.post(
            "/api/constraints/upload",
            files={"file": ("constraints.zip", boundary_zip.read_bytes(), "application/zip")},
        )
        self.assertEqual(boundary_upload.status_code, 200)
        self.assertTrue(boundary_upload.json()["landscape_boundary"]["loaded"])
        boundary_geojson = self.client.get("/api/landscape/boundary/geojson")
        self.assertEqual(boundary_geojson.status_code, 200)
        boundary_payload = boundary_geojson.json()
        self.assertEqual(boundary_payload["type"], "FeatureCollection")
        self.assertEqual(
            boundary_payload["features"][0]["properties"]["layer_type"],
            "urban_development_boundary",
        )
        self.assertEqual(boundary_payload["features"][0]["properties"]["patch_count"], 2)
        response = self.client.post(
            "/api/landscape/evaluate",
            json={
                "block_ids": ["BRIDGE"],
                "block_actions": {"BRIDGE": "include"},
                "neighborhood_m": 400,
                "cell_size_m": 20,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["evaluated_count"], 1)
        self.assertIn("grade_counts", payload["summary"])
        self.assertEqual(payload["results"][0]["delta"]["NP"], 0)
        self.assertEqual(payload["results"][0]["adjacency_tolerance_m"], 300.0)
        self.assertEqual(payload["results"][0]["topology_check"]["status"], "connected")
        self.assertEqual(payload["spatial"]["type"], "FeatureCollection")
        self.assertEqual(len(payload["spatial"]["features"]), 1)
        spatial_properties = payload["spatial"]["features"][0]["properties"]
        self.assertEqual(spatial_properties["block_id"], "BRIDGE")
        self.assertEqual(spatial_properties["landscape_action"], "include")
        self.assertEqual(spatial_properties["layer_type"], "landscape_adjustment")
        self.assertEqual(payload["artifact"]["kind"], "landscape")

        stream_response = self.client.post(
            "/api/landscape/evaluate/stream",
            json={
                "block_ids": ["BRIDGE"],
                "block_actions": {"BRIDGE": "include"},
                "cell_size_m": 20,
            },
        )
        self.assertEqual(stream_response.status_code, 200)
        stream_packets = [
            json.loads(line)
            for line in stream_response.text.splitlines()
            if line.strip()
        ]
        progress_packets = [item["progress"] for item in stream_packets if item["type"] == "progress"]
        self.assertEqual(progress_packets[0]["phase"], "preparing")
        self.assertTrue(any(item["phase"] == "evaluating" and item["completed"] == 1 for item in progress_packets))
        self.assertEqual(progress_packets[-1]["phase"], "completed")
        self.assertEqual(progress_packets[-1]["percent"], 100.0)
        stream_result = next(item["result"] for item in stream_packets if item["type"] == "result")
        self.assertEqual(stream_result["results"][0]["block_id"], "BRIDGE")

        scenario_response = self.client.post(
            "/api/landscape/scenario/evaluate",
            json={"block_ids": ["BRIDGE"], "action": "include"},
        )
        self.assertEqual(scenario_response.status_code, 200)
        scenario_payload = scenario_response.json()
        self.assertEqual(scenario_payload["type"], "landscape_scenario_analysis")
        self.assertEqual(scenario_payload["summary"]["selected_count"], 1)
        self.assertEqual(scenario_payload["scenario"]["topology_check"]["status"], "connected")

        chat_response = self.client.post(
            "/api/chat/stream",
            json={
                "query": "对 BRIDGE 按调入情景运行形态评价",
                "model_id": "local-rules",
            },
        )
        self.assertEqual(chat_response.status_code, 200)
        packets = [json.loads(line) for line in chat_response.text.splitlines() if line.strip()]
        self.assertTrue(any(item["type"] == "landscape_progress" for item in packets))
        result_packet = next(item for item in packets if item["type"] == "result")
        self.assertEqual(result_packet["result"]["intent"]["action"], "evaluate_landscape")
        self.assertEqual(
            result_packet["result"]["landscape_evaluation"]["results"][0]["block_id"],
            "BRIDGE",
        )
        self.assertEqual(result_packet["artifact"]["kind"], "landscape")

    def test_constraint_upload_provides_landscape_baseline(self):
        boundary_owner, boundary_zip = make_boundary_zip(stem="urban_development_boundary")
        self.addCleanup(boundary_owner.cleanup)
        response = self.client.post(
            "/api/constraints/upload",
            files={"file": ("constraints.zip", boundary_zip.read_bytes(), "application/zip")},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["constraint_dataset"]["loaded"])
        self.assertTrue(payload["landscape_boundary"]["loaded"])
        self.assertEqual(payload["landscape_boundary"]["patch_count"], 2)
        boundary_geojson = self.client.get("/api/landscape/boundary/geojson")
        self.assertEqual(boundary_geojson.status_code, 200)


if __name__ == "__main__":
    unittest.main()
