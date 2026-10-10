import { Download, ExternalLink, FileSearch, FileText, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  artifactDownloadUrl,
  artifactPreviewUrl,
  getRagPolicyDocument,
  listRagPolicies,
  searchRagPolicies,
} from "../api";
import type {
  ArtifactSummary,
  RagPolicyDocumentDetail,
  RagPolicyDocumentSummary,
  RagPolicySearchResult,
} from "../types";

interface Props {
  report: ArtifactSummary | null;
}

export function PolicyDocumentView({ report }: Props) {
  const [documents, setDocuments] = useState<RagPolicyDocumentSummary[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [selectedDocument, setSelectedDocument] = useState<RagPolicyDocumentDetail | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<RagPolicySearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const previewUrl = report ? artifactPreviewUrl(report.id) : "";

  useEffect(() => {
    setLoading(true);
    listRagPolicies()
      .then((items) => {
        setDocuments(items);
        setSelectedId((current) => current || items[0]?.id || "");
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setSelectedDocument(null);
      return;
    }
    getRagPolicyDocument(selectedId)
      .then(setSelectedDocument)
      .catch((err: Error) => setError(err.message));
  }, [selectedId]);

  useEffect(() => {
    const keyword = query.trim();
    if (!keyword) {
      setResults([]);
      return;
    }
    const timer = window.setTimeout(() => {
      searchRagPolicies(keyword, 10)
        .then(setResults)
        .catch((err: Error) => setError(err.message));
    }, 260);
    return () => window.clearTimeout(timer);
  }, [query]);

  const grouped = useMemo(() => {
    const next = new Map<string, RagPolicyDocumentSummary[]>();
    for (const doc of documents) {
      const key = doc.category || "政策文档";
      next.set(key, [...(next.get(key) ?? []), doc]);
    }
    return Array.from(next.entries());
  }, [documents]);

  const openSearchResult = (result: RagPolicySearchResult) => {
    if (result.document_id) setSelectedId(result.document_id);
  };

  return (
    <section className="policy-document-view">
      <header className="policy-document-toolbar">
        <div>
          <span className="eyebrow">POLICY RAG</span>
          <h2>政策库检索</h2>
        </div>
        {report && (
          <div className="policy-document-actions">
            <a className="policy-open-link" href={previewUrl} target="_blank" rel="noreferrer" title="新窗口打开PDF">
              <ExternalLink size={14} />
              报告
            </a>
            <a className="policy-open-link" href={artifactDownloadUrl(report.id)} title="下载PDF">
              <Download size={14} />
              下载
            </a>
          </div>
        )}
      </header>

      <div className="policy-rag-layout">
        <aside className="policy-rag-sidebar">
          <label className="policy-rag-search">
            <Search size={14} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索局部优化、永久基本农田、重大项目..."
            />
            {query && (
              <button type="button" onClick={() => setQuery("")} title="清空搜索">
                <X size={13} />
              </button>
            )}
          </label>

          {error && <p className="policy-rag-error">{error}</p>}

          {query.trim() ? (
            <div className="policy-rag-results">
              <strong>搜索结果</strong>
              {results.map((item, index) => (
                <button
                  key={`${item.source}-${item.rule_id ?? index}`}
                  type="button"
                  className="policy-rag-result"
                  onClick={() => openSearchResult(item)}
                  disabled={!item.document_id}
                >
                  <span>{item.rule_id ? `规则 ${item.rule_id}` : item.source}</span>
                  <small>{item.excerpt}</small>
                </button>
              ))}
              {!results.length && <p className="policy-rag-empty">未检索到匹配内容</p>}
            </div>
          ) : (
            <div className="policy-rag-documents">
              {loading && <p className="policy-rag-empty">正在读取政策库...</p>}
              {grouped.map(([category, items]) => (
                <section key={category}>
                  <h3>{category}</h3>
                  {items.map((doc) => (
                    <button
                      key={doc.id}
                      type="button"
                      className={`policy-rag-doc ${selectedId === doc.id ? "active" : ""}`}
                      onClick={() => setSelectedId(doc.id)}
                    >
                      <FileText size={13} />
                      <span>
                        <strong>{doc.name}</strong>
                        <small>{doc.text_length.toLocaleString()} 字</small>
                      </span>
                    </button>
                  ))}
                </section>
              ))}
            </div>
          )}
        </aside>

        <article className="policy-rag-reader">
          {selectedDocument ? (
            <>
              <div className="policy-rag-reader-head">
                <FileSearch size={18} />
                <div>
                  <span>{selectedDocument.category}</span>
                  <h3>{selectedDocument.name}</h3>
                  <small>{selectedDocument.source}</small>
                </div>
              </div>
              <pre>{selectedDocument.content}</pre>
            </>
          ) : (
            <div className="policy-empty-state">
              <FileText size={30} />
              <strong>暂无可预览政策文档</strong>
              <p>请确认 RAG 政策库目录存在，并包含 DOCX、TXT 或 PDF 文件。</p>
            </div>
          )}
        </article>
      </div>
    </section>
  );
}
