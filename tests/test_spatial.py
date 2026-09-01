from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import GeometryCollection, LineString, box

from boundary_agent.spatial import (
    build_analysis_geojson,
    export_candidate_shapefile,
    read_shapefile_zip,
)
from boundary_agent.spatial.shapefile import _polygonal_geometry


def make_shapefile_zip() -> bytes:
    with tempfile.TemporaryDirectory() as temp_root:
        root = Path(temp_root)
        frame = gpd.GeoDataFrame(
            {
                "block_id": ["TEST_001", "TEST_002"],
                "block_name": ["候选地块", "普通地块"],
                "inside_bou": [False, True],
                "poi_densit": [45.0, 5.0],
                "road_densi": [8.0, 1.0],
                "Bulit_pro2": [0.72, 0.18],
                "Bulit_CHAG": [0.25, -0.10],
                "NTL_MEAN": [0.06, 0.01],
                "disaster_r": ["低", "高"],
                "ecological": [False, False],
                "farmland": [False, False],
                "prohibited": [False, False],
            },
            geometry=[
                box(12710000, 3550000, 12711000, 3551000),
                box(12712000, 3550000, 12713000, 3551000),
            ],
            crs="EPSG:3857",
        )
        shp_path = root / "blocks.shp"
        frame.to_file(shp_path, driver="ESRI Shapefile", encoding="UTF-8", engine="pyogrio")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in root.glob("blocks.*"):
                archive.write(path, path.name)
        return buffer.getvalue()


def make_coded_landuse_zip() -> bytes:
    with tempfile.TemporaryDirectory() as temp_root:
        root = Path(temp_root)
        frame = gpd.GeoDataFrame(
            {"block_id": ["WH001"], "dominant_l": ["1"]},
            geometry=[box(0, 0, 1, 1)],
            crs="EPSG:4326",
        )
        shp_path = root / "coded_landuse.shp"
        frame.to_file(shp_path, driver="ESRI Shapefile", encoding="UTF-8", engine="pyogrio")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in root.glob("coded_landuse.*"):
                archive.write(path, path.name)
        return buffer.getvalue()


class SpatialDatasetTest(unittest.TestCase):
    def test_numeric_landuse_codes_remain_text(self):
        dataset = read_shapefile_zip("coded-landuse.zip", make_coded_landuse_zip())
        self.addCleanup(dataset.close)
        self.assertEqual(dataset.blocks[0]["dominant_landuse"], "1")
        self.assertTrue(any("开发边界标识" in warning for warning in dataset.warnings))

    def test_geometry_collection_keeps_polygon_parts(self):
        geometry = GeometryCollection([box(0, 0, 1, 1), LineString([(0, 0), (2, 2)])])
        polygonal = _polygonal_geometry(geometry)
        self.assertIsNotNone(polygonal)
        self.assertEqual(polygonal.geom_type if polygonal else None, "Polygon")

    def test_read_reproject_and_export_candidate(self):
        dataset = read_shapefile_zip("blocks.shp.zip", make_shapefile_zip())
        self.addCleanup(dataset.close)
        self.assertEqual(len(dataset.blocks), 2)
        self.assertFalse(dataset.blocks[0]["inside_boundary"])
        limited = build_analysis_geojson(dataset, max_features=1)
        self.assertEqual(limited["metadata"]["displayed_features"], 1)
        self.assertTrue(limited["metadata"]["truncated"])
        self.assertEqual(limited["metadata"]["simplify_meters"], 2)

        decision = {
            "block_id": "TEST_001",
            "decision": "一级优先调入",
            "overall_suitability": 88.0,
            "development_probability": 85.0,
            "planning_match": 72.0,
            "constraint_cost": 0.0,
            "dynamic_score": 83.4,
            "confidence": 0.9,
            "recommended_use": "第二产业",
            "key_drivers": ["建设用地增长", "道路可达性"],
            "constraint_status": "未发现硬约束",
            "rank_city": 1,
            "rank_district": 1,
            "manual_review_required": False,
            "recommendation": "建议作为候选调整地块",
        }
        payload = {
            "selected_block_ids": ["TEST_001"],
            "decisions": [decision],
        }
        geojson = build_analysis_geojson(dataset, payload)
        candidate = next(
            feature for feature in geojson["features"]
            if feature["properties"]["block_id"] == "TEST_001"
        )
        self.assertTrue(candidate["properties"]["is_candidate"])
        self.assertEqual(candidate["properties"]["ai_decision"], "一级优先调入")
        self.assertEqual(candidate["properties"]["ai_dynamic_score"], 83.4)
        self.assertEqual(candidate["properties"]["ai_rank_city"], 1)

        exported = export_candidate_shapefile(dataset, [decision])
        self.assertIsNotNone(exported)
        name, content = exported or ("", b"")
        self.assertEqual(name, "candidate_adjustment_blocks.shp.zip")
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertIn("candidate_adjustment_blocks.shp", archive.namelist())
            self.assertIn("candidate_adjustment_blocks.prj", archive.namelist())

    def test_rejects_zip_without_shapefile(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "not spatial")
        with self.assertRaisesRegex(ValueError, "未找到"):
            read_shapefile_zip("invalid.zip", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
