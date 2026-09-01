from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from boundary_agent.llm.client import build_chat_model, get_api_key
from report.document_schema import PlanningPolicyDocument, ReportArticle, ReportChapter, ReportSection


SYSTEM_PROMPT = """
你负责生成国土空间规划研判文本。
请根据系统提供的空间分析结果和政策检索依据，形成正式、严谨、内容充实的规划政策正文。
只能使用输入中真实存在的数据。
不得修改预测概率。不得修改候选地块类别。不得自行计算解释值。不得自行改变空间约束结果。
不得编造规划政策。不得编造政策条文。不得编造审批结论。
政策依据只能来自policy_evidence。
采用国土空间规划正式语言。
采用：章 → 节 → 条 → 正文 的组织方式。
正文应接近正式研究材料或规划建议稿，不要写成摘要。每一条正文原则上写120至220个汉字，重点条文可更长。
第一章必须充分展开编制目的、编制依据、指导思想、基本原则和适用范围。
目的部分应说明为什么开展候选调入地块研判、解决什么规划管理问题、服务什么实施场景。
依据部分应区分政策依据、规划依据、数据依据和分析依据，但不得编造输入中不存在的文件名称。
原则部分应至少覆盖底线约束、规划衔接、节约集约、交通和设施支撑、分类分级、动态维护、审慎决策。
后续章节应围绕空间发展基础、候选地块识别、综合评价、约束核验、边界调整建议、实施保障展开，内容要连贯，不要重复空话。
报告标题必须为“LLM驱动的城镇开发边界调整政策”。
正文必须围绕“调整说明、政策依据、优化策略”三类内容展开，并可补充实施保障和风险管控。
调整说明应说明候选地块来源、识别逻辑、调入必要性、调整对象分类和空间适配结论。
政策依据应结合policy_evidence说明城镇开发边界管理、三条控制线、规划衔接、局部优化和实施监督等依据。
优化策略应提出边界形态优化、分级调入、约束避让、设施支撑、实施时序、动态监测和部门复核建议。
除固定标题中的“LLM”外，正文技术信息不得直接出现模型、算法、机器学习等表述，应转写为自然的规划语言。
不要输出Markdown。
严格输出指定JSON。
""".strip()


FORBIDDEN_TERMS = ("智能体", "人工智能", "AI", "DeepSeek", "大模型", "Demo", "系统生成", "模型自动生成", "XGBoost", "SHAP", "机器学习", "算法", "特征重要性")


def generate_policy_document(evidence: dict[str, Any], model_settings: dict[str, Any] | None) -> tuple[PlanningPolicyDocument, bool, str | None]:
    if model_settings and model_settings.get("enabled") and get_api_key(model_settings):
        try:
            llm = build_chat_model({**model_settings, "temperature": 0.2, "max_tokens": 8500})
            response = llm.invoke(_build_prompt(evidence))
            document = _parse_document(str(getattr(response, "content", response)))
            return _sanitize_document(document), True, None
        except (ValidationError, ValueError, json.JSONDecodeError, TypeError) as exc:
            return _fallback_document(evidence), False, f"文本生成未返回合法结构，已使用本地模板：{exc}"
        except Exception as exc:
            return _fallback_document(evidence), False, f"文本生成失败，已使用本地模板：{exc}"
    return _fallback_document(evidence), False, None


def _build_prompt(evidence: dict[str, Any]) -> str:
    schema = {
        "title": "LLM驱动的城镇开发边界调整政策",
        "preface": "前言正文",
        "chapters": [
            {
                "title": "第一章  调整说明",
                "sections": [{"articles": [{"title": "第1条  调整背景", "content": "条文正文"}]}],
            }
        ],
    }
    return (
        f"{SYSTEM_PROMPT}\n\n"
        "输出JSON格式如下：\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n"
        "生成要求：\n"
        "1. 至少生成5章、22条以上，前三章必须分别为“调整说明”“政策依据”“优化策略”。\n"
        "2. 重点丰富“调整背景、调整对象、调整必要性、政策依据、约束核验、优化策略、实施保障”。\n"
        "3. 每条正文使用完整自然段，避免一句话结束，避免项目符号。\n"
        "4. 不要插入图片、不要输出图名；正文不要提及技术模型名称，但标题必须保留LLM。\n\n"
        "Evidence如下：\n"
        f"{json.dumps(evidence, ensure_ascii=False)[:18000]}"
    )


def _parse_document(text: str) -> PlanningPolicyDocument:
    cleaned = text.strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if match:
        cleaned = match.group(0)
    return PlanningPolicyDocument.model_validate_json(cleaned)


def _fallback_document(evidence: dict[str, Any]) -> PlanningPolicyDocument:
    region = str(evidence.get("region") or "武汉市")
    summary = dict(evidence.get("analysis_summary") or {})
    candidates = list(evidence.get("candidate_blocks") or [])
    policy = list(evidence.get("policy_evidence") or [])
    title = "LLM驱动的城镇开发边界调整政策"
    top_names = "、".join(str(item.get("block_id")) for item in candidates[:6]) or "暂无重点候选地块"
    policy_sources = "、".join(dict.fromkeys(str(item.get("source")) for item in policy[:6] if item.get("source"))) or "既有政策参考材料"
    articles = [
        ("第一章  调整说明", [
            ("第1条  调整背景", f"为适应{region}城镇空间结构优化、增量空间动态适配和高质量发展需要，进一步提升城镇开发边界局部优化工作的科学性和可操作性，本报告基于既有候选地块分析成果，对潜在调整地块开展综合研判。当前上一阶段识别形成的潜在调整地块数量较多，不同地块在建设基础、交通可达、规划衔接、空间约束和近期实施条件方面差异明显，亟需通过统一的技术口径和政策口径进行再次筛选，形成更适合进入后续复核和方案比选的重点对象。"),
            ("第2条  调整目标", "本次调整说明的目标，是在不突破国土空间规划底线约束、不削弱城镇空间结构稳定性、不替代法定审批程序的前提下，识别更具调入开发边界可能性的候选地块，并说明其纳入后对片区功能完善、基础设施补短板、公共服务提升和边界形态优化的潜在作用。报告强调从空间适宜性、政策符合性和实施可行性三个维度同步判断，避免单纯依据建设需求扩大边界。"),
            ("第3条  调整对象", f"本次研判范围以{region}现有候选地块数据为基础，共纳入分析街区{summary.get('total_blocks', 0)}个。现有分析识别一级优先调入候选{summary.get('primary_import', 0)}个，二级优先调入候选{summary.get('secondary_import', 0)}个，重点复核候选{summary.get('manual_review', 0)}个。上述对象主要来源于上一阶段潜在调整地块，不直接扩大到未经识别的新增区域，后续应根据图斑级空间核验和部门意见进一步压实边界。"),
            ("第4条  调整必要性", f"从现有结果看，部分候选地块与既有城镇空间联系较紧密，具备承接公共服务补短板、产业空间组织、交通设施完善或片区功能提升的潜在条件。初步重点候选包括{top_names}。对这类地块开展边界调入研判，有利于把潜在调整对象从粗筛清单转化为可讨论、可复核、可比选的规划对象，也有利于提高后续城镇开发边界局部优化方案的针对性。"),
            ("第5条  调整边界", "拟调入地块不宜形成零散化、跳跃式和破碎化空间，应优先与现状城镇建设空间、规划道路廊道、公共服务设施和市政基础设施保持良好衔接。对具备集中连片条件、边界形态较完整、设施接入成本较低的地块，可作为重点复核对象；对空间联系弱、约束冲突多或实施条件不清晰的地块，应暂缓纳入正式调整建议。"),
        ]),
        ("第二章  政策依据", [
            ("第6条  依据来源", f"本报告依据项目已有空间分析结果、候选地块识别成果和政策检索材料编制，参考文件包括{policy_sources}。其中，政策依据主要用于把握城镇开发边界划定、实施、调整和监督要求；规划依据主要用于衔接城市空间结构、片区功能定位、公共服务配置和基础设施建设方向；数据依据主要包括候选地块分类建议、空间约束核验信息和现有评价说明。"),
            ("第7条  边界管理依据", "城镇开发边界调整应以国土空间规划确定的城镇空间格局和边界管控要求为基础，严格落实集中建设、集约利用和形态完整的管理导向。对拟调入地块，应说明其与现状城镇建设空间、规划功能布局和近期建设需求之间的关系，重点论证其是否有利于促进城镇建设用地向边界内集中，是否有利于优化边界形态和提高空间治理效率。"),
            ("第8条  三线约束依据", "候选地块调入研判必须坚持三条控制线刚性约束，将生态保护红线、永久基本农田和城镇开发边界的统筹协调作为前置条件。涉及耕地和永久基本农田保护、生态保护红线、灾害风险、河湖水系、重大基础设施廊道等约束的地块，应开展图斑级叠加核验；凡存在无法依法合规处理的刚性冲突，不宜纳入优先调入范围。"),
            ("第9条  局部优化依据", "根据城镇开发边界局部优化的一般要求，边界优化应综合考虑交通、市政基础设施可达性和服务范围，促进边界形态尽可能完整，原则上避免产生新的破碎图斑和管理盲区。对因城镇功能布局优化、基础设施完善、公共服务补短板或已批准建设用地衔接等原因确需优化的地块，应进一步明确调整原因、调整范围、调整规模和实施影响。"),
            ("第10条  监督实施依据", "候选地块调入开发边界后，仍应纳入国土空间规划实施监督和用途管制体系，按照规划许可、详细规划编制、建设时序管控和年度实施计划等要求推进。报告形成的分类建议仅作为规划研判和汇报展示材料，不替代法定规划审批、部门审查、专家论证、公众参与和后续报批程序。"),
        ]),
        ("第三章  优化策略", [
            ("第11条  分类调入策略", "建议按照一级优先调入、二级优先调入和重点复核三类清单开展分级管理。一级优先调入地块应优先开展约束校核、现状权属核查和方案深化；二级优先调入地块应作为方案储备，重点论证设施承载、建设时序和空间连续性；重点复核地块应先补齐基础数据和部门意见，未完成复核前不宜进入正式调入范围。"),
            ("第12条  边界形态优化策略", "边界优化应坚持集中连片、功能协调和形态完整，优先选择能够与现状城镇建设空间形成连续关系的地块，避免形成孤立斑块、狭长尾巴和难以管控的破碎边界。对可调入地块，应结合自然地理边界、道路廊道、河湖水系和现状建设边界进行范围修正，使调整后的边界更便于识别、管理和实施。"),
            ("第13条  约束避让策略", "对涉及生态保护、耕地保护、灾害风险和重大廊道控制的区域，应采取避让优先、从严核验、分类处置的策略。对刚性约束冲突明确的地块，原则上不纳入调入建议；对局部存在可协调问题的地块，应划定不可建设范围，提出专项论证、工程治理或规划条件控制要求，确保开发边界优化不影响底线安全。"),
            ("第14条  设施支撑策略", "拟调入地块应与交通、市政、公服和韧性安全设施统筹衔接。对道路可达性较好、周边公共服务基础较完善、市政管线接入条件较清晰的地块，可优先开展实施方案深化；对设施短板明显的地块，应同步提出道路完善、给排水接入、公共服务配置和防灾减灾设施建设要求，避免形成边界先调入、配套后滞后的问题。"),
            ("第15条  时序组织策略", "边界调整应与近期建设计划、项目成熟度、资金保障和片区开发节奏相协调。近期确有公共服务、基础设施、产业支撑或民生补短板需求的地块，可作为优先实施对象；建设主体不明确、项目时序不清晰或设施投入成本较高的地块，应作为中远期储备或暂缓调入，防止新增建设空间长期闲置。"),
        ]),
        ("第四章  调整实施保障", [
            ("第16条  部门协同", "建议组织规划、自然资源、生态环境、农业农村、住建、交通、水务和应急管理等相关部门开展协同复核，形成可追溯的意见闭环。复核重点包括规划符合性、底线约束、权属情况、设施承载能力、灾害风险、公众利益影响和实施时序。对部门意见不一致或约束条件尚不明确的地块，应暂缓进入正式调整方案。"),
            ("第17条  数据复核", "后续应以最新国土变更调查、规划底图、三条控制线成果、现状建设用地、专项规划和地块权属信息为基础，对候选地块开展图斑级校核。对数据时点不一致、坐标精度差异、边界重叠或属性缺失的问题，应建立问题清单并逐项修正，确保调整建议建立在一致、准确、可追溯的数据基础上。"),
            ("第18条  成果入库", "经复核确认具备调入条件的地块，应将候选清单、调整理由、政策依据、约束核验、部门意见和空间范围同步整理归档，并按要求纳入后续国土空间规划实施管理成果。对暂不具备条件但具有储备价值的地块，应保留为动态监测对象，待政策条件、设施条件和实施需求进一步明确后再行研判。"),
            ("第19条  风险控制", "边界调整应防止以技术研判替代规划决策，防止以局部建设需求突破底线约束，防止新增建设空间与存量低效用地盘活脱节。对可能造成生态风险、耕地保护压力、设施投入压力或空间蔓延风险的地块，应明确控制措施和退出机制，确保调整过程稳妥、审慎、规范。"),
        ]),
        ("第五章  结论建议", [
            ("第20条  总体结论", "本次报告从调整说明、政策依据和优化策略三个方面，对城镇开发边界候选调入地块进行整理。总体看，具备较强城镇联系、较好设施支撑、较低约束冲突和较明确实施需求的地块，可作为后续边界优化复核重点；约束条件复杂、空间形态破碎或实施时序不清晰的地块，应作为储备或复核对象审慎推进。"),
            ("第21条  应用建议", "建议将本报告作为规划汇报、方案比选和部门沟通的辅助材料，与空间图件、候选地块清单、约束核验表和政策依据材料配套使用。对拟进入正式调整程序的地块，应按照法定要求完善专项论证、征求意见和报批材料，确保调整过程有据可依、有迹可循、有序推进。"),
            ("第22条  动态维护", "城镇开发边界调整具有阶段性和动态性，应随着政策要求、建设需求、设施条件和空间数据变化持续更新候选清单。后续可结合规划实施监测评估，对候选地块开发状态、约束变化、项目成熟度和实施成效开展跟踪，形成可更新的边界动态维护机制。"),
        ]),
    ]
    return PlanningPolicyDocument(
        title=title,
        preface="为支撑城镇开发边界调整政策说明和候选调入地块研判工作，依据现有空间分析结果、候选地块识别成果和政策检索依据，形成本报告。本报告重点围绕调整说明、政策依据和优化策略开展整理，用于规划汇报、方案比选和部门沟通，不作为正式规划及行政决策依据。",
        chapters=[
            ReportChapter(title=chapter, sections=[ReportSection(articles=[
                ReportArticle(title=title, content=content) for title, content in items
            ])])
            for chapter, items in articles
        ],
    )


def _sanitize_document(document: PlanningPolicyDocument) -> PlanningPolicyDocument:
    data = document.model_dump()
    def clean(value: str) -> str:
        for term in FORBIDDEN_TERMS:
            value = value.replace(term, "综合评价")
        return value
    data["title"] = clean(data["title"])
    data["preface"] = clean(data.get("preface") or "")
    for chapter in data["chapters"]:
        chapter["title"] = clean(chapter["title"])
        for section in chapter["sections"]:
            for article in section["articles"]:
                article["title"] = clean(article["title"])
                article["content"] = clean(article["content"])
    return PlanningPolicyDocument.model_validate(data)
