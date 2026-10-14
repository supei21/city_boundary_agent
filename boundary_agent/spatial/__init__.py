"""Spatial dataset services for upload, preview, and export."""

from boundary_agent.spatial.shapefile import (
    MAX_UPLOAD_BYTES,
    SpatialDataset,
    apply_constraint_layers,
    build_analysis_geojson,
    export_candidate_shapefile,
    read_arcgis_feature_set,
    read_shapefile_zip,
    read_shapefile_zip_path,
)
from boundary_agent.spatial.landscape import (
    LandscapeBoundary,
    build_landscape_boundary_geojson,
    build_landscape_results_geojson,
    build_landscape_scenario_geojson,
    calculate_landscape_metrics,
    evaluate_landscape_change,
    evaluate_landscape_scenario,
    read_landscape_boundary_zip_path,
    read_landscape_boundary_from_constraint_zip,
)

__all__ = [
    "MAX_UPLOAD_BYTES",
    "SpatialDataset",
    "apply_constraint_layers",
    "build_analysis_geojson",
    "export_candidate_shapefile",
    "read_arcgis_feature_set",
    "read_shapefile_zip",
    "read_shapefile_zip_path",
    "LandscapeBoundary",
    "build_landscape_boundary_geojson",
    "build_landscape_results_geojson",
    "build_landscape_scenario_geojson",
    "calculate_landscape_metrics",
    "evaluate_landscape_change",
    "evaluate_landscape_scenario",
    "read_landscape_boundary_zip_path",
    "read_landscape_boundary_from_constraint_zip",
]
