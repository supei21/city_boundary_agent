import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

interface Props {
  rows: Record<string, unknown>[];
  compact?: boolean;
  emptyText?: string;
  /** 服务端分页模式：数据总行数。传入后由父组件控制页码。 */
  total?: number;
  page?: number;
  onPageChange?: (page: number) => void;
  loading?: boolean;
}

const PAGE_SIZE = 50;
const MAX_COLUMNS = 30;

export function AttributeTable({
  rows,
  compact = true,
  emptyText,
  total,
  page: serverPage = 0,
  onPageChange,
  loading = false,
}: Props) {
  const serverMode = total !== undefined;
  const [clientPage, setClientPage] = useState(0);
  const page = serverMode ? serverPage : clientPage;
  const setPage = onPageChange ?? setClientPage;
  const pageCount = Math.max(1, Math.ceil((total ?? rows.length) / PAGE_SIZE));
  const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, MAX_COLUMNS);
  const visible = serverMode ? rows : rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  useEffect(() => {
    if (!serverMode) setClientPage(0);
  }, [rows, serverMode]);

  if (!rows.length && !loading) {
    return <p className="preview-inline-empty">{emptyText ?? "没有可显示的属性记录"}</p>;
  }
  return (
    <div className={`attribute-table ${compact ? "compact" : "full"}`}>
      <div className="attribute-table-scroll">
        <table className="preview-table">
          <thead>
            <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
          </thead>
          <tbody>
            {visible.map((row, index) => (
              <tr key={String(row.block_id ?? page * PAGE_SIZE + index)}>
                {columns.map((column) => (
                  <td key={column}>
                    {typeof row[column] === "object" ? JSON.stringify(row[column]) : String(row[column] ?? "")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(pageCount > 1 || loading) && (
        <div className="table-pagination">
          <span>{loading ? "正在读取…" : `共 ${total ?? rows.length} 条 · 第 ${page + 1} / ${pageCount} 页`}</span>
          <div>
            <button onClick={() => setPage(Math.max(page - 1, 0))} disabled={page === 0 || loading} title="上一页">
              <ChevronLeft size={14} />
            </button>
            <button onClick={() => setPage(Math.min(page + 1, pageCount - 1))} disabled={page >= pageCount - 1 || loading} title="下一页">
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
