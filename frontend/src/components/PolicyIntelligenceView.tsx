import { Bot, Download, FileSearch, FlaskConical, ShieldCheck, Upload } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";

import { analyzePolicyFeasibility, askRagQuestion, getCandidateParcels, validatePolicyFeasibilityBatch } from "../api";
import type { CandidateParcelPayload, PolicyBatchValidationResponse, PolicyFeasibilityResponse, RagQuestionResponse } from "../types";

export type PolicyFeatureMode = "qa" | "feasibility";

interface Props { modelId: string; mode: PolicyFeatureMode; }

const FEATURE_TITLES: Record<PolicyFeatureMode, string> = {
  qa: "政策问答",
  feasibility: "候选地块政策可行性分析",
};

const SAMPLE_PARCEL = `{
  "parcel_id": "P001",
  "block_id": "P001",
  "proposed_action": "调入",
  "land_type": "一般农用地",
  "project_type": "产业建设项目",
  "ecological_redline": false,
  "permanent_farmland": false,
  "prohibited_disaster_zone": false,
  "planning_compliance": true
}`;

function percent(value: number | null) {
  return value === null ? "N/A" : `${(value * 100).toFixed(1)}%`;
}

interface ConstraintItem { constraint_id?: string; name?: string; reason?: string; }

export function PolicyIntelligenceView({ modelId, mode }: Props) {
  const [question, setQuestion] = useState("");
  const [questionResult, setQuestionResult] = useState<RagQuestionResponse | null>(null);
  const [parcelJson, setParcelJson] = useState(SAMPLE_PARCEL);
  const [feasibility, setFeasibility] = useState<PolicyFeasibilityResponse | null>(null);
  const [candidates, setCandidates] = useState<CandidateParcelPayload | null>(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [batchCases, setBatchCases] = useState<Record<string, unknown>[]>([]);
  const [batchFileName, setBatchFileName] = useState("");
  const [batchResult, setBatchResult] = useState<PolicyBatchValidationResponse | null>(null);
  const [asking, setAsking] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [batching, setBatching] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (mode !== "feasibility") return;
    getCandidateParcels()
      .then(setCandidates)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [mode]);

  const triggeredConstraints = useMemo(() => {
    const value = feasibility?.hard_constraint_analysis.triggered_constraints;
    return Array.isArray(value) ? value as ConstraintItem[] : [];
  }, [feasibility]);

  const selectCandidate = (blockId: string) => {
    setSelectedCandidateId(blockId);
    const candidate = candidates?.parcels.find((item) => String(item.block_id ?? "") === blockId);
    if (!candidate) return;
    const insideBoundary = candidate.inside_boundary === true;
    const proposedAction = insideBoundary ? "调出" : "调入";
    setParcelJson(JSON.stringify({
      ...candidate,
      parcel_id: blockId,
      block_id: blockId,
      location: candidate.block_name ?? candidate.location ?? "",
      land_type: candidate.dominant_landuse ?? candidate.dominant_l ?? candidate.land_type ?? "",
      proposed_action: proposedAction,
    }, null, 2));
    setFeasibility(null);
  };

  const ask = async () => {
    if (!question.trim()) return;
    setAsking(true); setError(""); setQuestionResult(null);
    try { setQuestionResult(await askRagQuestion(question.trim(), modelId)); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setAsking(false); }
  };

  const analyze = async () => {
    setAnalyzing(true); setError(""); setFeasibility(null);
    try {
      setFeasibility(await analyzePolicyFeasibility(JSON.parse(parcelJson) as Record<string, unknown>, modelId));
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setAnalyzing(false); }
  };

  const loadBatchFile = async (file?: File) => {
    if (!file) return;
    setError(""); setBatchResult(null);
    try {
      const payload = JSON.parse(await file.text()) as { test_cases?: Record<string, unknown>[] } | Record<string, unknown>[];
      const cases = Array.isArray(payload) ? payload : payload.test_cases;
      if (!Array.isArray(cases) || cases.length === 0) throw new Error("JSON中缺少非空的 test_cases 数组");
      setBatchCases(cases); setBatchFileName(file.name);
    } catch (err) {
      setBatchCases([]); setBatchFileName("");
      setError(`批量文件解析失败：${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const runBatch = async () => {
    if (!batchCases.length) return;
    setBatching(true); setError(""); setBatchResult(null);
    try { setBatchResult(await validatePolicyFeasibilityBatch(batchCases, modelId)); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBatching(false); }
  };

  const downloadReport = () => {
    if (!batchResult) return;
    const url = URL.createObjectURL(new Blob([batchResult.report], { type: "text/markdown;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = "candidate_policy_feasibility_report.md"; anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className={`policy-intelligence-view mode-${mode}`}>
      <header className="policy-intelligence-header">
        <h2 className="feature-page-title">{FEATURE_TITLES[mode]}</h2>
        <div><span className="eyebrow">POLICY INTELLIGENCE</span><h2>{FEATURE_TITLES[mode]}</h2></div>
        <span>当前回答模型：{modelId}</span>
      </header>
      {error && <div className="policy-intelligence-error">{error}</div>}

      {mode === "qa" && <div className="policy-intelligence-grid single-feature-grid">
        <section className="policy-intelligence-card">
          <div className="policy-intelligence-title"><Bot size={18} /><div><h3>政策问答</h3><p>只检索 Chroma 中的政策原文，再由大模型组织回答</p></div></div>
          <div className="policy-question-row">
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="请输入政策问题，例如：城镇开发边界调整需要满足哪些条件？" />
            <button type="button" onClick={() => void ask()} disabled={asking || !question.trim()}>{asking ? "正在检索…" : "开始问答"}</button>
          </div>
          {questionResult && <article className="policy-answer">
            <div className="policy-answer-meta"><span>{questionResult.mode}</span><span>相关度 {questionResult.best_score.toFixed(3)}</span></div>
            <ReactMarkdown>{questionResult.answer}</ReactMarkdown>
            <details><summary><FileSearch size={13} /> 查看政策原文证据（{questionResult.sources.length}）</summary>
              {questionResult.sources.map((source, index) => <div className="policy-source" key={`${source.source}-${index}`}><strong>{source.source}</strong><small>相关度 {source.score.toFixed(3)}</small><p>{source.excerpt}</p></div>)}
            </details>
          </article>}
        </section>
      </div>}

      {mode === "feasibility" && <div className="policy-intelligence-grid single-feature-grid">
        <section className="policy-intelligence-card">
          <div className="policy-intelligence-title"><ShieldCheck size={18} /><div><h3>单地块分析</h3><p>候选地块属性 + 少量硬约束 + Chroma政策原文 + 大模型结构化判断</p></div></div>
          <div className="candidate-policy-picker">
            <label htmlFor="candidate-policy-block">从最近一次分析结果中选择候选地块</label>
            <select id="candidate-policy-block" value={selectedCandidateId} onChange={(event) => selectCandidate(event.target.value)}>
              <option value="">请选择候选地块</option>
              {(candidates?.parcels ?? []).map((item) => <option key={String(item.block_id)} value={String(item.block_id)}>
                {String(item.block_id)} ｜ {String(item.block_name ?? "未命名")} ｜ {String(item.candidate_decision ?? "")}
              </option>)}
            </select>
            {candidates?.analysis_required && <p>请先运行“候选地块分析”，再进行政策可行性分析。</p>}
            {candidates && !candidates.analysis_required && <p>已关联 {candidates.count} 个候选地块及其多源属性。</p>}
          </div>
          <textarea className="parcel-editor" value={parcelJson} onChange={(event) => setParcelJson(event.target.value)} spellCheck={false} />
          <button type="button" onClick={() => void analyze()} disabled={analyzing}>{analyzing ? "正在分析…" : "开始政策可行性分析"}</button>

          {feasibility?.parcel_context?.groups && <section className="parcel-context-summary">
            <h4>本次实际使用的候选地块属性</h4>
            {Object.entries(feasibility.parcel_context.groups).map(([group, items]) => <div key={group}><strong>{group}</strong><p>{items.map((item) => `${item.label}：${String(item.value)}${item.unit}`).join("；")}</p></div>)}
          </section>}

          {feasibility && <article className="policy-answer">
            <div className="policy-answer-meta"><span>{String(feasibility.retrieval.mode ?? "政策可行性分析")}</span><span>版本 v{feasibility.tool_version}</span></div>
            {triggeredConstraints.length > 0 && <div className="policy-intelligence-error">已触发硬约束：{triggeredConstraints.map((item) => item.name).join("、")}</div>}
            <ReactMarkdown>{feasibility.answer}</ReactMarkdown>
            <details><summary>查看完整结构化结果</summary><pre>{JSON.stringify(feasibility, null, 2)}</pre></details>
          </article>}
        </section>

        <section className="policy-intelligence-card policy-batch-card">
          <div className="policy-intelligence-title"><FlaskConical size={18} /><div><h3>多地块批量分析</h3><p>上传候选地块 JSON，逐地块执行相同的政策可行性流程</p></div></div>
          <label className="policy-upload-button"><Upload size={15} /> 选择 test_cases.json
            <input type="file" accept=".json,application/json" onChange={(event) => void loadBatchFile(event.target.files?.[0])} />
          </label>
          {batchCases.length > 0 && <div className="policy-batch-file"><strong>{batchFileName}</strong><span>已读取 {batchCases.length} 个候选地块</span></div>}
          <button type="button" onClick={() => void runBatch()} disabled={batching || !batchCases.length}>{batching ? `正在分析 ${batchCases.length} 个地块…` : "执行多地块政策可行性分析"}</button>
          {batchResult && <article className="policy-answer">
            <div className="policy-batch-metrics"><div><strong>{percent(batchResult.pass_rate)}</strong><span>分析成功率</span></div><div><strong>{percent(batchResult.gate_match_rate)}</strong><span>预期匹配率</span></div><div><strong>{percent(batchResult.evidence_valid_rate)}</strong><span>政策证据率</span></div><div><strong>{percent(batchResult.input_valid_rate)}</strong><span>输入有效率</span></div></div>
            <div className="policy-batch-results">{batchResult.results.map((item, index) => <details key={index}><summary className={item.passed ? "batch-pass" : "batch-fail"}>{item.passed ? "PASS" : "FAIL"} ｜ {String(item.id ?? "")} ｜ {String(item.actual_policy_state ?? "")}</summary><pre>{JSON.stringify(item, null, 2)}</pre></details>)}</div>
            <button type="button" className="policy-report-download" onClick={downloadReport}><Download size={14} /> 下载批量分析报告</button>
          </article>}
        </section>
      </div>}
    </section>
  );
}
