import type { LandscapeEvaluation } from "../types";
import {
  formatLandscapeMetric,
  LANDSCAPE_METRICS,
  landscapeMetricState,
} from "./landscapePresentation";

export function LandscapeTable({ evaluation }: { evaluation: LandscapeEvaluation }) {
  const { summary, results } = evaluation;
  const gradeCounts = summary.grade_counts ?? {
    significantly_improved: summary.improved_count,
    slightly_improved: 0,
    stable: summary.balanced_count,
    slightly_worsened: 0,
    significantly_worsened: summary.worsened_count,
  };

  return (
    <div className="landscape-result-view">
      <div className="landscape-summary">
        <div><span>评价地块</span><strong>{summary.evaluated_count}</strong></div>
        <div className="improved"><span>明显改善</span><strong>{gradeCounts.significantly_improved}</strong></div>
        <div className="improved"><span>轻微改善</span><strong>{gradeCounts.slightly_improved}</strong></div>
        <div><span>基本稳定</span><strong>{gradeCounts.stable}</strong></div>
        <div className="worsened"><span>轻微恶化</span><strong>{gradeCounts.slightly_worsened}</strong></div>
        <div className="worsened"><span>明显恶化</span><strong>{gradeCounts.significantly_worsened}</strong></div>
      </div>

      <div className="result-table-wrap landscape-table-wrap">
        <table className="result-table landscape-table">
          <thead>
            <tr>
              <th>地块</th>
              <th>调整动作</th>
              <th>综合评价</th>
              <th>变化面积</th>
              {LANDSCAPE_METRICS.map((metric) => (
                <th key={metric.key}>{metric.key}{metric.direction}</th>
              ))}
              <th>三个核心维度</th>
              <th>调入关系</th>
              <th>拓扑检查</th>
              <th>评价结论</th>
            </tr>
          </thead>
          <tbody>
            {results.map((item) => (
              <tr key={`${item.block_id}-${item.action}`}>
                <td>
                  <strong>{item.block_id}</strong>
                  <small>{item.block_name ?? "未命名"}</small>
                </td>
                <td><span className={`landscape-action ${item.action}`}>{item.action_label}</span></td>
                <td><span className={`landscape-grade ${item.overall_grade}`}>{item.overall_label}</span></td>
                <td>{item.changed_area_km2.toFixed(3)} km²</td>
                {LANDSCAPE_METRICS.map((metric) => {
                  const delta = item.delta[metric.key];
                  const state = landscapeMetricState(item, metric.key);
                  return (
                    <td className={`landscape-metric ${state}`} key={metric.key}>
                      <span>{formatLandscapeMetric(item.before[metric.key])} → {formatLandscapeMetric(item.after[metric.key])}</span>
                      <small>{delta > 0 ? "+" : ""}{formatLandscapeMetric(delta)}</small>
                    </td>
                  );
                })}
                <td className="landscape-dimensions">
                  {Object.values(item.dimensions ?? {}).map((dimension) => (
                    <span className={dimension.grade} key={dimension.label}>
                      {dimension.label}：{dimension.grade_label}
                    </span>
                  ))}
                </td>
                <td className="landscape-topology safe">
                  {item.expansion_pattern ? (
                    <><strong>{item.expansion_pattern.label}</strong><small>LEI {item.expansion_pattern.index.toFixed(2)}</small></>
                  ) : <strong>不适用</strong>}
                </td>
                <td className={`landscape-topology ${item.topology_check?.risk ? "risk" : "safe"}`}>
                  <strong>{item.topology_check?.label ?? "—"} · {item.topology_check?.risk_label ?? ""}</strong>
                  <small>{item.topology_check?.message ?? ""}{item.topology_check?.risk ? ` 受影响占比${item.topology_check.affected_share_percent.toFixed(4)}%。` : ""}</small>
                </td>
                <td className="landscape-conclusion">{item.conclusion}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="landscape-table-note">综合等级使用NP、LPI、LSI三个核心指标：单个轻微变化视为局部波动，三项得分净和达到±2或出现未被反向明显变化抵消的单项明显变化时判为轻微影响；PD、ED、AI仅作辅助观测，拓扑风险独立显示。</p>
    </div>
  );
}
