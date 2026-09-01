"""用户自定义模型配置，持久化到 runtime/user_models.json（已 gitignore）。

chat 流在 daemon 线程中运行，与事件循环上的 workspace 读取并发，
因此这里用模块级锁 + 内存缓存保证读写一致。
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from typing import Any
from uuid import uuid4

from boundary_agent.paths import USER_MODELS_FILE


class UserModelConflictError(Exception):
    """新增模型与已有条目冲突（同名 id 或相同 model+base_url）。"""


_store_lock = threading.Lock()
_cache: list[dict[str, Any]] | None = None


def _load_locked() -> list[dict[str, Any]]:
    global _cache
    if _cache is not None:
        return _cache
    if USER_MODELS_FILE.exists():
        try:
            data = json.loads(USER_MODELS_FILE.read_text(encoding="utf-8"))
            _cache = data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            # 文件损坏时降级为空目录，不让整个 API 崩溃
            _cache = []
    else:
        _cache = []
    return _cache


def _save_locked(models: list[dict[str, Any]]) -> None:
    USER_MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = USER_MODELS_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(models, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, USER_MODELS_FILE)


def _make_model_id(label: str, existing_ids: set[str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    base = f"custom-{slug[:40]}" if slug else f"custom-{uuid4().hex[:8]}"
    if base in existing_ids:
        raise UserModelConflictError(f"已存在同名模型：{label}")
    return base


def list_user_models() -> list[dict[str, Any]]:
    with _store_lock:
        return [dict(item) for item in _load_locked()]


def find_user_model(model_id: str) -> dict[str, Any] | None:
    with _store_lock:
        return next((dict(item) for item in _load_locked() if item.get("id") == model_id), None)


def add_user_model(label: str, model: str, base_url: str, api_key: str) -> dict[str, Any]:
    global _cache
    with _store_lock:
        models = _load_locked()
        if any(item["model"] == model and item["base_url"] == base_url for item in models):
            raise UserModelConflictError("相同模型名与网址的模型已存在")
        record = {
            "id": _make_model_id(label, {item["id"] for item in models}),
            "label": label,
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        models.append(record)
        _cache = models
        _save_locked(models)
        return dict(record)


def delete_user_model(model_id: str) -> bool:
    global _cache
    with _store_lock:
        models = _load_locked()
        remaining = [item for item in models if item["id"] != model_id]
        if len(remaining) == len(models):
            return False
        _cache = remaining
        _save_locked(remaining)
        return True
