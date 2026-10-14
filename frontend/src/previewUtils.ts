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
    if (Array.isArray(value.results)) {
      return value.results.filter(isRecord).map((item) => {
        const delta = isRecord(item.delta) ? item.delta : {};
        const dimensions = isRecord(item.dimensions) ? item.dimensions : {};
        const topology = isRecord(item.topology_check) ? item.topology_check : {};
        const dimensionLabel = (key: string) => {
          const dimension = isRecord(dimensions[key]) ? dimensions[key] : {};
          return dimension.grade_label ?? "";
        };
        return {
          block_id: item.block_id,
          block_name: item.block_name,
          adjustment: item.action_label,
          overall_evaluation: item.overall_label,
          continuity: dimensionLabel("continuity"),
          fragmentation: dimensionLabel("fragmentation"),
          regularity: dimensionLabel("regularity"),
          topology_check: topology.label,
          shape_score: item.demo_shape_score,
          improved_metrics: Array.isArray(item.improved_metrics) ? item.improved_metrics.join("、") : "",
          worsened_metrics: Array.isArray(item.worsened_metrics) ? item.worsened_metrics.join("、") : "",
          delta_NP: delta.NP,
          delta_PD: delta.PD,
          delta_LPI: delta.LPI,
          delta_ED: delta.ED,
          delta_LSI: delta.LSI,
          delta_AI: delta.AI,
          conclusion: item.conclusion,
        };
      });
    }
  }
  return [];
}

export function extractSpatial(value: unknown): FeatureCollection | null {
  if (!isRecord(value)) return null;
  if (value.type === "landscape_analysis" || value.type === "landscape_scenario_analysis") return null;
  const candidate = isRecord(value.spatial) ? value.spatial : value;
  if (candidate.type !== "FeatureCollection" || !Array.isArray(candidate.features)) return null;
  return candidate as unknown as FeatureCollection;
}
