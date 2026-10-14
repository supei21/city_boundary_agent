from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boundary_agent.paths import PROJECT_ROOT, RAG_PROJECT_DIR, RUNTIME_DIR


@dataclass(frozen=True)
class PolicyRagSettings:
    enabled: bool = True
    policy_dir: Path = RAG_PROJECT_DIR / "城镇开发边界政策库"
    index_path: Path = RUNTIME_DIR / "policy_rag" / "policy_index.json"
    chroma_path: Path = RUNTIME_DIR / "policy_rag" / "chroma_db"
    embedding_model: Path = RAG_PROJECT_DIR / "models" / "bge-small-zh-v1.5"
    backend: str = "chroma"
    retrieval_k: int = 6
    chunk_size: int = 900
    chunk_overlap: int = 120


def settings_from_config(config: dict[str, Any]) -> PolicyRagSettings:
    payload = config.get("policy_rag", {})
    policy_dir = _resolve_path(payload.get("policy_dir"), RAG_PROJECT_DIR / "城镇开发边界政策库")
    return PolicyRagSettings(
        enabled=bool(payload.get("enabled", True)),
        policy_dir=policy_dir,
        index_path=_resolve_path(payload.get("index_path"), RUNTIME_DIR / "policy_rag" / "policy_index.json"),
        chroma_path=_resolve_path(payload.get("chroma_path"), RUNTIME_DIR / "policy_rag" / "chroma_db"),
        embedding_model=_resolve_path(payload.get("embedding_model"), RAG_PROJECT_DIR / "models" / "bge-small-zh-v1.5"),
        backend=str(payload.get("backend", "chroma")),
        retrieval_k=int(payload.get("retrieval_k", 6)),
        chunk_size=int(payload.get("chunk_size", 900)),
        chunk_overlap=int(payload.get("chunk_overlap", 120)),
    )


def _resolve_path(value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value else default
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
