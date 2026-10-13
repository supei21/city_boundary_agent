import { lazy, Suspense } from "react";
import type { FeatureCollection } from "geojson";
import { Map } from "lucide-react";
import type { LandscapeEvaluation, LandscapeScenarioEvaluation } from "../types";

const SpatialMap = lazy(() => import("./SpatialMap").then((module) => ({ default: module.SpatialMap })));

interface Props {
  data: FeatureCollection | null;
  boundaryData: FeatureCollection | null;
  landscapeEvaluation: LandscapeEvaluation | null;
  landscapeScenarioEvaluation: LandscapeScenarioEvaluation | null;
  busy: boolean;
  onRunScenario: (blockIds: string[], action: "include" | "exclude") => void;
  onClearScenario: () => void;
}

export function MapView({
  data,
  boundaryData,
  landscapeEvaluation,
  landscapeScenarioEvaluation,
  busy,
  onRunScenario,
  onClearScenario,
}: Props) {
  const metadata = data && typeof (data as FeatureCollection & { metadata?: unknown }).metadata === "object"
    ? (data as FeatureCollection & { metadata: Record<string, unknown> }).metadata
    : null;

  if (!data) {
    return (
      <div className="preview-empty">
        <Map size={28} />
        <strong>暂无可显示的地图</strong>
        <p>上传 SHP ZIP 数据后即可在这里查看地块空间分布。</p>
      </div>
    );
  }
  return (
    <div className="map-tab">
      {metadata?.truncated === true && (
        <p className="map-preview-note">
          地图优先显示候选地块，共显示 {String(metadata.displayed_features)} / {String(metadata.total_features)} 个；分析和导出仍使用完整数据。
        </p>
      )}
      <Suspense fallback={<div className="preview-loading">正在加载地图组件…</div>}>
        <SpatialMap
          data={data}
          boundaryData={boundaryData}
          landscapeEvaluation={landscapeEvaluation}
          landscapeScenarioEvaluation={landscapeScenarioEvaluation}
          busy={busy}
          onRunScenario={onRunScenario}
          onClearScenario={onClearScenario}
        />
      </Suspense>
    </div>
  );
}
