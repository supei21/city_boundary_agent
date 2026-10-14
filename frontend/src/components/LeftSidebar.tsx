import { useRef } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Database,
  Layers,
  RefreshCcw,
  ShieldCheck,
  Upload,
} from "lucide-react";

import type { WorkspaceSummary } from "../types";

interface Props {
  collapsed: boolean;
  workspace: WorkspaceSummary | null;
  busy: boolean;
  selectedModelId: string;
  onToggle: () => void;
  onUpload: (file: File) => void;
  onUploadConstraint: (file: File) => void;
  onReset: () => void;
}

function StatusDot({ active, partial = false }: { active: boolean; partial?: boolean }) {
  return <span className={`dataset-dot ${active ? "loaded" : partial ? "partial" : ""}`} />;
}

export function LeftSidebar({
  collapsed,
  workspace,
  busy,
  selectedModelId,
  onToggle,
  onUpload,
  onUploadConstraint,
  onReset,
}: Props) {
  const uploadRef = useRef<HTMLInputElement>(null);
  const constraintUploadRef = useRef<HTMLInputElement>(null);
  const hasBlocks = (workspace?.block_count ?? 0) > 0;
  const hasGeometry = !!workspace?.has_spatial_data;
  const constraintDataset = workspace?.constraint_dataset;
  const hasConstraints = !!constraintDataset?.loaded;
  const constraintWarnings = constraintDataset?.warnings ?? [];
  const landscapeBoundary = workspace?.landscape_boundary;
  const activeModel = workspace?.models.find((model) => model.id === selectedModelId);
  const isLlmModel = activeModel?.kind === "llm";

  return (
    <aside className={`left-sidebar ${collapsed ? "collapsed" : ""}`}>
      <div className="sidebar-brand">
        <div className="brand-mark">AI</div>
        {!collapsed && (
          <div className="brand-copy">
            <strong>数据管理</strong>
            <span>Boundary Agent</span>
          </div>
        )}
        <button className="icon-button sidebar-toggle" onClick={onToggle} title={collapsed ? "展开功能区" : "收起功能区"}>
          {collapsed ? <ChevronRight size={17} /> : <ChevronLeft size={17} />}
        </button>
      </div>

      <div className="sidebar-scroll">
        <section className="sidebar-section">
          {!collapsed && <h2>数据上传</h2>}
          <button className="sidebar-action primary" onClick={() => uploadRef.current?.click()} disabled={busy} title="上传街区属性数据">
            <Upload size={17} />
            {!collapsed && <span>上传空间数据</span>}
          </button>
          <button className="sidebar-action secondary" onClick={() => constraintUploadRef.current?.click()} disabled={busy} title="上传三线约束数据">
            <ShieldCheck size={17} />
            {!collapsed && <span>上传约束数据</span>}
          </button>
          {!collapsed && <p className="sidebar-hint">候选地块：SHP / ZIP / GeoJSON / CSV；约束数据：SHP ZIP / GeoJSON</p>}
          <input
            ref={uploadRef}
            type="file"
            accept=".json,.csv,.zip,application/json,text/csv,application/zip"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onUpload(file);
              event.target.value = "";
            }}
          />
          <input
            ref={constraintUploadRef}
            type="file"
            accept=".zip,.json,.geojson,application/zip,application/json"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onUploadConstraint(file);
              event.target.value = "";
            }}
          />
        </section>

        <section className="sidebar-section">
          {!collapsed && <h2>当前数据集</h2>}
          {!collapsed && workspace && (
            <div className="dataset-summary">
              <span className="eyebrow">街区数据</span>
              <strong title={workspace.dataset_name}>{workspace.dataset_name || "未加载"}</strong>
              <div className="dataset-metrics">
                <span>{workspace.block_count} 个街区</span>
                <span>{workspace.field_count} 个字段</span>
              </div>
              <div className="dataset-state"><StatusDot active={hasBlocks} />{hasBlocks ? "数据已加载" : "未加载"}</div>
              {workspace.dataset_warnings.length > 0 && (
                <details className="dataset-warning">
                  <summary>{workspace.dataset_warnings.length} 项数据提示</summary>
                  {workspace.dataset_warnings.map((warning) => <p key={warning}>{warning}</p>)}
                </details>
              )}
              {hasConstraints && (
                <div className="constraint-summary">
                  <span className="eyebrow">三线约束数据</span>
                  <strong title={constraintDataset?.name ?? ""}>{constraintDataset?.name}</strong>
                  <small>{constraintDataset?.layer_count ?? 0} 个约束图层</small>
                </div>
              )}
              {constraintWarnings.length > 0 && (
                <details className="dataset-warning">
                  <summary>{constraintWarnings.length} 项约束数据提示</summary>
                  {constraintWarnings.map((warning) => <p key={warning}>{warning}</p>)}
                </details>
              )}
              {landscapeBoundary?.loaded && (
                <div className="constraint-summary">
                  <span className="eyebrow">形态评价基准边界</span>
                  <strong title={landscapeBoundary.name ?? ""}>{landscapeBoundary.name}</strong>
                  <small>{landscapeBoundary.patch_count} 个斑块 · {landscapeBoundary.area_km2.toFixed(1)} km²</small>
                </div>
              )}
            </div>
          )}
        </section>

        <section className="sidebar-section">
          {!collapsed && <h2>数据集</h2>}
          {collapsed ? (
            <button className="sidebar-action" title="数据集">
              <Layers size={17} />
            </button>
          ) : (
            <div className="dataset-list">
              <div><StatusDot active={hasBlocks} /><span>街区基础数据</span><em>{hasBlocks ? "已加载" : "未加载"}</em></div>
              <div><StatusDot active={hasGeometry} /><span>空间几何</span><em>{hasGeometry ? "已加载" : "未加载"}</em></div>
              <div><StatusDot active={!!landscapeBoundary?.loaded} /><span>城镇开发边界</span><em>{landscapeBoundary?.loaded ? "形态评价可用" : "未加载"}</em></div>
              <div><StatusDot active={!!constraintDataset?.layers.includes("生态保护红线")} partial={hasConstraints} /><span>生态保护红线</span><em>{constraintDataset?.layers.includes("生态保护红线") ? "已加载" : hasConstraints ? "待识别" : "未加载"}</em></div>
              <div><StatusDot active={!!constraintDataset?.layers.includes("永久基本农田")} partial={hasConstraints} /><span>永久基本农田</span><em>{constraintDataset?.layers.includes("永久基本农田") ? "已加载" : hasConstraints ? "待识别" : "未加载"}</em></div>
              <div><StatusDot active={false} /><span>地质灾害数据</span><em>未单独加载</em></div>
            </div>
          )}
        </section>

        <section className="sidebar-section">
          {!collapsed && <h2>数据状态</h2>}
          {collapsed ? (
            <button className="sidebar-action" title="数据状态">
              <Database size={17} />
            </button>
          ) : (
            <div className="data-status-list">
              <div><span>街区数据</span><strong>{hasBlocks ? "已加载" : "未加载"}</strong></div>
              <div><span>空间几何</span><strong>{hasGeometry ? "已加载" : "未加载"}</strong></div>
              <div><span>约束数据</span><strong>{hasConstraints ? `${constraintDataset?.layer_count ?? 0} 图层` : "未加载"}</strong></div>
              <div><span>形态评价</span><strong>{landscapeBoundary?.loaded ? "边界已就绪" : "未就绪"}</strong></div>
              <div><span>模型特征</span><strong>{workspace?.model_diagnostics?.available ? "可读取" : "未就绪"}</strong></div>
            </div>
          )}
        </section>
      </div>

      <div className="sidebar-footer">
        {!collapsed && (
          <div className="model-status" title={activeModel?.description}>
            <span className={`status-dot ${isLlmModel ? "ready" : ""}`} />
            {isLlmModel ? (
              <div className="model-status-copy">
                <strong>{activeModel.label}</strong>
                {activeModel.base_url ? <small>{activeModel.base_url}</small> : null}
              </div>
            ) : (
              <span>未调用大模型（本地规则）</span>
            )}
          </div>
        )}
        <button className="sidebar-action quiet" onClick={onReset} disabled={busy} title="重置工作区">
          <RefreshCcw size={16} />
          {!collapsed && <span>重置工作区</span>}
        </button>
      </div>
    </aside>
  );
}
