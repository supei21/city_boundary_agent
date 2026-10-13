import { useEffect, useMemo, useRef, useState } from "react";
import type { FeatureCollection } from "geojson";
import maplibregl, { LngLatBounds } from "maplibre-gl";
import type { LandscapeEvaluation, LandscapeResult, LandscapeScenarioEvaluation } from "../types";
import { LandscapeMetricDetail } from "./LandscapeMetricDetail";
import { LandscapeScenarioDetail } from "./LandscapeScenarioDetail";


interface Props {
  data: FeatureCollection;
  boundaryData?: FeatureCollection | null;
  landscapeEvaluation?: LandscapeEvaluation | null;
  landscapeScenarioEvaluation?: LandscapeScenarioEvaluation | null;
  busy?: boolean;
  onRunScenario?: (blockIds: string[], action: "include" | "exclude") => void;
  onClearScenario?: () => void;
}

type FeatureProperties = Record<string, unknown>;
type DecisionFilter = "全部" | "形态评价" | "一级优先调入" | "二级优先调入" | "建议调出" | "内部更新" | "人工复核";
type MapMode = "potential" | "decision";

const FILTER_OPTIONS: DecisionFilter[] = [
  "全部",
  "形态评价",
  "一级优先调入",
  "二级优先调入",
  "建议调出",
  "内部更新",
  "人工复核",
];

const DETAIL_FIELDS: Array<[string, string]> = [
  ["boundary_name", "边界名称"],
  ["patch_count", "边界斑块数"],
  ["area_km2", "边界面积（km²）"],
  ["source_crs", "源坐标系"],
  ["block_id", "地块编号"],
  ["block_name", "行政区/名称"],
  ["ai_decision", "智能体判断"],
  ["development_potential", "综合发展潜力"],
  ["ai_dynamic_score", "动态适配分"],
  ["ai_suitability", "空间适宜性"],
  ["ai_development_probability", "开发概率"],
  ["ai_planning_match", "规划匹配度"],
  ["ai_constraint_cost", "约束成本"],
  ["ai_rank_city", "全市排名"],
  ["ai_rank_district", "区内排名"],
  ["ai_recommended_use", "推荐用途"],
  ["ai_key_drivers", "主要驱动因素"],
  ["ai_constraint_status", "约束状态"],
  ["ai_policy_compliance", "政策合规"],
  ["ai_policy_reason", "政策判读依据"],
  ["ai_policy_requirements", "政策待补条件"],
  ["ai_confidence", "置信度"],
  ["ai_recommendation", "建议"],
];

function extendBounds(bounds: LngLatBounds, coordinates: unknown): void {
  if (!Array.isArray(coordinates)) return;
  if (
    coordinates.length >= 2
    && typeof coordinates[0] === "number"
    && typeof coordinates[1] === "number"
  ) {
    bounds.extend([coordinates[0], coordinates[1]]);
    return;
  }
  coordinates.forEach((value) => extendBounds(bounds, value));
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}

const EMPTY_FEATURE_COLLECTION: FeatureCollection = { type: "FeatureCollection", features: [] };

export function SpatialMap({
  data,
  boundaryData = null,
  landscapeEvaluation = null,
  landscapeScenarioEvaluation = null,
  busy = false,
  onRunScenario,
  onClearScenario,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [selected, setSelected] = useState<FeatureProperties | null>(null);
  const [selectedLandscape, setSelectedLandscape] = useState<LandscapeResult | null>(null);
  const [mode, setMode] = useState<MapMode>("potential");
  const [filter, setFilter] = useState<DecisionFilter>("全部");
  const [scenarioSelecting, setScenarioSelecting] = useState(false);
  const [scenarioAction, setScenarioAction] = useState<"include" | "exclude">("include");
  const [selectedScenarioIds, setSelectedScenarioIds] = useState<string[]>([]);
  const scenarioSelectingRef = useRef(scenarioSelecting);
  const scenarioActionRef = useRef(scenarioAction);
  const selectedScenarioIdsRef = useRef(selectedScenarioIds);
  const isBoundaryMode = mode === "decision" && filter === "形态评价";
  const landscapeData = landscapeEvaluation?.spatial ?? EMPTY_FEATURE_COLLECTION;
  const evaluationByBlockId = useMemo(() => new Map(
    (landscapeEvaluation?.results ?? []).map((item) => [item.block_id, item]),
  ), [landscapeEvaluation]);
  const filteredData = useMemo<FeatureCollection>(() => {
    if (isBoundaryMode) {
      return {
        ...data,
        features: data.features.filter((feature) => (
          ["一级优先调入", "二级优先调入", "建议调出"].includes(String(feature.properties?.ai_decision ?? ""))
        )),
      };
    }
    return {
      ...data,
      features: mode === "potential" || filter === "全部"
        ? data.features
        : data.features.filter((feature) => feature.properties?.ai_decision === filter),
    };
  }, [data, filter, isBoundaryMode, mode]);

  const selectedScenarioData = useMemo<FeatureCollection>(() => ({
    type: "FeatureCollection",
    features: filteredData.features.filter((feature) => (
      selectedScenarioIds.includes(String(feature.properties?.block_id ?? ""))
    )),
  }), [filteredData.features, selectedScenarioIds]);

  const selectableScenarioIds = useMemo(() => Array.from(new Set(
    filteredData.features
      .filter((feature) => {
        const decision = String(feature.properties?.ai_decision ?? "");
        return scenarioAction === "include"
          ? ["一级优先调入", "二级优先调入"].includes(decision)
          : decision === "建议调出";
      })
      .map((feature) => String(feature.properties?.block_id ?? ""))
      .filter(Boolean),
  )), [filteredData.features, scenarioAction]);

  useEffect(() => { scenarioSelectingRef.current = scenarioSelecting; }, [scenarioSelecting]);
  useEffect(() => { scenarioActionRef.current = scenarioAction; }, [scenarioAction]);
  useEffect(() => { selectedScenarioIdsRef.current = selectedScenarioIds; }, [selectedScenarioIds]);

  useEffect(() => {
    const source = mapRef.current?.getSource("scenario-selection") as maplibregl.GeoJSONSource | undefined;
    source?.setData(selectedScenarioData);
  }, [selectedScenarioData]);

  useEffect(() => {
    const boundaryFeatures = isBoundaryMode ? (boundaryData?.features ?? []) : [];
    const displayedFeatures = [...boundaryFeatures, ...filteredData.features];
    if (!containerRef.current || !displayedFeatures.length) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "© OpenStreetMap contributors",
          },
        },
        layers: [
          { id: "background", type: "background", paint: { "background-color": "#eef1ee" } },
          { id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": 0.72 } },
        ],
      },
      center: [114.3, 30.6],
      zoom: 8,
      attributionControl: false,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");

    map.on("load", () => {
      if (boundaryFeatures.length && boundaryData) {
        map.addSource("boundary", { type: "geojson", data: boundaryData });
        map.addLayer({
          id: "boundary-fill",
          type: "fill",
          source: "boundary",
          paint: { "fill-color": "#176c50", "fill-opacity": 0.14 },
        });
        map.addLayer({
          id: "boundary-line",
          type: "line",
          source: "boundary",
          paint: { "line-color": "#0d513d", "line-width": 2.6 },
        });
      }

      if (filteredData.features.length) {
        map.addSource("blocks", { type: "geojson", data: filteredData });
        map.addLayer({
          id: "blocks-fill",
          type: "fill",
          source: "blocks",
          paint: {
            "fill-color": mode === "potential"
              ? [
                "interpolate", ["linear"],
                [
                  "coalesce",
                  ["to-number", ["get", "development_potential"]],
                  ["/", ["to-number", ["get", "ai_development_probability"]], 100],
                  0,
                ],
                0, "#440154", 0.25, "#3b528b", 0.5, "#21918c", 0.75, "#5ec962", 1, "#fde725",
              ]
              : [
                "case",
                ["==", ["get", "layer_type"], "landscape_adjustment"],
                ["match", ["get", "landscape_action"], "include", "#16835f", "exclude", "#c64c4c", "#7d9186"],
                [
                  "match", ["get", "ai_decision"],
                  "一级优先调入", "#00a878", "二级优先调入", "#42c997",
                  "建议调出", "#e3485b", "内部更新", "#3277a8",
                  "人工复核", "#d39a35", "#c8cfcb",
                ],
              ],
            "fill-opacity": mode === "potential"
              ? 0.72
              : [
                "case",
                ["==", ["get", "layer_type"], "landscape_adjustment"], 0.82,
                ["boolean", ["get", "is_candidate"], false], 0.8,
                ["boolean", ["get", "is_selected"], false], 0.42,
                0.08,
              ],
          },
        });
        if (mode === "decision") {
          map.addLayer({
            id: "blocks-halo",
            type: "line",
            source: "blocks",
            paint: {
              "line-color": [
                "case",
                ["==", ["get", "layer_type"], "landscape_adjustment"],
                ["match", ["get", "landscape_action"], "include", "#00b77f", "exclude", "#f0445a", "#f5b642"],
                [
                  "match", ["get", "ai_decision"],
                  "一级优先调入", "#00b77f", "二级优先调入", "#52d8a5",
                  "建议调出", "#f0445a", "内部更新", "#3d8fd1",
                  "人工复核", "#e5a52e", "rgba(0,0,0,0)",
                ],
              ],
              "line-opacity": [
                "case",
                ["==", ["get", "layer_type"], "landscape_adjustment"], 0.9,
                ["boolean", ["get", "is_candidate"], false], 0.86,
                [
                  "match", ["get", "ai_decision"],
                  "一级优先调入", true, "二级优先调入", true,
                  "建议调出", true, "内部更新", true, "人工复核", true,
                  false,
                ], 0.86,
                0,
              ],
              "line-width": [
                "interpolate", ["linear"], ["zoom"],
                6, 6.5,
                10, 4.5,
                14, 2.5,
              ],
              "line-blur": 1.2,
            },
          });
        }
        map.addLayer({
          id: "blocks-line",
          type: "line",
          source: "blocks",
          paint: {
            "line-color": mode === "potential"
              ? "#263a33"
              : [
                "case",
                ["==", ["get", "layer_type"], "landscape_adjustment"],
                ["match", ["get", "landscape_action"], "include", "#0f5f43", "exclude", "#8e2f32", "#53665c"],
                ["boolean", ["get", "is_candidate"], false], "#173f32",
                "#728079",
              ],
            "line-opacity": mode === "potential" ? 0.48 : 0.75,
            "line-width": mode === "potential"
              ? 0.45
              : [
                "case",
                ["==", ["get", "layer_type"], "landscape_adjustment"], 3.2,
                ["boolean", ["get", "is_candidate"], false], 2.2,
                0.35,
              ],
          },
        });
        if (mode === "decision") {
          map.addLayer({
            id: "active-block-line",
            type: "line",
            source: "blocks",
            filter: ["==", ["get", "block_id"], "__no_active_block__"],
            paint: {
              "line-color": "#ffd84d",
              "line-width": ["interpolate", ["linear"], ["zoom"], 6, 8, 12, 6, 16, 4],
              "line-opacity": 0.98,
              "line-blur": 0.5,
            },
          });
        }
        map.addSource("scenario-selection", { type: "geojson", data: selectedScenarioData });
        map.addLayer({
          id: "scenario-selection-fill",
          type: "fill",
          source: "scenario-selection",
          paint: { "fill-color": "#f1a33c", "fill-opacity": 0.7 },
        });
        map.addLayer({
          id: "scenario-selection-line",
          type: "line",
          source: "scenario-selection",
          paint: { "line-color": "#8b4d00", "line-width": 3.5 },
        });
      }

      const bounds = new LngLatBounds();
      displayedFeatures.forEach((feature) => {
        const geometry = feature.geometry;
        if (geometry && "coordinates" in geometry) extendBounds(bounds, geometry.coordinates);
      });
      if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 34, maxZoom: 16, duration: 0 });

      const interactiveLayers = ["blocks-fill", "boundary-fill"].filter((layer) => !!map.getLayer(layer));
      map.on("mousemove", (event) => {
        const features = map.queryRenderedFeatures(event.point, { layers: interactiveLayers });
        map.getCanvas().style.cursor = features.length ? "pointer" : "";
      });
      map.on("click", (event) => {
        const feature = map.queryRenderedFeatures(event.point, { layers: interactiveLayers })[0];
        const properties = feature?.properties as FeatureProperties | undefined;
        if (!properties) return;
        const isBoundary = properties.layer_type === "urban_development_boundary";
        const blockId = isBoundary ? "" : String(properties.block_id ?? "");
        const landscapeResult = isBoundaryMode && blockId
          ? evaluationByBlockId.get(blockId) ?? null
          : null;
        setSelected(properties);
        setSelectedLandscape(landscapeResult);
        if (map.getLayer("active-block-line")) {
          map.setFilter("active-block-line", [
            "==",
            ["get", "block_id"],
            blockId || "__no_active_block__",
          ]);
        }

        if (isBoundaryMode && scenarioSelectingRef.current && blockId) {
          const decision = String(properties.ai_decision ?? "");
          const allowed = scenarioActionRef.current === "include"
            ? ["一级优先调入", "二级优先调入"].includes(decision)
            : decision === "建议调出";
          if (allowed) {
            onClearScenario?.();
            setSelectedScenarioIds((current) => (
              current.includes(blockId)
                ? current.filter((value) => value !== blockId)
                : [...current, blockId]
            ));
          }
        }

        const popupContent = document.createElement("div");
        popupContent.className = "map-popup-content";
        const title = document.createElement("strong");
        title.textContent = displayValue(
          isBoundary ? properties.boundary_name : properties.block_name || properties.block_id,
        );
        const detail = document.createElement("span");
        const scoreValue = mode === "potential"
          ? properties.development_potential
          : properties.ai_dynamic_score;
        const scoreLabel = mode === "potential" ? "综合潜力" : "动态适配";
        const score = scoreValue === undefined
          ? ""
          : ` · ${scoreLabel} ${displayValue(scoreValue)}`;
        detail.textContent = isBoundary
          ? `城镇开发边界 · ${displayValue(properties.patch_count)} 个斑块`
          : isBoundaryMode && landscapeResult
            ? `${landscapeResult.action_label} · ${landscapeResult.overall_label}`
            : `${displayValue(properties.ai_decision || "查看地块属性")}${score}`;
        popupContent.append(title, detail);
        new maplibregl.Popup({ closeButton: false, offset: 8 })
          .setLngLat(event.lngLat)
          .setDOMContent(popupContent)
          .addTo(map);
      });
    });

    const observer = new ResizeObserver(() => map.resize());
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      mapRef.current = null;
      map.remove();
    };
  }, [boundaryData, evaluationByBlockId, filteredData, isBoundaryMode, mode, onClearScenario]);

  const selectedEntries = selected
    ? DETAIL_FIELDS
      .filter(([key]) => selected[key] !== undefined && selected[key] !== null && selected[key] !== "")
      .map(([key, label]) => [label, selected[key]] as const)
    : [];

  return (
    <div className="spatial-map-view">
      <div className="map-toolbar">
        <div className="map-mode-toggle" role="group" aria-label="地图显示模式">
          <button
            type="button"
            className={mode === "potential" ? "active" : ""}
            onClick={() => {
              setMode("potential");
              setSelected(null);
              setSelectedLandscape(null);
              setScenarioSelecting(false);
              setSelectedScenarioIds([]);
              onClearScenario?.();
            }}
          >综合发展潜力</button>
          <button
            type="button"
            className={mode === "decision" ? "active" : ""}
            onClick={() => {
              setMode("decision");
              setSelected(null);
              setSelectedLandscape(null);
            }}
          >调入决策</button>
        </div>
        {mode === "decision" && (
          <>
            <label htmlFor="decision-filter">显示结果</label>
            <select
              id="decision-filter"
              value={filter}
              onChange={(event) => {
                setFilter(event.target.value as DecisionFilter);
                setSelected(null);
                setSelectedLandscape(null);
                setScenarioSelecting(false);
                setSelectedScenarioIds([]);
                onClearScenario?.();
              }}
            >
              {FILTER_OPTIONS.filter((option) => option !== "形态评价" || boundaryData).map((option) => (
                <option key={option}>{option}</option>
              ))}
            </select>
          </>
        )}
        {isBoundaryMode && (
          <button
            className={`scenario-entry-button ${scenarioSelecting ? "active" : ""}`}
            onClick={() => {
              setScenarioSelecting((value) => !value);
              setSelected(null);
              setSelectedLandscape(null);
              setSelectedScenarioIds([]);
              onClearScenario?.();
            }}
          >
            {scenarioSelecting ? "退出组合选择" : "创建组合情景"}
          </button>
        )}
        <span className="map-feature-count">
          {isBoundaryMode
            ? `${displayValue(boundaryData?.features[0]?.properties?.patch_count)} 个边界斑块 · ${filteredData.features.length} 个调入调出候选`
            : `${filteredData.features.length} 个地块`}
        </span>
      </div>
      {isBoundaryMode && scenarioSelecting && (
        <div className="scenario-toolbar">
          <select
            aria-label="组合情景动作"
            value={scenarioAction}
            onChange={(event) => {
              setScenarioAction(event.target.value as "include" | "exclude");
              setSelectedScenarioIds([]);
              onClearScenario?.();
            }}
          >
            <option value="include">选择调入地块</option>
            <option value="exclude">选择调出地块</option>
          </select>
          <strong>已选 {selectedScenarioIds.length} 个</strong>
          <button
            className="secondary"
            onClick={() => {
              setSelectedScenarioIds(selectableScenarioIds);
              onClearScenario?.();
            }}
            disabled={busy || !selectableScenarioIds.length}
            title={`选择全部${scenarioAction === "include" ? "调入" : "调出"}候选地块`}
          >全选</button>
          <button
            className="secondary"
            onClick={() => {
              setSelectedScenarioIds([]);
              onClearScenario?.();
            }}
            disabled={!selectedScenarioIds.length && !landscapeScenarioEvaluation}
          >清空</button>
          <button
            className="run"
            disabled={busy || !selectedScenarioIds.length || !onRunScenario}
            onClick={() => onRunScenario?.(selectedScenarioIds, scenarioAction)}
          >
            {busy ? "正在评价…" : "评价所选方案"}
          </button>
        </div>
      )}
      <div className="map-canvas" ref={containerRef} />
      {!isBoundaryMode && filteredData.features.length === 0 && <p className="map-empty">当前筛选条件没有地块</p>}
      {isBoundaryMode && landscapeData.features.length === 0 && (
        <p className="map-evaluation-empty">当前显示城镇开发边界及全部调入调出候选；可点击“创建组合情景”后多选地块。</p>
      )}
      {mode === "potential" ? (
        <div className="map-legend potential-legend" aria-label="综合发展潜力图例">
          <span>低 0</span><i className="potential-gradient" /><span>1 高</span>
        </div>
      ) : (
        <div className="map-legend" aria-label="地图图例">
          {isBoundaryMode ? (
          <>
            <span><i className="legend-boundary" />城镇开发边界</span>
            <span><i className="legend-primary" />评价调入地块</span>
            <span><i className="legend-out" />评价调出地块</span>
            <span><i className="legend-scenario-selected" />情景已选地块</span>
          </>
          ) : (
          <>
            <span><i className="legend-primary" />一级调入</span>
            <span><i className="legend-secondary" />二级调入</span>
            <span><i className="legend-out" />调出</span>
            <span><i className="legend-renew" />内部更新</span>
            <span><i className="legend-review" />复核</span>
            <span><i className="legend-base" />其他地块</span>
          </>
          )}
        </div>
      )}
      {scenarioSelecting && (
        <div className="scenario-selection-summary">
          <strong>{scenarioAction === "include" ? "调入组合" : "调出组合"}</strong>
          <span>{selectedScenarioIds.length ? selectedScenarioIds.join("、") : "请在地图上点击候选地块进行多选"}</span>
        </div>
      )}
      {scenarioSelecting ? (
        landscapeScenarioEvaluation && (
          <div className="map-selection scenario-mode">
            <LandscapeScenarioDetail evaluation={landscapeScenarioEvaluation} />
          </div>
        )
      ) : (
        <div className="map-selection">
          <strong>
            {selected
              ? isBoundaryMode && selectedLandscape ? `${selectedLandscape.block_id} 形态评价` : "当前要素"
              : isBoundaryMode ? "点击调入、调出地块查看形态评价" : "点击地块查看属性"}
          </strong>
          {isBoundaryMode && selectedLandscape ? (
            <LandscapeMetricDetail result={selectedLandscape} />
          ) : selectedEntries.length > 0 && (
            <dl>
              {selectedEntries.map(([key, value]) => (
                <div key={key}><dt>{key}</dt><dd>{displayValue(value)}</dd></div>
              ))}
            </dl>
          )}
        </div>
      )}
    </div>
  );
}
