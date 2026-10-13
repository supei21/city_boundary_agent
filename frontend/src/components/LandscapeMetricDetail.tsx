import type { LandscapeResult } from "../types";
import {
  formatLandscapeMetric,
  LANDSCAPE_METRICS,
  landscapeAssessmentLabel,
  landscapeMetricState,
} from "./landscapePresentation";

export function LandscapeMetricDetail({ result }: { result: LandscapeResult }) {
  return (
    <div className="map-landscape-detail">
      <div className="map-landscape-heading">
        <span className={`landscape-action ${result.action}`}>{result.action_label}</span>
        <span className={`landscape-grade ${result.overall_grade}`}>{result.overall_label}</span>
        <span>{result.changed_area_km2.toFixed(3)} km²</span>
      </div>

      <table aria-label={`${result.block_id}景观指数`} className="map-landscape-metrics">
        <thead>
          <tr>
            <th>指标</th>
            <th>调整前</th>
            <th>调整后</th>
            <th>变化量</th>
            <th>判断</th>
          </tr>
        </thead>
        <tbody>
          {LANDSCAPE_METRICS.map((metric) => {
            const state = landscapeMetricState(result, metric.key);
            const delta = result.delta[metric.key];
            return (
              <tr className={state} key={metric.key}>
                <th>{metric.key}{metric.direction}</th>
                <td>{formatLandscapeMetric(result.before[metric.key])}</td>
                <td>{formatLandscapeMetric(result.after[metric.key])}</td>
                <td>{delta > 0 ? "+" : ""}{formatLandscapeMetric(delta)}</td>
                <td>{landscapeAssessmentLabel(result.metric_assessments[metric.key])}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="map-landscape-dimensions">
        {Object.values(result.dimensions).map((dimension) => (
          <div key={dimension.label}>
            <span>{dimension.label}</span>
            <strong className={dimension.grade}>{dimension.grade_label}</strong>
          </div>
        ))}
      </div>

      {result.expansion_pattern && (
        <div className="map-landscape-topology safe">
          <span>调入关系</span>
          <strong>{result.expansion_pattern.label}</strong>
          <small>LEI {result.expansion_pattern.index.toFixed(2)} · {result.expansion_pattern.buffer_m.toFixed(0)}米缓冲</small>
        </div>
      )}

      <div className={`map-landscape-topology ${result.topology_check.risk ? "risk" : "safe"}`}>
        <span>拓扑检查</span>
        <strong>{result.topology_check.label} · {result.topology_check.risk_label}</strong>
        <small>{result.topology_check.message}{result.topology_check.risk ? ` 受影响面积占完整边界 ${result.topology_check.affected_share_percent.toFixed(4)}%。` : ""}</small>
      </div>
      <p className="map-landscape-conclusion">{result.conclusion}</p>
    </div>
  );
}
