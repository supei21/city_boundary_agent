import type { FeatureCollection } from "geojson";

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function extractRows(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.filter(isRecord);
  if (isRecord(value)) {
    if (Array.isArray(value.block_records)) {
      const blockRecords = value.block_records.filter(isRecord);
      if (blockRecords.length) return blockRecords;
    }
    if (isRecord(value.spatial) && Array.isArray(value.spatial.features)) {
      const spatialRecords = value.spatial.features
        .map((feature) => isRecord(feature) ? feature.properties : null)
        .filter(isRecord);
      if (spatialRecords.length) return spatialRecords;
    }
    if (Array.isArray(value.decisions)) return value.decisions.filter(isRecord);
  }
  return [];
}

export function extractSpatial(value: unknown): FeatureCollection | null {
  if (!isRecord(value)) return null;
  const candidate = isRecord(value.spatial) ? value.spatial : value;
  if (candidate.type !== "FeatureCollection" || !Array.isArray(candidate.features)) return null;
  return candidate as unknown as FeatureCollection;
}
