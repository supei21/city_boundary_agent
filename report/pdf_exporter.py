from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

from report.document_schema import PlanningPolicyDocument


PAGE_SIZE = (1240, 1754)
MARGIN_X = 96
DISCLAIMER = "本材料仅用于汇报展示，不作为正式规划及行政决策依据。"
RED_HEAD_TITLE = "LLM驱动的城镇开发边界调整政策"


def export_pdf(output_path: Path, document: PlanningPolicyDocument, evidence: dict, map_image: Path | None = None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    regular = _font(25)
    bold = _font(30, True)
    title = _font(42, True)
    red_head = _font(44, True)
    small = _font(19)
    pages: list[Image.Image] = []
    page = _page()
    pages.append(page)
    draw = ImageDraw.Draw(page)
    y = _research_header(draw, document, red_head, title, regular, small)
    draw.text((MARGIN_X + 40, y), "有关单位：", fill="#333333", font=regular)
    y += 62
    y = _write_paragraphs(draw, y, [document.preface], regular, pages, small)
    for chapter in document.chapters:
        y = _ensure_space(y, pages, small)
        draw = ImageDraw.Draw(pages[-1])
        y = _write_heading(draw, y, chapter.title, bold)
        for section in chapter.sections:
            for article in section.articles:
                y = _ensure_space(y, pages, small)
                draw = ImageDraw.Draw(pages[-1])
                draw.text((MARGIN_X, y), article.title, fill="#111111", font=bold)
                y += 44
                y = _write_paragraphs(draw, y, [article.content], regular, pages, small)
    if map_image and map_image.exists():
        y = _ensure_space(y + 20, pages, small, needed=520)
        draw = ImageDraw.Draw(pages[-1])
        y = _write_heading(draw, y, "附图", bold)
        image = Image.open(map_image).convert("RGB")
        image.thumbnail((PAGE_SIZE[0] - MARGIN_X * 2, 430))
        x = (PAGE_SIZE[0] - image.width) // 2
        pages[-1].paste(image, (x, y))
        y += image.height + 24
        y = _center(draw, y, "图1  候选调入地块空间分布", small, "#333333") + 20
    y = _ensure_space(y + 20, pages, small, needed=360)
    draw = ImageDraw.Draw(pages[-1])
    y = _write_heading(draw, y, "附表  候选调入地块清单", bold)
    y = _draw_table(draw, y, list(evidence.get("candidate_blocks") or [])[:16], small)
    for idx, page in enumerate(pages, start=1):
        _footer(ImageDraw.Draw(page), idx, small)
    pages[0].save(output_path, "PDF", resolution=150, save_all=True, append_images=pages[1:])


def _research_header(draw: ImageDraw.ImageDraw, document: PlanningPolicyDocument, red_head, title_font, regular, small) -> int:
    red = "#b13a35"
    y = 165
    y = _center(draw, y, RED_HEAD_TITLE, red_head, red) + 28
    y = _center(draw, y, "武新规研〔2026〕5号", small, "#777777") + 34
    draw.line((MARGIN_X + 105, y, PAGE_SIZE[0] - MARGIN_X - 105, y), fill=red, width=4)
    y += 58
    y = _center(draw, y, f"专题报告  {datetime.now().strftime('%Y年%m月')}", small, "#a0a0a0") + 74
    for line in _title_lines(document.title):
        y = _center(draw, y, line, title_font, "#111111") + 14
    y += 66
    return y


def _write_heading(draw: ImageDraw.ImageDraw, y: int, text: str, font) -> int:
    y += 20
    return _center(draw, y, text, font, "#111111") + 36


def _write_paragraphs(draw: ImageDraw.ImageDraw, y: int, paragraphs: Iterable[str], font, pages: list[Image.Image], small) -> int:
    for paragraph in paragraphs:
        for line in _wrap("    " + paragraph, font, PAGE_SIZE[0] - MARGIN_X * 2):
            y = _ensure_space(y, pages, small)
            draw = ImageDraw.Draw(pages[-1])
            draw.text((MARGIN_X, y), line, fill="#222222", font=font)
            y += 42
        y += 8
    return y


def _draw_table(draw: ImageDraw.ImageDraw, y: int, candidates: list[dict], font) -> int:
    headers = ["序号", "街区ID", "开发潜力", "候选类型", "约束情况"]
    widths = [70, 260, 140, 220, 230]
    row_h = 42
    x0 = MARGIN_X
    for row_index, values in enumerate([headers] + [[str(i), str(item.get("block_id") or ""), _score(item.get("dynamic_score") or item.get("overall_suitability")), str(item.get("decision") or ""), _constraint_label(item.get("constraints") or {})] for i, item in enumerate(candidates, 1)]):
        x = x0
        for idx, value in enumerate(values):
            draw.rectangle((x, y, x + widths[idx], y + row_h), outline="#777777", width=1)
            draw.text((x + 6, y + 9), value[:16], fill="#111111", font=font)
            x += widths[idx]
        y += row_h
        if row_index >= 17:
            break
    return y + 24


def _ensure_space(y: int, pages: list[Image.Image], small, needed: int = 120) -> int:
    if not pages:
        pages.append(_page())
    if y + needed < PAGE_SIZE[1] - 120:
        return y
    _footer(ImageDraw.Draw(pages[-1]), len(pages), small)
    pages.append(_page())
    return 100


def _wrap(text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        test = current + char
        bbox = font.getbbox(test)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = char
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def _center(draw: ImageDraw.ImageDraw, y: int, text: str, font, fill: str) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(((PAGE_SIZE[0] - (bbox[2] - bbox[0])) // 2, y), text, fill=fill, font=font)
    return y + bbox[3] - bbox[1]


def _title_lines(title: str) -> list[str]:
    if len(title) <= 24:
        return [title]
    if "候选调入地块" in title:
        return title.replace("候选调入地块", "候选调入地块\n").splitlines()
    midpoint = len(title) // 2
    return [title[:midpoint], title[midpoint:]]


def _footer(draw: ImageDraw.ImageDraw, page_no: int, font) -> None:
    disclaimer_bbox = draw.textbbox((0, 0), DISCLAIMER, font=font)
    draw.text(((PAGE_SIZE[0] - (disclaimer_bbox[2] - disclaimer_bbox[0])) // 2, PAGE_SIZE[1] - 112), DISCLAIMER, fill="#9a9a9a", font=font)
    y = PAGE_SIZE[1] - 72
    text = f"— {page_no} —"
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(((PAGE_SIZE[0] - (bbox[2] - bbox[0])) // 2, y), text, fill="#666666", font=font)


def _page() -> Image.Image:
    return Image.new("RGB", PAGE_SIZE, "#ffffff")


def _font(size: int, bold: bool = False):
    names = ["simhei.ttf", "msyhbd.ttc"] if bold else ["simsun.ttc", "msyh.ttc", "simhei.ttf"]
    for name in names:
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _score(value: object) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return ""


def _constraint_label(constraints: dict) -> str:
    return "需复核" if any(constraints.values()) else "未见明显冲突"
