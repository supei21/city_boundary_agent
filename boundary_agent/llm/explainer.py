from __future__ import annotations

from boundary_agent.llm.client import build_chat_model, get_api_key, merge_model_settings


def explain_with_llm(decision: dict, model_config: dict | None = None) -> str | None:
    """Optional wording layer. It cannot change the deterministic decision."""
    settings = merge_model_settings(model_config)
    if not settings["enabled"]:
        return None
    key_setting = settings.get("api_key_env", "").strip()
    api_key = get_api_key(settings)
    if not api_key:
        return (
            "大模型未调用：请在 config/model_local.py 中配置密钥，"
            f"或设置环境变量 {key_setting}"
        )
    try:
        model = build_chat_model(settings)
        prompt = (
            "你是城镇开发边界规划决策解释助手。只能基于给定JSON解释，不能修改decision、评分、约束、"
            "政策规则命中和置信度，不能补充JSON中不存在的事实。若JSON含policy_sources或policy_matched_rules，"
            "请优先引用这些政策依据说明拟调入地块是否符合规定；若依据不足，请明确需要人工复核。"
            "请用180字以内中文说明，明确这是辅助建议而非正式规划审批结论。\nJSON："
            + str(decision)
        )
        return model.invoke(prompt).content
    except Exception as exc:
        return f"大模型解释不可用：{exc}"
