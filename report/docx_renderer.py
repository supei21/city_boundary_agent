from __future__ import annotations

import html
import zipfile
from pathlib import Path

from report.document_schema import PlanningPolicyDocument


DISCLAIMER = "本材料仅用于汇报展示，不作为正式规划及行政决策依据。"
RED_HEAD_TITLE = "LLM驱动的城镇开发边界调整政策"


def render_docx(
    output_path: Path,
    document: PlanningPolicyDocument,
    evidence: dict,
    map_image: Path | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image_bytes = map_image.read_bytes() if map_image and map_image.exists() else None
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", _content_types(bool(image_bytes)))
        docx.writestr("_rels/.rels", _root_rels())
        docx.writestr("word/_rels/document.xml.rels", _document_rels(bool(image_bytes)))
        docx.writestr("word/styles.xml", _styles_xml())
        docx.writestr("word/document.xml", _document_xml(document, evidence, bool(image_bytes)))
        if image_bytes:
            docx.writestr("word/media/map.png", image_bytes)


def _content_types(has_image: bool) -> str:
    image = '<Default Extension="png" ContentType="image/png"/>' if has_image else ""
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  {image}
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""


def _root_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _document_rels(has_image: bool) -> str:
    rel = '<Relationship Id="rIdMap" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/map.png"/>' if has_image else ""
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rel}</Relationships>"""


def _styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/><w:rPr><w:rFonts w:ascii="FangSong" w:eastAsia="仿宋"/><w:sz w:val="28"/></w:rPr>
  </w:style>
</w:styles>"""


def _document_xml(document: PlanningPolicyDocument, evidence: dict, has_image: bool) -> str:
    parts: list[str] = []
    parts.append(_paragraph(RED_HEAD_TITLE, "center", color="B13A35", size=44, bold=True, spacing=80))
    parts.append(_paragraph("武新规研〔2026〕5号", "center", color="777777", size=22, spacing=80))
    parts.append(_paragraph("━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "center", color="B13A35", size=22, spacing=130))
    parts.append(_paragraph("专题报告  2026年5月", "center", color="999999", size=22, spacing=170))
    parts.append(_paragraph("\n".join(_title_lines(document.title)), "center", size=34, bold=True, spacing=170))
    parts.append(_paragraph("有关单位：", "left", size=26, spacing=80))
    parts.append(_paragraph(document.preface, "both", first_line=True))
    for chapter in document.chapters:
        parts.append(_paragraph(chapter.title, "center", size=32, bold=True, spacing=180))
        for section in chapter.sections:
            for article in section.articles:
                parts.append(_paragraph(article.title, "left", bold=True))
                parts.append(_paragraph(article.content, "both", first_line=True))
    if has_image:
        parts.append(_paragraph("附图", "center", size=32, bold=True, spacing=180))
        parts.append(_image_paragraph())
        parts.append(_paragraph("图1  候选调入地块空间分布", "center", size=22))
    parts.append(_paragraph("附表  候选调入地块清单", "center", size=30, bold=True, spacing=180))
    parts.append(_candidate_table(list(evidence.get("candidate_blocks") or [])[:20]))
    parts.append(_paragraph(DISCLAIMER, "center", size=18))
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
<w:body>{''.join(parts)}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body>
</w:document>"""


def _paragraph(text: str, align: str = "left", *, color: str = "000000", size: int = 28, bold: bool = False, first_line: bool = False, spacing: int = 80) -> str:
    lines = str(text).splitlines() or [""]
    runs = "<w:br/>".join(_run(line, color=color, size=size, bold=bold) for line in lines)
    indent = '<w:ind w:firstLine="560"/>' if first_line else ""
    return f'<w:p><w:pPr><w:jc w:val="{align}"/>{indent}<w:spacing w:after="{spacing}" w:line="360" w:lineRule="auto"/></w:pPr>{runs}</w:p>'


def _run(text: str, *, color: str, size: int, bold: bool) -> str:
    b = "<w:b/>" if bold else ""
    return f'<w:r><w:rPr><w:rFonts w:ascii="FangSong" w:eastAsia="仿宋"/>{b}<w:color w:val="{color}"/><w:sz w:val="{size}"/></w:rPr><w:t>{html.escape(text)}</w:t></w:r>'


def _page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def _title_lines(title: str) -> list[str]:
    if len(title) <= 24:
        return [title]
    if "候选调入地块" in title:
        return title.replace("候选调入地块", "候选调入地块\n").splitlines()
    midpoint = len(title) // 2
    return [title[:midpoint], title[midpoint:]]


def _image_paragraph() -> str:
    return """<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing><wp:inline><wp:extent cx="5486400" cy="3429000"/><wp:docPr id="1" name="map"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="map.png"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="rIdMap"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="5486400" cy="3429000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"""


def _candidate_table(candidates: list[dict]) -> str:
    headers = ["序号", "街区ID", "行政区", "开发潜力", "候选类型", "主要优势", "约束情况"]
    rows = [_table_row(headers, header=True)]
    for index, item in enumerate(candidates, start=1):
        rows.append(_table_row([
            str(index),
            str(item.get("block_id") or ""),
            str(item.get("district") or ""),
            _score(item.get("dynamic_score") or item.get("overall_suitability")),
            str(item.get("decision") or ""),
            "；".join(str(f.get("name")) for f in item.get("positive_factors", [])[:2]),
            _constraint_label(item.get("constraints") or {}),
        ]))
    return '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders><w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/><w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/></w:tblBorders></w:tblPr>' + "".join(rows) + "</w:tbl>"


def _table_row(values: list[str], header: bool = False) -> str:
    return "<w:tr>" + "".join(f"<w:tc><w:p>{_run(value, color='000000', size=18, bold=header)}</w:p></w:tc>" for value in values) + "</w:tr>"


def _score(value: object) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return ""


def _constraint_label(constraints: dict) -> str:
    if any(constraints.values()):
        return "需复核"
    return "未见明显冲突"
