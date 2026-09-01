"""Load safe defaults and optional machine-local model overrides."""

from __future__ import annotations

from config.model_example import MODEL_CONFIG as DEFAULT_MODEL_CONFIG
from config.model_example import MODEL_PROFILES as DEFAULT_MODEL_PROFILES

try:
    from config.model_local import MODEL_CONFIG as LOCAL_MODEL_CONFIG
except ImportError:
    LOCAL_MODEL_CONFIG = {}

try:
    from config.model_local import MODEL_PROFILES as LOCAL_MODEL_PROFILES
except ImportError:
    LOCAL_MODEL_PROFILES = {}

MODEL_CONFIG = {**DEFAULT_MODEL_CONFIG, **LOCAL_MODEL_CONFIG}
MODEL_PROFILES = {**DEFAULT_MODEL_PROFILES, **LOCAL_MODEL_PROFILES}
