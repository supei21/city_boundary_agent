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
            "你是规划决策解释助手。只能解释给定JSON，不能修改decision、评分、约束和置信度，"
            "不能补充JSON中不存在的事实。请用120字以内中文说明，明确这是辅助建议而非正式规划结论。\nJSON："
            + str(decision)
        )
        return model.invoke(prompt).content
    except Exception as exc:
        return f"大模型解释不可用：{exc}"
