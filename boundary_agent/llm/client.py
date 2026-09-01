"""统一创建LangChain聊天模型，集中处理参数与Key。"""

from __future__ import annotations

import os
from typing import Any

from config.model import MODEL_CONFIG, MODEL_PROFILES

from boundary_agent.llm import user_models


LOCAL_RULES_MODEL_ID = "local-rules"
DEFAULT_MODEL_ID = "default"


def merge_model_settings(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    return {**MODEL_CONFIG, **(overrides or {})}


def get_api_key(settings: dict[str, Any]) -> str | None:
    direct = str(settings.get("api_key") or "").strip()
    key_setting = str(settings.get("api_key_env") or "").strip()
    if direct:
        return direct
    if key_setting.startswith("sk-"):
        return key_setting
    return os.getenv(key_setting) if key_setting else None


def model_is_ready(overrides: dict[str, Any] | None = None) -> bool:
    settings = merge_model_settings(overrides)
    return bool(settings.get("enabled") and get_api_key(settings))


def get_model_settings(model_id: str) -> dict[str, Any]:
    """Resolve a public model id to server-side settings without exposing keys."""

    if model_id == LOCAL_RULES_MODEL_ID:
        return {**MODEL_CONFIG, "enabled": False}
    if model_id == DEFAULT_MODEL_ID:
        return dict(MODEL_CONFIG)
    profile = MODEL_PROFILES.get(model_id)
    if profile is not None:
        return {**MODEL_CONFIG, **profile}
    user = user_models.find_user_model(model_id)
    if user is not None:
        return {
            **MODEL_CONFIG,
            "enabled": True,
            "label": user["label"],
            "model": user["model"],
            "base_url": user["base_url"],
            "api_key": user["api_key"],
            "api_key_env": "",  # 防止环境变量回退混入自定义模型
        }
    raise KeyError(f"未知模型：{model_id}")


def list_model_options() -> list[dict[str, Any]]:
    """Return the safe model catalog consumed by the frontend."""

    options = [
        {
            "id": LOCAL_RULES_MODEL_ID,
            "label": "本地规则",
            "description": "不调用大模型，使用可复现的本地分析流程",
            "ready": True,
            "kind": "local",
            "base_url": "",
        }
    ]
    configured_profiles = [(DEFAULT_MODEL_ID, MODEL_CONFIG), *MODEL_PROFILES.items()]
    for profile_id, profile in configured_profiles:
        settings = {**MODEL_CONFIG, **profile}
        model_name = str(settings.get("model") or profile_id)
        options.append(
            {
                "id": profile_id,
                "label": str(settings.get("label") or model_name),
                "description": str(settings.get("description") or f"使用 {model_name} 运行 ReAct 工具流程"),
                "ready": model_is_ready(settings),
                "kind": "llm",
                "base_url": str(settings.get("base_url") or ""),
            }
        )
    for user in user_models.list_user_models():
        options.append(
            {
                "id": user["id"],
                "label": user["label"],
                "description": f"{user['model']} · {user['base_url']}",
                "ready": bool(user.get("api_key")),
                "kind": "llm",
                "base_url": user["base_url"],
            }
        )
    return options


def get_default_model_id() -> str:
    for option in list_model_options():
        if option["kind"] == "llm" and option["ready"]:
            return str(option["id"])
    return LOCAL_RULES_MODEL_ID


def build_chat_model(overrides: dict[str, Any] | None = None):
    from langchain_openai import ChatOpenAI

    settings = merge_model_settings(overrides)
    kwargs = {
        "model": settings["model"],
        "api_key": get_api_key(settings),
        "temperature": settings["temperature"],
        "max_tokens": settings["max_tokens"],
        "timeout": settings["timeout"],
        "max_retries": settings["max_retries"],
        "model_kwargs": settings.get("model_kwargs", {}),
    }
    if settings.get("base_url"):
        kwargs["base_url"] = settings["base_url"]
    return ChatOpenAI(**kwargs)
