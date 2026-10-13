import type { LandscapeScenarioEvaluation } from "../types";
import {
  formatLandscapeMetric,
  LANDSCAPE_METRICS,
  landscapeAssessmentLabel,
} from "./landscapePresentation";


export function LandscapeScenarioDetail({ evaluation }: { evaluation: LandscapeScenarioEvaluation }) {
  const { scenario, summary } = evaluation;

  return (
    <section className="scenario-result-card">
      <header>
        <div>
          <span>最近一次组合情景</span>
          <strong>{summary.action_label} · {summary.selected_count} 个地块</strong>
        </div>
        <span className={`landscape-grade ${scenario.overall_grade}`}>{scenario.overall_label}</span>
      </header>

      <table className="map-landscape-metrics" aria-label="组合情景景观指数">
        <thead><tr><th>指标</th><th>调整前</th><th>调整后</th><th>变化</th><th>判断</th></tr></thead>
        <tbody>
          {LANDSCAPE_METRICS.map((metric) => {
            const assessment = scenario.metric_assessments[metric.key];
            const delta = scenario.delta[metric.key];
            return (
              <tr className={assessment?.state ?? "unchanged"} key={metric.key}>
                <th>{metric.key}{metric.direction}</th>
                <td>{formatLandscapeMetric(scenario.before[metric.key])}</td>
                <td>{formatLandscapeMetric(scenario.after[metric.key])}</td>
                <td>{delta > 0 ? "+" : ""}{formatLandscapeMetric(delta)}</td>
                <td>{landscapeAssessmentLabel(assessment)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {scenario.expansion_pattern && (
        <div className="map-landscape-topology safe">
          <span>组合调入关系</span>
          <strong>{scenario.expansion_pattern.label}</strong>
          <small>LEI {scenario.expansion_pattern.index.toFixed(2)} · {scenario.expansion_pattern.buffer_m.toFixed(0)}米缓冲</small>
        </div>
      )}

      <div className={`map-landscape-topology ${scenario.topology_check.risk ? "risk" : "safe"}`}>
        <span>组合拓扑</span>
        <strong>{scenario.topology_check.label} · {scenario.topology_check.risk_label}</strong>
        <small>{scenario.topology_check.message}{scenario.topology_check.risk ? ` 受影响面积占完整边界 ${scenario.topology_check.affected_share_percent.toFixed(4)}%。` : ""}</small>
      </div>

      <p className="map-landscape-conclusion">{scenario.conclusion}</p>
      <p className="landscape-table-note">所选地块先作为一个整体作用于完整现状城镇开发边界，再计算调整前后的六项全域形态指标；组合方案PD和AI均继续采用原阈值。</p>
    </section>
  );
}
