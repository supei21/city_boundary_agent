import { lazy, Suspense, useEffect, useState } from "react";
import type { FeatureCollection } from "geojson";
import { FileCode2, Map, TableProperties, X } from "lucide-react";

import { extractRows, extractSpatial, isRecord } from "../previewUtils";
import type { ArtifactDetail, ArtifactSummary, Decision } from "../types";
import { AttributeTable } from "./AttributeTable";
import { DecisionTable } from "./DecisionTable";

const SpatialMap = lazy(() => import("./SpatialMap").then((module) => ({ default: module.SpatialMap })));

interface Props {
  open: boolean;
  artifacts: ArtifactSummary[];
  selected: ArtifactSummary | null;
  detail: ArtifactDetail | null;
  onSelect: (artifact: ArtifactSummary) => void;
  onClose: () => void;
}

export function RightPreview({ open, artifacts, selected, detail, onSelect, onClose }: Props) {
  const [activeTab, setActiveTab] = useState<"map" | "attributes">("map");
  const rows = extractRows(detail?.preview);
  const spatial = extractSpatial(detail?.preview);
  const spatialMetadata = spatial && isRecord((spatial as FeatureCollection & { metadata?: unknown }).metadata)
    ? (spatial as FeatureCollection & { metadata: Record<string, unknown> }).metadata
    : null;
  const analysisDecisions = isRecord(detail?.preview) && Array.isArray(detail.preview.decisions)
    ? detail.preview.decisions as Decision[]
    : [];

  useEffect(() => {
    setActiveTab(spatial ? "map" : "attributes");
  }, [selected?.id, spatial]);

  return (
    <aside className={`right-preview ${open ? "open" : ""}`}>
      <header className="preview-header">
        <div>
          <span className="eyebrow">ARTIFACTS</span>
          <h2>可视化预览</h2>
        </div>
        <button className="icon-button" onClick={onClose} title="关闭预览"><X size={18} /></button>
      </header>

      {artifacts.length > 1 && (
        <div className="artifact-switcher">
          <label htmlFor="artifact-select">切换文件</label>
          <select
            id="artifact-select"
            value={selected?.id ?? ""}
            onChange={(event) => {
              const artifact = artifacts.find((item) => item.id === event.target.value);
              if (artifact) onSelect(artifact);
            }}
          >
            <option value="" disabled>选择成果文件</option>
            {artifacts.map((artifact) => <option value={artifact.id} key={artifact.id}>{artifact.name}</option>)}
          </select>
        </div>
      )}

      {!selected ? (
        <div className="preview-empty">
          <FileCode2 size={28} />
          <strong>还没有打开文件</strong>
          <p>上传数据或完成一次分析后，点击左侧成果文件即可在这里查看。</p>
        </div>
      ) : (
        <>
          <div className="preview-file-bar">
            <div>
              <strong>{selected.name}</strong>
              <span>{selected.kind.toUpperCase()} · {(selected.size / 1024).toFixed(1)} KB</span>
            </div>
          </div>
          <div className="preview-scroll">
            {!detail ? (
              <div className="preview-loading">正在读取文件…</div>
            ) : (
              <>
                <div className="preview-tabs" role="tablist" aria-label="预览方式">
                  {spatial && (
                    <button className={activeTab === "map" ? "active" : ""} onClick={() => setActiveTab("map")} role="tab">
                      <Map size={13} />地图
                    </button>
                  )}
                  <button className={activeTab === "attributes" ? "active" : ""} onClick={() => setActiveTab("attributes")} role="tab">
                    <TableProperties size={13} />属性
                  </button>
                </div>
                {activeTab === "map" && spatial && (
                  <>
                    <section className="preview-section map-section">
                      {spatialMetadata?.truncated === true && (
                        <p className="map-preview-note">
                          地图优先显示候选地块，共显示 {String(spatialMetadata.displayed_features)} / {String(spatialMetadata.total_features)} 个；分析和导出仍使用完整数据。
                        </p>
                      )}
                      <Suspense fallback={<div className="preview-loading">正在加载地图组件…</div>}>
                        <SpatialMap data={spatial} />
                      </Suspense>
                    </section>
                    {analysisDecisions.length > 0 && (
                      <section className="preview-section">
                        <h3>分析结果</h3>
                        <DecisionTable decisions={analysisDecisions} />
                      </section>
                    )}
                  </>
                )}
                {activeTab === "attributes" && (
                  <section className="preview-section">
                    <h3>地块属性</h3>
                    {rows.length ? <AttributeTable rows={rows} compact /> : <p className="preview-inline-empty">当前文件没有可显示的属性记录</p>}
                  </section>
                )}
              </>
            )}
          </div>
        </>
      )}
    </aside>
  );
}
