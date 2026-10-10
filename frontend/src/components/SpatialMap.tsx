import { useEffect, useMemo, useRef, useState } from "react";
import type { FeatureCollection } from "geojson";
import maplibregl, { LngLatBounds } from "maplibre-gl";


interface Props {
  data: FeatureCollection;
}

type FeatureProperties = Record<string, unknown>;
type DecisionFilter = "全部" | "一级优先调入" | "二级优先调入" | "建议调出" | "内部更新" | "人工复核";
type MapMode = "potential" | "decision";

const FILTER_OPTIONS: DecisionFilter[] = [
  "全部",
  "一级优先调入",
  "二级优先调入",
  "建议调出",
  "内部更新",
  "人工复核",
];

const DETAIL_FIELDS: Array<[string, string]> = [
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

export function SpatialMap({ data }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<FeatureProperties | null>(null);
  const [mode, setMode] = useState<MapMode>("potential");
  const [filter, setFilter] = useState<DecisionFilter>("全部");
  const filteredData = useMemo<FeatureCollection>(() => ({
    ...data,
    features: mode === "potential" || filter === "全部"
      ? data.features
      : data.features.filter((feature) => feature.properties?.ai_decision === filter),
  }), [data, filter, mode]);

  useEffect(() => {
    if (!containerRef.current || !filteredData.features.length) return;

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
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");

    map.on("load", () => {
      map.addSource("blocks", { type: "geojson", data: filteredData });
      map.addLayer({
        id: "blocks-fill",
        type: "fill",
        source: "blocks",
        paint: {
          "fill-color": [
            ...(mode === "potential"
              ? [
                "interpolate",
                ["linear"],
                [
                  "coalesce",
                  ["to-number", ["get", "development_potential"]],
                  ["/", ["to-number", ["get", "ai_development_probability"]], 100],
                  0,
                ],
                0, "#440154",
                0.25, "#3b528b",
                0.5, "#21918c",
                0.75, "#5ec962",
                1, "#fde725",
              ]
              : [
                "match",
                ["get", "ai_decision"],
                "一级优先调入", "#16835f",
                "二级优先调入", "#55a47f",
                "建议调出", "#c64c4c",
                "内部更新", "#3277a8",
                "人工复核", "#d39a35",
                "#c8cfcb",
              ]),
          ],
          "fill-opacity": mode === "potential"
            ? 0.72
            : [
              "case",
              ["boolean", ["get", "is_candidate"], false],
              0.68,
              ["boolean", ["get", "is_selected"], false],
              0.42,
              0.08,
            ],
        },
      });
      map.addLayer({
        id: "blocks-line",
        type: "line",
        source: "blocks",
        paint: {
          "line-color": mode === "potential"
            ? "#263a33"
            : [
              "case",
              ["boolean", ["get", "is_candidate"], false],
              "#173f32",
              "#728079",
            ],
          "line-opacity": mode === "potential" ? 0.48 : 0.75,
          "line-width": mode === "potential"
            ? 0.45
            : [
              "case",
              ["boolean", ["get", "is_candidate"], false],
              1.4,
              0.35,
            ],
        },
      });

      const bounds = new LngLatBounds();
      filteredData.features.forEach((feature) => {
        const geometry = feature.geometry;
        if (geometry && "coordinates" in geometry) extendBounds(bounds, geometry.coordinates);
      });
      if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 34, maxZoom: 16, duration: 0 });
    });

    map.on("mouseenter", "blocks-fill", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "blocks-fill", () => { map.getCanvas().style.cursor = ""; });
    map.on("click", "blocks-fill", (event) => {
      const feature = event.features?.[0];
      const properties = feature?.properties as FeatureProperties | undefined;
      if (!properties) return;
      setSelected(properties);
      const popupContent = document.createElement("div");
      popupContent.className = "map-popup-content";
      const title = document.createElement("strong");
      title.textContent = displayValue(properties.block_name || properties.block_id);
      const detail = document.createElement("span");
      const scoreValue = mode === "potential"
        ? properties.development_potential
        : properties.ai_dynamic_score;
      const scoreLabel = mode === "potential" ? "综合潜力" : "动态适配";
      const score = scoreValue === undefined ? "" : ` · ${scoreLabel} ${displayValue(scoreValue)}`;
      detail.textContent = `${displayValue(properties.ai_decision || "查看地块属性")}${score}`;
      popupContent.append(title, detail);
      new maplibregl.Popup({ closeButton: false, offset: 8 })
        .setLngLat(event.lngLat)
        .setDOMContent(popupContent)
        .addTo(map);
    });

    const observer = new ResizeObserver(() => map.resize());
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      map.remove();
    };
  }, [filteredData, mode]);

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
            onClick={() => { setMode("potential"); setSelected(null); }}
          >综合发展潜力</button>
          <button
            type="button"
            className={mode === "decision" ? "active" : ""}
            onClick={() => { setMode("decision"); setSelected(null); }}
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
              }}
            >
              {FILTER_OPTIONS.map((option) => <option key={option}>{option}</option>)}
            </select>
          </>
        )}
        <span className="map-feature-count">{filteredData.features.length} 个地块</span>
      </div>
      <div className="map-canvas" ref={containerRef} />
      {filteredData.features.length === 0 && <p className="map-empty">当前筛选条件没有地块</p>}
      {mode === "potential" ? (
        <div className="map-legend potential-legend" aria-label="综合发展潜力图例">
          <span>低 0</span><i className="potential-gradient" /><span>1 高</span>
        </div>
      ) : (
        <div className="map-legend" aria-label="调入决策图例">
          <span><i className="legend-primary" />一级调入</span>
          <span><i className="legend-secondary" />二级调入</span>
          <span><i className="legend-out" />调出</span>
          <span><i className="legend-renew" />内部更新</span>
          <span><i className="legend-review" />复核</span>
          <span><i className="legend-base" />其他地块</span>
        </div>
      )}
      <div className="map-selection">
        <strong>{selected ? "当前地块" : "点击地块查看属性"}</strong>
        {selectedEntries.length > 0 && (
          <dl>
            {selectedEntries.map(([key, value]) => (
              <div key={key}><dt>{key}</dt><dd>{displayValue(value)}</dd></div>
            ))}
          </dl>
        )}
      </div>
    </div>
  );
}
