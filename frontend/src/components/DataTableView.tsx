import { useEffect, useMemo, useState } from "react";
import { Search, X } from "lucide-react";

import { getDataset } from "../api";
import type { DatasetPayload, Decision } from "../types";
import { AttributeTable } from "./AttributeTable";
import { DecisionTable } from "./DecisionTable";

const PAGE_SIZE = 50;

interface Props {
  datasetName: string;
  blockCount: number;
  decisions: Decision[];
  /** 数据版本号：上传/重置数据后递增，触发重新拉取。 */
  dataVersion: number;
}

export function DataTableView({ datasetName, blockCount, decisions, dataVersion }: Props) {
  const [source, setSource] = useState<"raw" | "analysis">("raw");
  const effectiveSource = source === "analysis" && !decisions.length ? "raw" : source;

  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [page, setPage] = useState(0);
  const [payload, setPayload] = useState<DatasetPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    setPage(0);
  }, [debouncedQuery, dataVersion]);

  useEffect(() => {
    if (effectiveSource !== "raw") return;
    let cancelled = false;
    setLoading(true);
    getDataset(page * PAGE_SIZE, PAGE_SIZE, debouncedQuery)
      .then((next) => {
        if (cancelled) return;
        setPayload(next);
        setError(null);
      })
      .catch((exc: Error) => {
        if (cancelled) return;
        setError(exc.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [effectiveSource, page, dataVersion, debouncedQuery]);

  const filteredDecisions = useMemo(() => {
    if (!debouncedQuery) return decisions;
    const text = debouncedQuery.toLowerCase();
    return decisions.filter((item) =>
      item.block_id.toLowerCase().includes(text)
      || (item.block_name ?? "").toLowerCase().includes(text)
      || item.decision.includes(text)
      || item.recommended_use.includes(text)
    );
  }, [decisions, debouncedQuery]);

  const rawTotal = payload?.matched_count ?? 0;
  const searching = debouncedQuery.length > 0;

  return (
    <div className="data-table-view">
      <div className="data-toolbar">
        <div className="data-segmented" role="tablist" aria-label="数据来源">
          <button
            className={effectiveSource === "raw" ? "active" : ""}
            onClick={() => setSource("raw")}
          >
            原始数据
          </button>
          <button
            className={effectiveSource === "analysis" ? "active" : ""}
            onClick={() => setSource("analysis")}
            disabled={!decisions.length}
            title={decisions.length ? "查看最近一次分析结果" : "还没有分析结果"}
          >
            分析结果
          </button>
        </div>
        <div className="data-search">
          <Search size={13} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="查询街区编号、名称或字段值"
            aria-label="查询街区数据"
          />
          {query && (
            <button onClick={() => setQuery("")} title="清除查询" aria-label="清除查询">
              <X size={12} />
            </button>
          )}
        </div>
        <span className="data-meta">
          {effectiveSource === "raw"
            ? searching
              ? `找到 ${rawTotal} / ${payload?.block_count ?? blockCount} 条`
              : `${datasetName} · 共 ${payload?.block_count ?? blockCount} 行`
            : searching
              ? `找到 ${filteredDecisions.length} / ${decisions.length} 条`
              : `最近一次分析 · ${decisions.length} 条判断`}
        </span>
      </div>
      {effectiveSource === "raw" ? (
        error ? (
          <p className="preview-inline-empty">数据读取失败：{error}</p>
        ) : (
          <AttributeTable
            rows={payload?.blocks ?? []}
            compact={false}
            emptyText={searching ? "没有匹配的街区记录" : "当前数据没有可显示的记录"}
            total={rawTotal}
            page={page}
            onPageChange={setPage}
            loading={loading}
          />
        )
      ) : filteredDecisions.length ? (
        <DecisionTable decisions={filteredDecisions} />
      ) : (
        <p className="preview-inline-empty">
          {searching ? "分析结果中没有匹配的记录" : "还没有分析结果，先在上方对话中发起分析"}
        </p>
      )}
    </div>
  );
}
