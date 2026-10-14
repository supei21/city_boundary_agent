from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Any, Callable

from boundary_agent.llm.client import build_chat_model, model_is_ready
from boundary_agent.rag.chroma_store import get_chroma_store
from boundary_agent.rag.config import PolicyRagSettings
from boundary_agent.rag.hard_constraints import evaluate_hard_constraints
from boundary_agent.rag.parcel_context import summarize_parcel_attributes


MIN_RELEVANCE = 0.30


def _distance_to_score(distance: Any) -> float:
    try:
        value = float(distance)
    except (TypeError, ValueError):
        value = 99.0
    return round(max(0.0, min(1.0, 1.0 - (value**2) / 2.0)), 6)


def _invoke(llm: Any, prompt: str) -> str:
    response = llm.invoke(prompt)
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
    return str(content)


def _extract_json_value(text: str) -> Any:
    clean = str(text or "").strip()
    if clean.startswith("```"):
        clean = clean.strip("`").strip()
        if clean.lower().startswith("json"):
            clean = clean[4:].strip()
    candidates = [clean]
    if "{" in clean and "}" in clean:
        candidates.append(clean[clean.find("{"):clean.rfind("}") + 1])
    if "[" in clean and "]" in clean:
        candidates.append(clean[clean.find("["):clean.rfind("]") + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def _extract_json(text: str) -> dict[str, Any] | None:
    value = _extract_json_value(text)
    return value if isinstance(value, dict) else None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_policy(value: dict[str, Any] | None) -> dict[str, Any]:
    item = value or {}
    feasible = item.get("policy_feasible") if item.get("policy_feasible") in (True, False, None) else None
    return {
        "policy_decision": str(item.get("policy_decision") or "需要补充信息后判断"),
        "policy_feasible": feasible,
        "hard_constraint": bool(item.get("hard_constraint", False)),
        "policy_support_level": str(item.get("policy_support_level") or "未知"),
        "prohibited_constraints": _as_list(item.get("prohibited_constraints")),
        "restrictive_conditions": _as_list(item.get("restrictive_conditions")),
        "supportive_conditions": _as_list(item.get("supportive_conditions")),
        "policy_evidence": _as_list(item.get("policy_evidence")),
        "missing_information": [str(value) for value in _as_list(item.get("missing_information")) if str(value).strip()],
        "policy_reason": str(item.get("policy_reason") or ""),
        "development_conditions": _as_list(item.get("development_conditions")),
    }


def _source_payload(doc: Any, distance: Any) -> dict[str, Any]:
    return {
        "source": str(doc.metadata.get("source", "")),
        "type": "policy",
        "score": _distance_to_score(distance),
        "article_ref": doc.metadata.get("article_ref"),
        "excerpt": str(doc.page_content)[:360],
    }


def answer_policy_question(
    question: str,
    settings: PolicyRagSettings,
    model_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not model_settings or not model_is_ready(model_settings):
        raise ValueError("政策问答需要大模型生成答案，请先在模型设置中选择一个可用模型。")

    pairs = get_chroma_store(settings).similarity_search_with_score(question, settings.retrieval_k, "policy") or []
    context = "\n\n".join(
        f"【政策片段{i}｜来源：{doc.metadata.get('source', '未知')}】\n{doc.page_content}"
        for i, (doc, _) in enumerate(pairs, 1)
    )
    prompt = f"""你是国土空间规划政策问答助手。
只能依据下列 Chroma 检索到的政策原文回答，不得引用外部规则库，不得编造政策名称、文号、条款或数值。
如果原文不足以回答，明确说明缺少什么信息。回答时给出结论、政策依据和必要的适用条件。

【检索到的政策原文】
{context or '未检索到相关政策原文'}

【用户问题】
{question}
"""
    answer = _invoke(build_chat_model(model_settings), prompt)
    sources = [_source_payload(doc, distance) for doc, distance in pairs]
    return {
        "answer": answer,
        "mode": "Chroma政策原文检索 + 大模型回答",
        "best_score": sources[0]["score"] if sources else 0.0,
        "sources": sources,
        "is_fallback": False,
    }


def _parcel_query(parcel: dict[str, Any], context: dict[str, Any], hard: dict[str, Any]) -> str:
    action = parcel.get("proposed_action") or parcel.get("candidate_decision") or "未提供"
    land_type = parcel.get("land_type") or parcel.get("dominant_landuse") or parcel.get("dominant_l") or "未提供"
    triggered = "、".join(item["name"] for item in hard["triggered_constraints"]) or "未触发内置硬约束"
    return "\n".join([
        "城镇开发边界候选地块政策可行性分析",
        f"拟调整方向：{action}",
        f"现状用地：{land_type}",
        f"地块位置：{parcel.get('location') or parcel.get('block_name') or '未提供'}",
        f"确定性硬约束：{triggered}",
        context.get("text") or "未提供多源地块属性",
        "请检索与调入调出、生态保护、永久基本农田、规划符合性、重大项目、节约集约用地和实施条件有关的政策原文。",
    ])


def _safe_parcel(parcel: dict[str, Any]) -> dict[str, Any]:
    excluded = {"geometry", "coordinates", "geom", "wkt", "shape"}
    return {
        str(key): value
        for key, value in parcel.items()
        if not str(key).startswith("_") and str(key).lower() not in excluded
    }


def _merge_hard_constraints(structured: dict[str, Any], hard: dict[str, Any]) -> dict[str, Any]:
    triggered = list(hard.get("triggered_constraints") or [])
    deterministic_items = [
        {
            "constraint": item["name"],
            "triggered_by": f"{item['field']}={item['value']}",
            "policy_name": "内置确定性硬约束",
            "article_ref": "请结合检索到的现行政策原文核验",
            "evidence": item["reason"],
            "constraint_id": item["constraint_id"],
        }
        for item in triggered
    ]
    existing = [item for item in structured["prohibited_constraints"] if item]
    structured["prohibited_constraints"] = deterministic_items + existing
    structured["hard_constraint"] = bool(triggered)
    if triggered:
        structured["policy_feasible"] = False
        structured["policy_decision"] = "存在刚性约束，当前不可行"
        reasons = "；".join(item["reason"] for item in triggered)
        structured["policy_reason"] = reasons + (f"；{structured['policy_reason']}" if structured["policy_reason"] else "")
    deterministic_missing = list(hard.get("missing_information") or [])
    structured["missing_information"] = list(dict.fromkeys(deterministic_missing + structured["missing_information"]))
    return structured


def _policy_markdown(result: dict[str, Any]) -> str:
    feasible = result.get("policy_feasible")
    feasible_text = "可行" if feasible is True else "不可行" if feasible is False else "信息不足，暂不能确定"
    lines = [
        f"### 政策可行性结论：{result['policy_decision']}",
        f"- **政策是否可行**：{feasible_text}",
        f"- **是否存在刚性约束**：{'是' if result['hard_constraint'] else '否'}",
        f"- **政策支持程度**：{result['policy_support_level']}",
        f"- **判断说明**：{result['policy_reason'] or '无'}",
    ]
    sections = [
        ("刚性约束", "prohibited_constraints", "constraint", "未触发内置硬约束"),
        ("有利条件", "supportive_conditions", "condition", "未识别到明确有利条件"),
        ("限制条件", "restrictive_conditions", "condition", "未识别到明确限制条件"),
        ("政策依据", "policy_evidence", "evidence", "检索片段不足，需补充政策依据"),
    ]
    for title, key, field, empty in sections:
        lines.extend(["", f"#### {title}"])
        items = result.get(key) or []
        if not items:
            lines.append(f"- {empty}")
            continue
        for item in items:
            if isinstance(item, dict):
                main = str(item.get(field) or item.get("name") or "")
                source = str(item.get("policy_name") or item.get("source") or "")
                article = str(item.get("article_ref") or "")
                suffix = "；".join(value for value in (source, article) if value)
                lines.append(f"- {main}" + (f"（{suffix}）" if suffix else ""))
            else:
                lines.append(f"- {item}")
    if result.get("development_conditions"):
        lines.extend(["", "#### 地块发展条件解释"])
        for item in result["development_conditions"]:
            if isinstance(item, dict):
                lines.append(
                    f"- **{item.get('dimension', '综合条件')}**：{item.get('assessment', '')}；"
                    f"依据：{item.get('evidence', '')}；影响：{item.get('effect', '中性')}"
                )
    lines.extend(["", "#### 需要补充的信息"])
    missing = result.get("missing_information") or []
    lines.extend([f"- {item}" for item in missing] if missing else ["- 当前关键字段已提供"])
    return "\n".join(lines)


def analyze_policy_feasibility(
    parcel: dict[str, Any],
    settings: PolicyRagSettings,
    model_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not model_settings or not model_is_ready(model_settings):
        raise ValueError("候选地块政策可行性分析需要大模型，请先选择一个可用模型。")

    parcel_context = summarize_parcel_attributes(parcel)
    hard = evaluate_hard_constraints(parcel)
    query = _parcel_query(parcel, parcel_context, hard)
    pairs = get_chroma_store(settings).similarity_search_with_score(query, max(8, settings.retrieval_k), "policy") or []
    sources = [_source_payload(doc, distance) for doc, distance in pairs]
    policy_context = "\n\n".join(
        f"【政策片段{i}｜来源：{doc.metadata.get('source', '未知')}】\n{doc.page_content}"
        for i, (doc, _) in enumerate(pairs, 1)
    )
    prompt = f"""你是“候选地块政策可行性分析”模块。请综合三类输入：
1. 候选地块真实属性；
2. 程序内置的少量确定性硬约束检查结果；
3. Chroma 检索到的政策原文。

判断边界：
- POI、人口、夜间灯光、地形、道路和发展概率只能解释发展条件，不能充当法律依据。
- 重大项目只能作为支持条件或例外审查线索，不能自动覆盖生态红线、永久基本农田等刚性约束。
- hard_constraint 只能依据程序检查结果填写；不得自行创造新的硬约束。
- 政策名称、文号、条款和数值只能来自给定政策片段。证据不足时 policy_feasible 填 null。
- 拟调出地块涉及生态红线或永久基本农田时，不应当把该重叠本身判为“阻止调出”的硬约束。

【候选地块属性】
{json.dumps(_safe_parcel(parcel), ensure_ascii=False, indent=2)}

【多源属性摘要】
{parcel_context['text'] or '暂无可用多源属性'}

【确定性硬约束检查】
{json.dumps(hard, ensure_ascii=False, indent=2)}

【Chroma 检索到的政策原文】
{policy_context or '未检索到相关政策原文'}

只返回合法 JSON，不要返回 Markdown：
{{
  "policy_decision": "明确可行/有条件可行/当前不可行/需要补充信息后判断",
  "policy_feasible": true,
  "hard_constraint": false,
  "policy_support_level": "高/中/低/无/未知",
  "prohibited_constraints": [],
  "supportive_conditions": [{{"condition":"", "triggered_by":""}}],
  "restrictive_conditions": [{{"condition":"", "triggered_by":""}}],
  "policy_evidence": [{{"policy_name":"", "article_ref":"", "evidence":"", "source":""}}],
  "development_conditions": [{{"dimension":"POI/人口/夜光/地形/道路/重大项目", "assessment":"", "evidence":"字段名=实际值", "effect":"有利/限制/中性/信息不足"}}],
  "missing_information": [],
  "policy_reason": ""
}}
"""
    single_model_settings = dict(model_settings)
    single_model_settings.update({"max_tokens": 2500, "timeout": 180, "max_retries": 3})
    json_model = build_chat_model(single_model_settings).bind(
        response_format={"type": "json_object"},
    )
    raw = _invoke(json_model, prompt)
    parsed = _extract_json(raw)
    structured = _normalize_policy(parsed)
    if parsed is None:
        structured["policy_reason"] = "大模型未返回合法的结构化结果。"
        structured["missing_information"].append("重新执行分析或检查所选大模型是否支持 JSON 输出")
    structured = _merge_hard_constraints(structured, hard)

    best_score = sources[0]["score"] if sources else 0.0
    sufficient = bool(sources) and best_score >= MIN_RELEVANCE
    mode = "候选地块属性 + Chroma政策检索 + 大模型结构化判断"
    return {
        "tool_name": "policy_feasibility_rag",
        "tool_version": "4.0",
        "parcel_id": str(parcel.get("parcel_id") or parcel.get("block_id") or ""),
        "policy_analysis": structured,
        "hard_constraint_analysis": hard,
        "retrieval": {
            "query": query,
            "best_policy_score": best_score,
            "local_sufficient": sufficient,
            "mode": mode,
            "source_count": len(sources),
            "sources": sources,
        },
        "parcel_context": parcel_context,
        "answer": _policy_markdown(structured),
        "raw_answer": raw,
    }


def _candidate_policy_report(results: list[dict[str, Any]]) -> str:
    completed = [item for item in results if item.get("success")]
    failed = [item for item in results if not item.get("success")]
    decision_counts: dict[str, int] = {}
    hard_count = 0
    for item in completed:
        analysis = item.get("policy_analysis") or {}
        label = str(analysis.get("policy_decision") or "需要补充信息后判断")
        decision_counts[label] = decision_counts.get(label, 0) + 1
        hard_count += int(bool(analysis.get("hard_constraint")))

    lines = [
        "## 候选地块政策可行性分析完成",
        "",
        f"- 候选地块总数：{len(results)}",
        f"- 成功完成：{len(completed)}",
        f"- 分析失败：{len(failed)}",
        f"- 检出刚性约束：{hard_count}",
        "",
        "### 政策结论统计",
    ]
    if decision_counts:
        lines.extend(f"- {label}：{count}" for label, count in sorted(decision_counts.items()))
    else:
        lines.append("- 暂无有效结论")

    focus = sorted(
        completed,
        key=lambda item: (
            not bool((item.get("policy_analysis") or {}).get("hard_constraint")),
            (item.get("policy_analysis") or {}).get("policy_feasible") is not False,
            str(item.get("block_id") or ""),
        ),
    )[:30]
    if focus:
        lines.extend(["", "### 重点复核地块"])
        for item in focus:
            analysis = item.get("policy_analysis") or {}
            reason = str(analysis.get("policy_reason") or "暂无简要原因")
            lines.append(
                f"- **{item.get('block_id')}（{item.get('block_name') or '未命名'}）**："
                f"{analysis.get('policy_decision') or '待判断'}；{reason}"
            )
    if len(completed) > len(focus):
        lines.extend(["", f"其余 {len(completed) - len(focus)} 个地块的结论已写入地图，切换到“调入决策”后悬停即可查看。"])
    if failed:
        lines.extend(["", "### 未完成地块"])
        lines.extend(
            f"- {item.get('block_id') or '未知地块'}：{item.get('error') or '分析失败'}"
            for item in failed[:20]
        )
    return "\n".join(lines)


def _analyze_candidate_policy_feasibility_legacy(
    parcels: list[dict[str, Any]],
    settings: PolicyRagSettings,
    model_settings: dict[str, Any] | None = None,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Analyze all current candidate parcels and retain one structured result per block."""

    if not parcels:
        raise ValueError("当前没有可分析的候选地块，请先完成候选地块分析。")
    if not model_settings or not model_is_ready(model_settings):
        raise ValueError("候选地块政策可行性分析需要大模型，请先选择一个可用模型。")

    # Initialize the persistent store before worker threads start reading it.
    get_chroma_store(settings)

    def analyze_one(index: int, raw: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        parcel = dict(raw)
        candidate_decision = str(parcel.get("candidate_decision") or "")
        if not parcel.get("proposed_action"):
            parcel["proposed_action"] = "调出" if "调出" in candidate_decision else "调入"
        block_id = str(parcel.get("block_id") or parcel.get("parcel_id") or "")
        try:
            analysis = analyze_policy_feasibility(parcel, settings, model_settings)
            return index, {
                "success": True,
                "block_id": block_id,
                "block_name": parcel.get("block_name") or parcel.get("location"),
                "candidate_decision": candidate_decision,
                "policy_analysis": analysis.get("policy_analysis") or {},
                "hard_constraint_analysis": analysis.get("hard_constraint_analysis") or {},
                "retrieval": analysis.get("retrieval") or {},
                "answer": analysis.get("answer") or "",
            }
        except Exception as exc:
            return index, {
                "success": False,
                "block_id": block_id,
                "block_name": parcel.get("block_name") or parcel.get("location"),
                "candidate_decision": candidate_decision,
                "policy_analysis": {},
                "hard_constraint_analysis": {},
                "retrieval": {},
                "answer": "",
                "error": str(exc),
            }

    ordered: list[dict[str, Any] | None] = [None] * len(parcels)
    worker_count = max(1, min(int(max_workers), len(parcels)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(analyze_one, index, parcel) for index, parcel in enumerate(parcels)]
        for future in as_completed(futures):
            index, item = future.result()
            ordered[index] = item

    results = [item for item in ordered if item is not None]
    completed = sum(bool(item.get("success")) for item in results)
    return {
        "total": len(results),
        "completed": completed,
        "failed": len(results) - completed,
        "results": results,
        "report": _candidate_policy_report(results),
    }


def analyze_candidate_policy_feasibility(
    parcels: list[dict[str, Any]],
    settings: PolicyRagSettings,
    model_settings: dict[str, Any] | None = None,
    max_workers: int = 2,
    batch_size: int = 5,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run one policy retrieval and analyze compact parcel batches."""

    if not parcels:
        raise ValueError("当前没有可分析的候选地块，请先完成候选地块分析。")
    if not model_settings or not model_is_ready(model_settings):
        raise ValueError("候选地块政策可行性分析需要大模型，请选择一个可用模型。")

    total_parcels = len(parcels)

    def publish_progress(
        phase: str,
        message: str,
        *,
        processed: int = 0,
        failed: int = 0,
        percent: float = 0.0,
        current_batch: int | None = None,
        total_batches: int | None = None,
        status: str = "running",
    ) -> None:
        if progress_callback is None:
            return
        progress_callback({
            "phase": phase,
            "status": status,
            "processed": processed,
            "completed": processed,
            "failed": failed,
            "total": total_parcels,
            "percent": round(max(0.0, min(100.0, percent)), 1),
            "current_batch": current_batch,
            "total_batches": total_batches,
            "message": message,
        })

    publish_progress("preparing", "正在整理候选地块属性与本地硬约束", percent=2.0)

    prepared: list[dict[str, Any]] = []
    constraint_names: set[str] = set()
    actions: set[str] = set()
    for index, raw in enumerate(parcels):
        parcel = dict(raw)
        candidate_decision = str(parcel.get("candidate_decision") or "")
        if not parcel.get("proposed_action"):
            parcel["proposed_action"] = "调出" if "调出" in candidate_decision else "调入"
        block_id = str(parcel.get("block_id") or parcel.get("parcel_id") or "")
        context = summarize_parcel_attributes(parcel)
        hard = evaluate_hard_constraints(parcel)
        constraint_names.update(
            str(item.get("name"))
            for item in hard.get("triggered_constraints", [])
            if item.get("name")
        )
        actions.add(str(parcel.get("proposed_action") or candidate_decision or "候选地块"))
        prepared.append({
            "index": index,
            "parcel": parcel,
            "block_id": block_id,
            "block_name": parcel.get("block_name") or parcel.get("location"),
            "candidate_decision": candidate_decision,
            "context": context,
            "hard": hard,
        })

    retrieval_query = "\n".join([
        "城镇开发边界候选地块政策可行性分析",
        f"调整行为：{'、'.join(sorted(actions)) or '调入或调出'}",
        f"已识别硬约束：{'、'.join(sorted(constraint_names)) or '未识别明确硬约束'}",
        "检索城镇开发边界调整、生态保护红线、永久基本农田、地质灾害、规划符合性、重大项目和实施条件相关政策原文。",
    ])
    publish_progress("retrieving", "正在从 Chroma 检索本次分析共用的政策条文", percent=7.0)
    pairs = get_chroma_store(settings).similarity_search_with_score(
        retrieval_query,
        max(8, settings.retrieval_k),
        "policy",
    ) or []
    sources = [_source_payload(doc, distance) for doc, distance in pairs]
    policy_context = "\n\n".join(
        f"【政策片段{i}｜来源：{doc.metadata.get('source', '未知')}】\n{doc.page_content}"
        for i, (doc, _) in enumerate(pairs, 1)
    )
    best_score = sources[0]["score"] if sources else 0.0
    retrieval = {
        "query": retrieval_query,
        "best_policy_score": best_score,
        "local_sufficient": bool(sources) and best_score >= MIN_RELEVANCE,
        "mode": "候选地块属性 + 一次 Chroma 政策检索 + 大模型批量结构化判断",
        "source_count": len(sources),
        "sources": sources,
        "retrieval_reused": True,
    }

    size = max(1, min(int(batch_size), 20))
    batches = [prepared[offset:offset + size] for offset in range(0, len(prepared), size)]
    publish_progress(
        "analyzing",
        f"政策检索完成，准备分 {len(batches)} 批调用大模型",
        percent=12.0,
        total_batches=len(batches),
    )
    model_call_count = 0
    model_call_lock = Lock()

    def analyze_batch(batch_index: int, batch: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
        batch_payload = [
            {
                "block_id": item["block_id"],
                "block_name": item["block_name"],
                "candidate_decision": item["candidate_decision"],
                "proposed_action": item["parcel"].get("proposed_action"),
                "attributes": item["context"].get("flat", {}),
                "hard_constraint_analysis": {
                    "hard_constraint": item["hard"].get("hard_constraint", False),
                    "triggered_constraints": item["hard"].get("triggered_constraints", []),
                    "missing_information": item["hard"].get("missing_information", []),
                },
            }
            for item in batch
        ]
        prompt = f"""你是候选地块政策可行性分析模块。请基于同一组政策原文，逐一分析下面的候选地块。

判断边界：
- 硬约束结果已经由本地确定性代码计算，必须原样尊重，不得覆盖。
- POI、人口、夜光、地形、道路和发展概率只能解释发展条件，不能充当政策依据。
- 重大项目只能作为支持条件，不能自动覆盖生态红线、永久基本农田等刚性约束。
- 政策名称、条款号和数值只能引用给出的政策片段；证据不足时 policy_feasible 必须为 null。
- 每个输入 block_id 必须返回且只能返回一条结果。
- 输出务必简洁：每个列表最多保留 2 项，每项用一句话，不要重复输入属性。

【本次统一检索到的政策原文】
{policy_context or '未检索到相关政策原文'}

【候选地块批次】
{json.dumps(batch_payload, ensure_ascii=False, indent=2)}

只返回合法 JSON，不要输出 Markdown。顶层格式必须为：
{{"results":[{{
  "block_id":"",
  "policy_decision":"明确可行/附条件可行/当前不可行/需要补充信息后判断",
  "policy_feasible":true,
  "hard_constraint":false,
  "policy_support_level":"高/中/低/无/未知",
  "prohibited_constraints":[],
  "supportive_conditions":[{{"condition":"","triggered_by":""}}],
  "restrictive_conditions":[{{"condition":"","triggered_by":""}}],
  "policy_evidence":[{{"policy_name":"","article_ref":"","evidence":"","source":""}}],
  "development_conditions":[{{"dimension":"POI/人口/夜光/地形/道路/重大项目","assessment":"","evidence":"字段名=实际值","effect":"有利/不利/中性/信息不足"}}],
  "missing_information":[],
  "policy_reason":""
}}]}}
"""
        try:
            nonlocal model_call_count
            with model_call_lock:
                model_call_count += 1
            batch_model_settings = dict(model_settings)
            batch_model_settings.update({"max_tokens": 4000, "timeout": 180, "max_retries": 3})
            json_model = build_chat_model(batch_model_settings).bind(
                response_format={"type": "json_object"},
            )
            raw_answer = _invoke(json_model, prompt)
            decoded = _extract_json_value(raw_answer)
            decoded_items = decoded.get("results") if isinstance(decoded, dict) else decoded
            if not isinstance(decoded_items, list):
                raise ValueError("大模型未返回合法的批量 JSON 结果")
            by_id = {
                str(value.get("block_id") or ""): value
                for value in decoded_items
                if isinstance(value, dict)
            }
            output: list[tuple[int, dict[str, Any]]] = []
            for item in batch:
                model_item = by_id.get(item["block_id"])
                if model_item is None:
                    raise ValueError(f"大模型批量结果缺少地块 {item['block_id']}")
                structured = _merge_hard_constraints(_normalize_policy(model_item), item["hard"])
                item_retrieval = dict(retrieval)
                item_retrieval.update({"batch_index": batch_index + 1, "batch_size": len(batch)})
                output.append((item["index"], {
                    "success": True,
                    "block_id": item["block_id"],
                    "block_name": item["block_name"],
                    "candidate_decision": item["candidate_decision"],
                    "policy_analysis": structured,
                    "hard_constraint_analysis": item["hard"],
                    "retrieval": item_retrieval,
                    "answer": _policy_markdown(structured),
                }))
            return output
        except ValueError as exc:
            if len(batch) > 1:
                midpoint = len(batch) // 2
                return (
                    analyze_batch(batch_index, batch[:midpoint])
                    + analyze_batch(batch_index, batch[midpoint:])
                )
            return [
                (item["index"], {
                    "success": False,
                    "block_id": item["block_id"],
                    "block_name": item["block_name"],
                    "candidate_decision": item["candidate_decision"],
                    "policy_analysis": {},
                    "hard_constraint_analysis": item["hard"],
                    "retrieval": dict(retrieval, batch_index=batch_index + 1, batch_size=len(batch)),
                    "answer": "",
                    "error": str(exc),
                })
                for item in batch
            ]
        except Exception as exc:
            error_text = str(exc).lower()
            recoverable = any(marker in error_text for marker in (
                "timed out",
                "timeout",
                "length limit",
                "maximum context",
                "could not parse response content",
            ))
            if recoverable and len(batch) > 1:
                midpoint = len(batch) // 2
                return (
                    analyze_batch(batch_index, batch[:midpoint])
                    + analyze_batch(batch_index, batch[midpoint:])
                )
            return [
                (item["index"], {
                    "success": False,
                    "block_id": item["block_id"],
                    "block_name": item["block_name"],
                    "candidate_decision": item["candidate_decision"],
                    "policy_analysis": {},
                    "hard_constraint_analysis": item["hard"],
                    "retrieval": dict(retrieval, batch_index=batch_index + 1, batch_size=len(batch)),
                    "answer": "",
                    "error": str(exc),
                })
                for item in batch
            ]

    ordered: list[dict[str, Any] | None] = [None] * len(parcels)
    worker_count = max(1, min(int(max_workers), len(batches)))
    processed_count = 0
    failed_count = 0
    finished_batches = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(analyze_batch, batch_index, batch)
            for batch_index, batch in enumerate(batches)
        ]
        for future in as_completed(futures):
            batch_results = future.result()
            for index, item in batch_results:
                ordered[index] = item
            processed_count += len(batch_results)
            failed_count += sum(not bool(item.get("success")) for _, item in batch_results)
            finished_batches += 1
            publish_progress(
                "analyzing",
                f"已完成 {processed_count}/{total_parcels} 个候选地块的政策判断",
                processed=processed_count,
                failed=failed_count,
                percent=12.0 + 83.0 * processed_count / max(total_parcels, 1),
                current_batch=finished_batches,
                total_batches=len(batches),
            )

    results = [item for item in ordered if item is not None]
    completed = sum(bool(item.get("success")) for item in results)
    publish_progress(
        "completed",
        f"政策可行性分析完成：成功 {completed} 个，失败 {len(results) - completed} 个",
        processed=len(results),
        failed=len(results) - completed,
        percent=100.0,
        current_batch=len(batches),
        total_batches=len(batches),
        status="completed",
    )
    return {
        "total": len(results),
        "completed": completed,
        "failed": len(results) - completed,
        "results": results,
        "report": _candidate_policy_report(results),
        "retrieval_count": 1,
        "model_call_count": model_call_count,
        "batch_size": size,
    }


def _optional_bool(value: Any) -> bool | None:
    if value in (True, False, None):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "是", "有", "可行"}:
        return True
    if text in {"false", "0", "否", "无", "不可行"}:
        return False
    return None


def _keyword_recall(keywords: list[Any], text: str) -> float | None:
    words = [str(item).strip() for item in keywords if str(item).strip()]
    return None if not words else sum(word in text for word in words) / len(words)


def _generate_batch_report(results: list[dict[str, Any]]) -> str:
    total = len(results)
    passed = sum(bool(item.get("passed")) for item in results)
    lines = [
        "# 候选地块政策可行性批量分析报告",
        "",
        f"- 地块数量：{total}",
        f"- 分析成功：{passed}",
        f"- 成功率：{passed / total:.1%}" if total else "- 成功率：N/A",
        "",
        "## 分地块结果",
    ]
    for item in results:
        lines.extend([
            "",
            f"### {item['id']}｜{item['name'] or item['parcel_id']}",
            f"- 分析状态：{'成功' if item['passed'] else '失败'}",
            f"- 政策结论：{item['actual_policy_state']}",
            f"- 是否存在刚性约束：{'是' if item['actual_hard_constraint'] else '否'}",
            f"- RAG模式：{item['rag_mode']}",
            "",
            item["detail"],
        ])
    return "\n".join(lines)


def validate_policy_feasibility_batch(
    test_cases: list[dict[str, Any]],
    settings: PolicyRagSettings,
    model_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for index, raw_case in enumerate(test_cases, 1):
        case = raw_case if isinstance(raw_case, dict) else {}
        value = case.get("parcel") or case.get("land_attrs") or case
        parcel = value if isinstance(value, dict) else {}
        case_id = str(case.get("case_id") or case.get("id") or f"TC{index:03d}")
        case_name = str(case.get("case_name") or case.get("name") or "")
        expected_feasible = _optional_bool(case.get("expected_policy_feasible"))
        expected_hard = _optional_bool(case.get("expected_hard_constraint"))
        input_valid = bool(parcel) and bool(parcel.get("block_id") or parcel.get("parcel_id"))
        try:
            analysis = analyze_policy_feasibility(parcel, settings, model_settings)
            structured = analysis["policy_analysis"]
            hard = analysis["hard_constraint_analysis"]
            retrieval = analysis["retrieval"]
            actual_feasible = structured.get("policy_feasible")
            actual_hard = bool(structured.get("hard_constraint"))
            checks = [input_valid, bool(retrieval.get("sources"))]
            if expected_feasible is not None:
                checks.append(actual_feasible is expected_feasible)
            if expected_hard is not None:
                checks.append(actual_hard is expected_hard)
            expected_constraints = case.get("expected_hard_constraints") or case.get("expected_rules") or []
            actual_ids = {item["constraint_id"] for item in hard.get("triggered_constraints", [])}
            constraint_recall = _keyword_recall(expected_constraints, " ".join(actual_ids))
            if constraint_recall is not None:
                checks.append(constraint_recall >= 1.0)
            detail = str(analysis.get("answer", ""))
            results.append({
                "id": case_id,
                "name": case_name,
                "parcel_id": str(parcel.get("parcel_id") or parcel.get("block_id") or ""),
                "parcel_type": str(parcel.get("proposed_action") or parcel.get("candidate_decision") or "候选地块"),
                "expected_policy_state": str(case.get("expected_policy_state") or ""),
                "actual_policy_state": str(structured.get("policy_decision") or ""),
                "expected_policy_feasible": expected_feasible,
                "actual_policy_feasible": actual_feasible,
                "expected_hard_constraint": expected_hard,
                "actual_hard_constraint": actual_hard,
                "gate_match": all(checks[2:]) if len(checks) > 2 else None,
                "input_valid": input_valid,
                "evidence_valid": bool(retrieval.get("sources")),
                "constraint_recall": constraint_recall,
                "rule_recall": None,
                "policy_recall": _keyword_recall(case.get("expected_policy_keywords", []), detail),
                "rag_mode": retrieval.get("mode", ""),
                "local_sufficient": bool(retrieval.get("local_sufficient")),
                "passed": all(checks),
                "detail": detail,
                "structured_result": structured,
            })
        except Exception as exc:
            results.append({
                "id": case_id,
                "name": case_name,
                "parcel_id": str(parcel.get("parcel_id") or parcel.get("block_id") or ""),
                "parcel_type": "候选地块",
                "expected_policy_state": str(case.get("expected_policy_state") or ""),
                "actual_policy_state": "分析失败",
                "expected_policy_feasible": expected_feasible,
                "actual_policy_feasible": None,
                "expected_hard_constraint": expected_hard,
                "actual_hard_constraint": False,
                "gate_match": False,
                "input_valid": input_valid,
                "evidence_valid": False,
                "constraint_recall": None,
                "rule_recall": None,
                "policy_recall": None,
                "rag_mode": "ERROR",
                "local_sufficient": False,
                "passed": False,
                "detail": str(exc),
                "structured_result": {},
            })

    total = len(results)
    passed = sum(bool(item["passed"]) for item in results)
    strict = [item for item in results if item.get("gate_match") is not None]
    return {
        "tool_name": "policy_feasibility_batch_validation",
        "tool_version": "4.0",
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total else 0.0,
        "gate_match_rate": sum(bool(item["gate_match"]) for item in strict) / len(strict) if strict else None,
        "evidence_valid_rate": sum(bool(item["evidence_valid"]) for item in results) / total if total else 0.0,
        "input_valid_rate": sum(bool(item["input_valid"]) for item in results) / total if total else 0.0,
        "results": results,
        "report": _generate_batch_report(results),
    }
