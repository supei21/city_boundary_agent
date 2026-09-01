"""Spatial dataset services for upload, preview, and export."""

from boundary_agent.spatial.shapefile import (
    MAX_UPLOAD_BYTES,
    SpatialDataset,
    build_analysis_geojson,
    export_candidate_shapefile,
    read_shapefile_zip,
    read_shapefile_zip_path,
)

__all__ = [
    "MAX_UPLOAD_BYTES",
    "SpatialDataset",
    "build_analysis_geojson",
    "export_candidate_shapefile",
    "read_shapefile_zip",
    "read_shapefile_zip_path",
]
