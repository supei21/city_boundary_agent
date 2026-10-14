import type { LandscapeResult } from "../types";

export const LANDSCAPE_METRICS = [
  { key: "NP", direction: "↓" },
  { key: "LPI", direction: "↑" },
  { key: "LSI", direction: "↓" },
] as const;

export const AUXILIARY_LANDSCAPE_METRICS = [
  { key: "PD", direction: "↓" },
  { key: "ED", direction: "↓" },
  { key: "AI", direction: "↑" },
] as const;

export function formatLandscapeMetric(value: number | undefined) {
  if (value === undefined || !Number.isFinite(value)) return "—";
  return Math.abs(value) >= 100 ? value.toFixed(2) : value.toFixed(4);
}

export function landscapeMetricState(item: LandscapeResult, metric: string) {
  if (item.metric_assessments?.[metric]) return item.metric_assessments[metric].state;
  if (item.improved_metrics.includes(metric)) return "improved";
  if (item.worsened_metrics.includes(metric)) return "worsened";
  return "unchanged";
}

export function landscapeStateLabel(state: "improved" | "unchanged" | "worsened") {
  if (state === "improved") return "改善";
  if (state === "worsened") return "恶化";
  return "稳定";
}

export function landscapeAssessmentLabel(
  assessment: LandscapeResult["metric_assessments"][string] | undefined,
) {
  if (!assessment || assessment.state === "unchanged" || assessment.strength === "stable") return "稳定";
  const strength = assessment.strength === "significant" ? "明显" : "轻微";
  return `${strength}${assessment.state === "improved" ? "改善" : "恶化"}`;
}
