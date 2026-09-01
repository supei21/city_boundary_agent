"""Safe model configuration template.

Copy overrides to ``model_local.py`` for local development. The local file is
ignored by version control so API keys do not enter the repository.
"""

MODEL_CONFIG = {
    "enabled": False,
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com",
    "api_key": "",
    "api_key_env": "DEEPSEEK_API_KEY",
    "temperature": 0.0,
    "max_tokens": 800,
    "timeout": 60,
    "max_retries": 2,
    "model_kwargs": {},
}

# Optional additional models shown in the UI. Each profile inherits omitted
# connection settings (such as base_url and api_key_env) from MODEL_CONFIG.
MODEL_PROFILES = {
    # "reasoning": {
    #     "label": "Reasoning model",
    #     "model": "your-reasoning-model",
    # },
}
