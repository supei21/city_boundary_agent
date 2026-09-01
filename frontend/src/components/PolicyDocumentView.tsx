import { Download, ExternalLink, FileText } from "lucide-react";

import { artifactDownloadUrl, artifactPreviewUrl } from "../api";
import type { ArtifactSummary } from "../types";

interface Props {
  report: ArtifactSummary | null;
}

export function PolicyDocumentView({ report }: Props) {
  const previewUrl = report ? artifactPreviewUrl(report.id) : "";

  return (
    <section className="policy-document-view">
      <header className="policy-document-toolbar">
        <div>
          <span className="eyebrow">POLICY REPORT</span>
          <h2>生成文档预览</h2>
        </div>
        {report && (
          <div className="policy-document-actions">
            <a className="policy-open-link" href={previewUrl} target="_blank" rel="noreferrer" title="新窗口打开PDF">
              <ExternalLink size={14} />
              打开PDF
            </a>
            <a className="policy-open-link" href={artifactDownloadUrl(report.id)} title="下载PDF">
              <Download size={14} />
              下载PDF
            </a>
          </div>
        )}
      </header>
      {report ? (
        <div className="policy-document-body">
          <div className="policy-pdf-frame-wrap">
            <iframe
              title={report.name}
              className="policy-pdf-frame"
              src={previewUrl}
            />
          </div>
        </div>
      ) : (
        <div className="policy-empty-state">
          <FileText size={30} />
          <strong>暂无生成的政策 PDF</strong>
          <p>在右侧“报告生成”中点击生成后，系统会自动跳转到这里预览文档。</p>
        </div>
      )}
    </section>
  );
}
