from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boundary_agent.paths import PROJECT_ROOT, RAG_PROJECT_DIR, RUNTIME_DIR


@dataclass(frozen=True)
class PolicyRagSettings:
    enabled: bool = True
    policy_dir: Path = RAG_PROJECT_DIR / "城镇边界政策库"
    rules_path: Path = RAG_PROJECT_DIR / "城镇边界政策库" / "policy_rules.json"
    index_path: Path = RUNTIME_DIR / "policy_rag" / "policy_index.json"
    retrieval_k: int = 6
    chunk_size: int = 900
    chunk_overlap: int = 120


def settings_from_config(config: dict[str, Any]) -> PolicyRagSettings:
    payload = config.get("policy_rag", {})
    policy_dir = _resolve_path(payload.get("policy_dir"), RAG_PROJECT_DIR / "城镇边界政策库")
    return PolicyRagSettings(
        enabled=bool(payload.get("enabled", True)),
        policy_dir=policy_dir,
        rules_path=_resolve_path(payload.get("rules_path"), policy_dir / "policy_rules.json"),
        index_path=_resolve_path(payload.get("index_path"), RUNTIME_DIR / "policy_rag" / "policy_index.json"),
        retrieval_k=int(payload.get("retrieval_k", 6)),
        chunk_size=int(payload.get("chunk_size", 900)),
        chunk_overlap=int(payload.get("chunk_overlap", 120)),
    )


def _resolve_path(value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value else default
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
