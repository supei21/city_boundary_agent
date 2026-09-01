from __future__ import annotations

import re
import textwrap
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont

from boundary_agent.llm.client import build_chat_model, get_api_key


PAGE_SIZE = (1240, 1754)
MARGIN_X = 96
MARGIN_TOP = 86
LINE_GAP = 12
MAX_POLICY_CHARS = 6200
REPORT_BODY_MIN_CHARS = 1800
POLICY_REFERENCE_FILE = Path(__file__).resolve().parents[1] / "data" / "policies" / "policy_references.json"
POLICY_REFERENCE_PDF = Path(__file__).resolve().parents[1] / "data" / "policies" / "武汉市国土空间总体规划_2021-2035.pdf"
POLICY_REPORT_STYLE_GUIDE = """
生成风格参照国土空间总体规划和开发边界管理类正式政策文件：
- 使用规范、审慎、客观的行政公文语体，避免营销化、口语化和第一人称表达。
- 使用“一、二、三”分节结构，正文采用完整段落，不使用 Markdown 标记、表格或项目符号。
- 常用表述包括：依法依规、底线约束、节约集约、统筹衔接、空间适配、动态维护、审慎论证、部门协同、闭环管理。
- 报告开头应呈现“为落实……依据……结合……现形成如下说明”的政策说明口吻。
- 参考红头政策文件的表达方式：标题庄重、章节完整、段落较长、少用短句，多使用“应当、原则上、严格落实、统筹推进、分类施策”等规范表述。
- 必须明示参考文档名称，且不得编造政策条款编号、批复文号或不存在的审批结论。
- 必须说明模型识别结果仅作为辅助研判，不替代法定规划审批、专家论证、公众参与和部门审查。
""".strip()


def load_policy_references() -> list[dict[str, Any]]:
    if not POLICY_REFERENCE_FILE.exists():
        return []
    try:
        payload = __import__("json").loads(POLICY_REFERENCE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    documents: list[dict[str, Any]] = []
    for item in payload.get("documents", []):
        text = str(item.get("text") or "")
        documents.append({
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or "政策参考文档"),
            "size": len(text.encode("utf-8")),
            "uploaded_at": str(payload.get("generated_at") or ""),
            "text_length": len(text),
            "excerpt": str(item.get("excerpt") or text[:140]),
            "warning": None,
            "text": text,
            "source_format": str(item.get("source_format") or ""),
            "stored_format": str(item.get("stored_format") or "plain_text"),
            "builtin": True,
        })
    return documents


def extract_policy_text(path: Path) -> tuple[str, str | None]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".docx":
            return _extract_docx(path), None
        if suffix == ".txt":
            return path.read_text(encoding="utf-8", errors="ignore"), None
        if suffix == ".doc":
            if path.read_bytes()[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
                return "", "DOC为旧版二进制Word格式；请另存为DOCX后上传，以便可靠提取政策文本"
            text = _extract_legacy_doc(path)
            warning = None if len(text) > 80 else "DOC文本提取有限，建议转换为DOCX"
            return text, warning
    except Exception as exc:
        return "", f"政策文件解析失败：{exc}"
    return "", "当前支持DOCX、DOC或TXT政策文件"


def _extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", ns):
        texts = [node.text or "" for node in paragraph.findall(".//w:t", ns)]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _extract_legacy_doc(path: Path) -> str:
    data = path.read_bytes()
    best_chunks: list[str] = []
    for encoding in ("utf-16le", "gb18030", "utf-8"):
        text = data.decode(encoding, errors="ignore")
        chunks = re.findall(r"[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9，。；：、（）《》“”‘’\s\-]{3,}", text)
        cleaned_chunks: list[str] = []
        for chunk in chunks:
            cleaned = re.sub(r"\s+", " ", chunk).strip()
            cjk_count = sum(1 for char in cleaned if "\u4e00" <= char <= "\u9fff")
            if cjk_count >= 4 and cjk_count / max(len(cleaned), 1) >= 0.35:
                cleaned_chunks.append(cleaned)
        if sum(len(item) for item in cleaned_chunks) > sum(len(item) for item in best_chunks):
            best_chunks = cleaned_chunks
    seen: set[str] = set()
    unique = []
    for chunk in best_chunks:
        if chunk not in seen:
            seen.add(chunk)
            unique.append(chunk)
    return "\n".join(unique)


def summarize_decisions(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(item.get("decision") or "未分类") for item in decisions)
    priority = [item for item in decisions if str(item.get("decision")) in {"一级优先调入", "二级优先调入"}]
    review = [item for item in decisions if item.get("manual_review_required")]
    top = sorted(
        priority,
        key=lambda item: float(item.get("dynamic_score") or item.get("overall_suitability") or 0),
        reverse=True,
    )[:10]
    return {
        "total": len(decisions),
        "counts": dict(counts),
        "priority_count": len(priority),
        "review_count": len(review),
        "top_blocks": top,
    }


def build_policy_prompt(
    analysis_payload: dict[str, Any],
    policy_documents: list[dict[str, Any]],
    dataset_name: str,
) -> str:
    decisions = list(analysis_payload.get("decisions") or [])
    summary = summarize_decisions(decisions)
    policy_context = "\n\n".join(
        f"【{doc['name']}】\n{doc.get('text', '')[:MAX_POLICY_CHARS]}"
        for doc in policy_documents
    )
    return textwrap.dedent(
        f"""
        你是国土空间规划政策文件起草助手。请基于候选地块模型分析结果和本地政策文件，生成一份可直接排版为PDF的“城镇开发边界调整政策说明报告”。

        文风与格式要求：
        {POLICY_REPORT_STYLE_GUIDE}

        固定章节：
        一、总体要求
        二、政策依据
        三、模型识别结果
        四、调入开发边界建议
        五、风险管控与复核要求
        六、实施路径与保障措施
        七、结语

        内容要求：
        1. 每一节写成正式文件段落，语言风格接近国土空间总体规划、开发边界管理办法等政策文本。
        2. 重点说明为什么这些地块“更可能适合调入开发边界”，并结合模型概率、动态适配、规划匹配、约束成本等结果解释。
        3. 对重点候选地块给出审慎建议，不作最终审批承诺。
        4. 正文字数控制在1800至2600字之间，内容要充分，适合形成多页红头PDF。
        5. 输出纯正文，不要输出Markdown、表格、代码块、JSON或项目符号。

        数据集：{dataset_name}
        分析地块数：{summary['total']}
        分类统计：{summary['counts']}
        重点候选地块：{[item.get('block_id') for item in summary['top_blocks'][:8]]}
        参考文档：{[doc['name'] for doc in policy_documents]}

        政策文件摘录：
        {policy_context[:9000]}
        """
    ).strip()


def draft_report_text(
    analysis_payload: dict[str, Any],
    policy_documents: list[dict[str, Any]],
    dataset_name: str,
    model_settings: dict[str, Any] | None,
) -> tuple[str, bool, str | None]:
    if model_settings and model_settings.get("enabled") and get_api_key(model_settings):
        try:
            llm = build_chat_model({**model_settings, "temperature": 0.18, "max_tokens": 4200})
            response = llm.invoke(build_policy_prompt(analysis_payload, policy_documents, dataset_name))
            text = _normalize_report_text(str(getattr(response, "content", response)))
            if text:
                return text, True, None
        except Exception as exc:
            return _fallback_report_text(analysis_payload, policy_documents, dataset_name), False, f"大模型生成失败，已使用本地模板：{exc}"
    return _fallback_report_text(analysis_payload, policy_documents, dataset_name), False, None


def _fallback_report_text(
    analysis_payload: dict[str, Any],
    policy_documents: list[dict[str, Any]],
    dataset_name: str,
) -> str:
    decisions = list(analysis_payload.get("decisions") or [])
    summary = summarize_decisions(decisions)
    top_blocks = [
        f"{item.get('block_id')}（{item.get('decision')}，动态适配{float(item.get('dynamic_score') or 0):.1f}）"
        for item in summary["top_blocks"][:6]
    ]
    policy_names = "、".join(doc["name"] for doc in policy_documents) or "未上传政策文件"
    return "\n".join(
        [
            "一、总体要求",
            f"为落实国土空间规划管控要求，统筹城镇开发边界动态维护与增量空间适配，提升增量建设空间识别、论证和管理的科学化水平，本报告基于“{dataset_name}”候选地块分析结果，结合本地政策参考文件形成辅助说明。报告坚持底线约束、节约集约、以人为本、空间协同和动态维护原则，围绕上一阶段识别形成的潜在调整地块，进一步开展模型筛选、规划适配和政策符合性研判。模型识别结果仅作为规划研判和方案比选参考，不替代法定规划审批、专家论证、公众参与和部门审查程序。",
            "本次工作面向城镇开发边界优化调整的前期辅助决策环节，重点回答“哪些候选地块更具调入开发边界的可能性”。在技术方法上，系统综合利用训练完成的机器学习模型、SHAP解释结果、动态适配指标和三线约束数据，对候选地块的开发概率、建设基础、规划匹配、约束冲突、空间连通和实施风险进行综合评价。相关结论用于提高规划人员初筛效率，为后续实地核查、部门会商和方案深化提供线索。",
            "二、政策依据",
            f"本次参考文档包括：{policy_names}。相关研判坚持底线约束、节约集约、空间适配和规划衔接原则，重点校核城镇开发边界管控要求、生态保护红线、永久基本农田、自然灾害风险、城镇建设适宜性及国土空间总体规划确定的空间结构、功能分区和发展方向。对位于刚性管控范围内或存在重大安全、生态、耕地保护风险的地块，原则上不得仅依据模型评分纳入调入建议，应当结合规划底图、现状权属、建设时序和设施承载能力开展复核。",
            "政策适配判断应突出规划权威性和边界刚性约束，既要保障合理城镇建设需求，也要防止无序扩张和低效蔓延。对确需纳入后续论证的地块，应说明其与城镇空间格局、产业布局、公共服务和基础设施廊道的关系，明确是否有利于提高边界完整性、连续性和土地利用效率，并同步评估对生态安全格局、永久基本农田保护和历史文化资源保护的影响。",
            "三、模型识别结果",
            f"本次共分析 {summary['total']} 个地块，分类统计为：{summary['counts']}。其中优先调入候选 {summary['priority_count']} 个，需人工复核 {summary['review_count']} 个。模型综合考虑发展概率、规划匹配、约束成本、动态适配和解释因子，用于从上一阶段潜在调整地块中进一步筛选更具实施可能性的对象。总体看，得分较高的地块通常表现为建设基础相对完善、与既有城镇空间联系紧密、规划用途方向较为一致、约束冲突较低、近期实施条件较清晰等特征。",
            "SHAP关键因素用于解释模型判断依据，能够提示哪些指标对地块被识别为优先调入产生较大影响。实际应用中，应将模型解释结果与规划人员经验判断结合，重点关注高分地块是否存在数据缺失、边界破碎、用地权属复杂、设施容量不足或与上位规划不一致等问题。对于模型概率较高但约束成本同步偏高的地块，应列入重点复核清单，不宜直接进入调入建议。",
            "四、调入开发边界建议",
            "建议优先将动态适配得分较高、建设基础较好、与规划用途衔接度较高且未触碰刚性管控底线的地块纳入后续论证范围。代表性候选包括：" + ("；".join(top_blocks) if top_blocks else "暂无。") + "。上述地块应作为“优先复核对象”而非最终审批对象，在后续工作中进一步核验其用地现状、交通市政条件、公共服务服务半径、产业或居住需求支撑、周边边界形态和生态环境影响。",
            "对一级优先调入地块，建议组织开展图斑级空间叠加核查，重点确认是否避让生态保护红线、永久基本农田、地质灾害高风险区和其他禁止建设区域；对二级优先调入地块，建议结合近期建设计划、存量空间盘活潜力和片区功能完善需求，开展多方案比选；对人工复核地块，应形成问题清单，明确复核责任、资料补充要求和判定时限。对于调入后可能造成边界外扩过快、空间形态破碎或公共服务成本显著提高的地块，应审慎控制。",
            "五、风险管控与复核要求",
            "对涉及生态保护红线、永久基本农田、灾害风险、边界连通性不足、设施承载能力不足或属性数据缺失的地块，应开展空间叠加校核、部门协同审查和现场复核，确保调整建议与国土空间规划实施监督要求一致。重点风险包括：一是突破底线约束导致调整方案不具备合规基础；二是高估近期建设需求导致增量空间配置低效；三是忽视基础设施和公共服务承载能力导致建设实施成本过高；四是空间数据精度、时点和口径不一致影响模型判断准确性。",
            "复核工作应坚持“先底线、后适宜、再实施”的顺序。对约束冲突明显的地块，应先行剔除或降级处理；对适宜性较好但资料不足的地块，应补充现状调查、权属信息和规划条件；对确有调入必要的地块，应同步提出管控边界、开发强度、配套设施、生态补偿或用途管制要求，避免形成单纯以模型评分替代规划论证的倾向。",
            "六、实施路径与保障措施",
            "建议建立“模型初筛、规划复核、三线校核、方案比选、成果入库、动态维护”的闭环工作流，将模型概率、SHAP关键因素、空间约束命中情况和人工复核意见同步归档，为后续边界优化和政策文件生成提供可追溯依据。系统层面应保留每一次模型调用、参数版本、输入数据、输出结果和人工修改记录，支持后续审查追踪和成果复现。",
            "在实施安排上，可先形成候选地块分级清单，再组织规划、自然资源、生态环境、农业农村、住建、交通等相关部门开展协同校核；对符合条件的地块，纳入片区方案深化和国土空间规划动态维护论证；对暂不具备条件的地块，进入储备库并标注限制因素。后续系统可接入政策向量库、审批规则库和多源空间底图，使政策文件生成、空间可视化和模型解释形成一体化工作链条。",
            "七、结语",
            "综上，本报告从智能模型识别、规划政策适配和空间约束校核三个方面，对潜在调整地块开展初步筛选和说明。相关成果可为城镇开发边界增量空间动态适配提供技术支撑，但最终调整范围、时序和管控要求仍应依据法定程序确定。建议在后续工作中持续完善样本数据、优化模型特征、强化政策规则约束，并通过人工复核和部门协同提升成果可靠性。",
        ]
    )


def _normalize_report_text(text: str) -> str:
    cleaned_lines: list[str] = []
    for line in text.strip().splitlines():
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        line = line.replace("**", "").replace("__", "").strip()
        line = re.sub(r"^\s*[-*]\s+", "", line)
        if line:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def write_policy_pdf(
    output_path: Path,
    title: str,
    report_text: str,
    metadata: dict[str, Any],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    regular_font = _load_font(25)
    bold_font = _load_font(30, bold=True)
    title_font = _load_font(42, bold=True)
    red_head_font = _load_font(48, bold=True)
    small_font = _load_font(20)
    pages: list[Image.Image] = []
    page = _new_page()
    draw = ImageDraw.Draw(page)
    y = _draw_red_head_header(draw, title, metadata, red_head_font, title_font, small_font)

    for line in _wrap_report_lines(report_text, regular_font):
        if y > PAGE_SIZE[1] - 138:
            _draw_page_footer(draw, len(pages) + 1, small_font)
            pages.append(page)
            page = _new_page()
            draw = ImageDraw.Draw(page)
            y = 104
        if _is_heading(line):
            y += 16
            draw.text((MARGIN_X, y), line, fill="#1d1d1d", font=bold_font)
            y += 44
        elif line:
            draw.text((MARGIN_X, y), line, fill="#222222", font=regular_font)
            y += 36 + LINE_GAP
        else:
            y += 20

    _draw_page_footer(draw, len(pages) + 1, small_font)
    pages.append(page)
    pages[0].save(output_path, "PDF", resolution=150, save_all=True, append_images=pages[1:])


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_names = ["msyhbd.ttc", "simhei.ttf"] if bold else ["msyh.ttc", "simsun.ttc", "simhei.ttf"]
    for name in font_names:
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _new_page() -> Image.Image:
    return Image.new("RGB", PAGE_SIZE, "#ffffff")


def _draw_centered(draw: ImageDraw.ImageDraw, y: int, text: str, font, fill: str) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (PAGE_SIZE[0] - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), text, fill=fill, font=font)
    return y + (bbox[3] - bbox[1])


def _draw_red_head_header(
    draw: ImageDraw.ImageDraw,
    title: str,
    metadata: dict[str, Any],
    red_head_font,
    title_font,
    small_font,
) -> int:
    red = "#c00000"
    y = 88
    y = _draw_centered(draw, y, "武汉市国土空间规划智能辅助决策专报", red_head_font, red)
    y += 34
    draw.line((MARGIN_X, y, PAGE_SIZE[0] - MARGIN_X, y), fill=red, width=5)
    y += 24

    issue = f"边界智能体生成〔{datetime.now().year}〕第 {datetime.now().strftime('%m%d%H%M')} 号"
    draw.text((MARGIN_X, y), issue, fill="#5c5c5c", font=small_font)
    right = "内部演示件"
    right_bbox = draw.textbbox((0, 0), right, font=small_font)
    draw.text((PAGE_SIZE[0] - MARGIN_X - (right_bbox[2] - right_bbox[0]), y), right, fill="#5c5c5c", font=small_font)
    y += 48

    y = _draw_centered(draw, y, title, title_font, "#111111")
    y += 26
    subtitle = "基于候选地块模型识别结果与政策参考文档生成"
    y = _draw_centered(draw, y, subtitle, small_font, "#666666")
    y += 36

    meta_lines = [
        f"数据来源：{str(metadata.get('dataset_name') or '未命名数据集')}",
        f"参考文件：{str(metadata.get('policy_names') or '未加载政策参考文件')}",
        f"生成时间：{datetime.now().strftime('%Y年%m月%d日 %H:%M')}",
    ]
    for line in meta_lines:
        for wrapped in _wrap_plain_text(line, small_font, PAGE_SIZE[0] - MARGIN_X * 2):
            draw.text((MARGIN_X, y), wrapped, fill="#555555", font=small_font)
            y += 32
    y += 12
    draw.line((MARGIN_X, y, PAGE_SIZE[0] - MARGIN_X, y), fill="#d8d8d8", width=1)
    return y + 38


def _draw_page_footer(draw: ImageDraw.ImageDraw, page_no: int, font) -> None:
    y = PAGE_SIZE[1] - 78
    draw.line((MARGIN_X, y, PAGE_SIZE[0] - MARGIN_X, y), fill="#e4e4e4", width=1)
    text = f"- {page_no} -"
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(((PAGE_SIZE[0] - (bbox[2] - bbox[0])) // 2, y + 20), text, fill="#777777", font=font)


def _wrap_report_lines(text: str, font) -> list[str]:
    lines: list[str] = []
    max_width = PAGE_SIZE[0] - MARGIN_X * 2
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            lines.append("")
            continue
        lines.extend(_wrap_plain_text(raw, font, max_width))
    return lines


def _wrap_plain_text(text: str, font, max_width: int) -> list[str]:
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


def _is_heading(line: str) -> bool:
    return bool(re.match(r"^[一二三四五六七八九十]、", line)) or line.endswith("：")
