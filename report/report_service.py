from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from report.docx_renderer import render_docx
from report.evidence_builder import build_report_evidence
from report.pdf_exporter import export_pdf
from report.policy_report_generator import generate_policy_document


@dataclass(slots=True)
class ReportOutputs:
    title: str
    text: str
    evidence: dict[str, Any]
    docx_path: Path
    pdf_path: Path
    model_used: bool
    warning: str | None


def generate_planning_policy_report(
    analysis_payload: dict[str, Any],
    policy_documents: list[dict[str, Any]],
    dataset_name: str,
    model_settings: dict[str, Any] | None,
    output_dir: Path,
    spatial_geojson: dict[str, Any] | None = None,
) -> ReportOutputs:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = output_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    evidence = build_report_evidence(analysis_payload, policy_documents, dataset_name)
    evidence_path = report_dir / f"evidence_{timestamp}.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    document, model_used, warning = generate_policy_document(evidence, model_settings)
    base_name = f"LLM驱动的城镇开发边界调整政策_{timestamp}"
    docx_path = report_dir / f"{base_name}.docx"
    pdf_path = report_dir / f"{base_name}.pdf"
    render_docx(docx_path, document, evidence, None)
    export_pdf(pdf_path, document, evidence, None)
    return ReportOutputs(
        title=document.title,
        text=_document_text(document),
        evidence=evidence,
        docx_path=docx_path,
        pdf_path=pdf_path,
        model_used=model_used,
        warning=warning,
    )


def _document_text(document) -> str:
    lines = [document.title, "前 言", document.preface]
    for chapter in document.chapters:
        lines.append(chapter.title)
        for section in chapter.sections:
            for article in section.articles:
                lines.extend([article.title, article.content])
    return "\n".join(lines)
