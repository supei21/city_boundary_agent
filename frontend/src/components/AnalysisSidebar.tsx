import { useState } from "react";
import { BarChart3, CheckCircle2, ChevronDown, Download, Eye, FileText, Play, X } from "lucide-react";

import { artifactDownloadUrl, artifactPreviewUrl } from "../api";
import type { ArtifactSummary, Decision, WorkspaceSummary } from "../types";

interface Props {
  open: boolean;
  workspace: WorkspaceSummary | null;
  decisions: Decision[];
  busy: boolean;
  artifacts: ArtifactSummary[];
  selectedModelId: string;
  onRunAnalysis: () => void;
  onGeneratePolicyReport: () => void;
  onClose: () => void;
}

const METRIC_LABELS: Record<string, string> = {
  test_roc_auc: "ROC-AUC",
  test_pr_auc_average_precision: "PR-AUC",
  test_f1: "F1",
  test_precision: "Precision",
  test_recall: "Recall",
};

const DIRECTORY_ITEMS = [
  { key: "analysis", icon: BarChart3, title: "候选地块分析", subtitle: "识别调入/调出候选地块" },
  { key: "report", icon: FileText, title: "报告生成", subtitle: "生成分析报告文档" },
] as const;

type DirectoryKey = (typeof DIRECTORY_ITEMS)[number]["key"];

function formatMetric(value: unknown) {
  return typeof value === "number" ? value.toFixed(3) : "";
}

function decisionCounts(decisions: Decision[]) {
  return decisions.reduce<Record<string, number>>((acc, item) => {
    acc[item.decision] = (acc[item.decision] ?? 0) + 1;
    return acc;
  }, {});
}

export function AnalysisSidebar({
  open,
  workspace,
  decisions,
  busy,
  artifacts,
  selectedModelId,
  onRunAnalysis,
  onGeneratePolicyReport,
  onClose,
}: Props) {
  const [expandedKey, setExpandedKey] = useState<DirectoryKey | null>("report");

  const diagnostics = workspace?.model_diagnostics ?? {};
  const metrics = diagnostics.metrics ?? {};
  const metricRows = Object.entries(METRIC_LABELS)
    .map(([key, label]) => ({ key, label, value: formatMetric(metrics[key]) }))
    .filter((item) => item.value);
  const shapTop = diagnostics.shap_top ?? [];
  const maxShap = Math.max(...shapTop.map((item) => item.mean_abs_shap), 0);
  const counts = decisionCounts(decisions);
  const exports = artifacts.filter((item) => item.kind === "analysis" || item.kind === "spatial" || item.kind === "geojson");
  const reportArtifacts = artifacts.filter((item) => item.kind === "report" && item.mime === "application/pdf");
  const latestReport = reportArtifacts[0];
  const policyDocuments = workspace?.policy_documents ?? [];
  const canGenerateReport = decisions.length > 0 && policyDocuments.length > 0 && !!selectedModelId;

  const toggle = (key: DirectoryKey) => {
    setExpandedKey((current) => (current === key ? null : key));
  };

  return (
    <aside className={`analysis-sidebar ${open ? "open" : ""}`}>
      <header className="analysis-header">
        <div>
          <span className="eyebrow">FUNCTIONS</span>
          <h2>功能目录</h2>
        </div>
        <button className="icon-button" onClick={onClose} title="收起功能区"><X size={18} /></button>
      </header>

      <div className="analysis-scroll">
        {DIRECTORY_ITEMS.map((item) => {
          const Icon = item.icon;
          const isOpen = expandedKey === item.key;
          const subtitle = item.key === "report" && policyDocuments.length
            ? `已加载 ${policyDocuments.length} 份参考文档`
            : item.subtitle;
          return (
            <section className={`directory-item ${isOpen ? "open" : ""}`} key={item.key}>
              <button
                className="directory-header"
                onClick={() => toggle(item.key)}
                aria-expanded={isOpen}
              >
                <Icon size={16} />
                <div className="directory-header-copy">
                  <strong>{item.title}</strong>
                  <small>{subtitle}</small>
                </div>
                <ChevronDown size={15} className="directory-chevron" />
              </button>

              {isOpen && item.key === "analysis" && (
                <div className="directory-body">
                  <p className="analysis-note">调用已有 XGBoost 模型、动态适配指数和空间约束规则，识别更可能调整的候选地块。</p>

                  <section className="analysis-card">
                    <h3>模型状态</h3>
                    <div className="status-table">
                      <div><span>XGBoost</span><strong>{diagnostics.available ? "已加载" : "未加载"}</strong></div>
                      <div><span>SHAP</span><strong>{diagnostics.shap_available ? "可用" : "暂无结果"}</strong></div>
                      {typeof diagnostics.feature_count === "number" && (
                        <div><span>模型特征</span><strong>{diagnostics.feature_count} 个</strong></div>
                      )}
                    </div>
                    {diagnostics.warning && <p className="analysis-warning">{diagnostics.warning}</p>}
                  </section>

                  <section className="analysis-card">
                    <h3>模型评估</h3>
                    {metricRows.length ? (
                      <div className="metric-list">
                        {metricRows.map((item) => (
                          <div key={item.key}>
                            <span>{item.label}</span>
                            <strong>{item.value}</strong>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="analysis-empty">暂无模型评估结果</p>
                    )}
                  </section>

                  <section className="analysis-card">
                    <h3>SHAP关键因素</h3>
                    {shapTop.length ? (
                      <div className="shap-list">
                        {shapTop.map((item) => (
                          <div key={item.feature} className="shap-row">
                            <div>
                              <span>{item.feature}</span>
                              <strong>{item.mean_abs_shap.toFixed(3)}</strong>
                            </div>
                            <i style={{ width: `${maxShap ? (item.mean_abs_shap / maxShap) * 100 : 0}%` }} />
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="analysis-empty">暂无 SHAP 关键因素</p>
                    )}
                  </section>

                  {decisions.length > 0 && (
                    <section className="analysis-card">
                      <h3>分析结果</h3>
                      <div className="summary-grid">
                        <div><span>分析街区</span><strong>{decisions.length}</strong></div>
                        <div><span>一级优先调入</span><strong>{counts["一级优先调入"] ?? 0}</strong></div>
                        <div><span>二级优先调入</span><strong>{counts["二级优先调入"] ?? 0}</strong></div>
                        <div><span>建议调出</span><strong>{counts["建议调出"] ?? 0}</strong></div>
                        <div><span>人工复核</span><strong>{counts["人工复核"] ?? 0}</strong></div>
                      </div>
                    </section>
                  )}

                  {exports.length > 0 && (
                    <section className="analysis-card export-card">
                      <h3>导出结果</h3>
                      {exports.slice(0, 4).map((artifact) => (
                        <a href={artifactDownloadUrl(artifact.id)} key={artifact.id}>
                          <Download size={14} />
                          <span>{artifact.kind === "analysis" ? "下载分析结果" : "下载空间结果"}</span>
                        </a>
                      ))}
                    </section>
                  )}

                  <button className="run-analysis-button" onClick={onRunAnalysis} disabled={busy || !workspace?.block_count}>
                    {busy ? <CheckCircle2 size={17} /> : <Play size={17} />}
                    <span>{busy ? "正在分析..." : decisions.length ? "重新分析候选地块" : "开始候选地块分析"}</span>
                  </button>
                  {decisions.length > 0 && <p className="analysis-done">分析完成</p>}
                </div>
              )}

              {isOpen && item.key === "report" && (
                <div className="directory-body">
                  <p className="analysis-note">系统自动引用本地政策参考文档，由大模型结合候选地块分析结果生成红头格式政策说明 PDF。</p>

                  <section className="analysis-card">
                    <h3>参考文档</h3>
                    {policyDocuments.length ? (
                      <div className="policy-doc-list">
                        {policyDocuments.map((doc) => (
                          <div key={doc.id}>
                            <strong title={doc.name}>{doc.name}</strong>
                            <span>{doc.text_length ? `本地参考库 · ${doc.text_length} 字` : "本地参考库"}</span>
                            {doc.warning && <small>{doc.warning}</small>}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="analysis-empty">暂无可用参考文档</p>
                    )}
                  </section>

                  <button
                    className="report-generate-button"
                    onClick={onGeneratePolicyReport}
                    disabled={busy || !canGenerateReport}
                    title={!decisions.length ? "请先完成候选地块分析" : !policyDocuments.length ? "暂无参考文档" : "生成报告"}
                  >
                    <FileText size={16} />
                    <span>{busy ? "正在生成..." : "生成报告"}</span>
                  </button>
                  {!canGenerateReport && <p className="report-hint">需要先完成候选地块分析，并确保本地参考文档可用。</p>}

                  {latestReport && (
                    <section className="analysis-card export-card report-output-card">
                      <h3>政策PDF</h3>
                      <a href={artifactPreviewUrl(latestReport.id)} target="_blank" rel="noreferrer">
                        <Eye size={14} />
                        <span>预览PDF</span>
                      </a>
                      <a href={artifactDownloadUrl(latestReport.id)}>
                        <Download size={14} />
                        <span>下载PDF</span>
                      </a>
                    </section>
                  )}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </aside>
  );
}
