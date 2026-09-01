import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import type { Decision } from "../types";

const PAGE_SIZE = 50;

export function DecisionTable({ decisions }: { decisions: Decision[] }) {
  const [page, setPage] = useState(0);
  const pageCount = Math.ceil(decisions.length / PAGE_SIZE);
  const visible = decisions.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  useEffect(() => {
    setPage(0);
  }, [decisions]);

  if (!decisions.length) return null;
  return (
    <div className="result-table-wrap">
      <table className="result-table">
        <thead>
          <tr>
            <th>街区</th>
            <th>判断</th>
            <th>动态适配</th>
            <th>推荐用途</th>
            <th>排名</th>
            <th>置信度</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((item) => (
            <tr key={item.block_id}>
              <td>
                <strong>{item.block_id}</strong>
                <small>{item.block_name ?? "未命名"}</small>
              </td>
              <td><span className="decision-tag">{item.decision}</span></td>
              <td>{item.dynamic_score.toFixed(1)}</td>
              <td>{item.recommended_use}</td>
              <td>{item.rank_city ? `全市 ${item.rank_city}` : "—"}</td>
              <td>{Math.round(item.confidence * 100)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      {pageCount > 1 && (
        <div className="table-pagination">
          <span>共 {decisions.length} 条 · 第 {page + 1} / {pageCount} 页</span>
          <div>
            <button onClick={() => setPage((value) => Math.max(value - 1, 0))} disabled={page === 0} title="上一页">
              <ChevronLeft size={14} />
            </button>
            <button onClick={() => setPage((value) => Math.min(value + 1, pageCount - 1))} disabled={page >= pageCount - 1} title="下一页">
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
