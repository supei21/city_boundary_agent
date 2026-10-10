from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from pathlib import Path
from typing import Any

from boundary_agent.rag.config import PolicyRagSettings
from boundary_agent.rag.documents import SUPPORTED_EXTENSIONS, clean_text, read_policy_text
from boundary_agent.rag.vector_store import search_policy_documents


def list_policy_library(settings: PolicyRagSettings) -> list[dict[str, Any]]:
    if not settings.policy_dir.exists():
        return []
    items = []
    for path in sorted(settings.policy_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        source = str(path.relative_to(settings.policy_dir))
        text = clean_text(read_policy_text(path))
        items.append(
            {
                "id": encode_policy_id(source),
                "name": path.name,
                "source": source,
                "category": path.parent.name if path.parent != settings.policy_dir else "",
                "suffix": path.suffix.lower(),
                "size": path.stat().st_size,
                "text_length": len(text),
                "excerpt": make_excerpt(text),
            }
        )
    return items


def get_policy_document(settings: PolicyRagSettings, document_id: str) -> dict[str, Any] | None:
    source = decode_policy_id(document_id)
    path = (settings.policy_dir / source).resolve()
    try:
        path.relative_to(settings.policy_dir.resolve())
    except ValueError:
        return None
    if not path.exists() or not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return None
    text = clean_text(read_policy_text(path))
    return {
        "id": document_id,
        "name": path.name,
        "source": source,
        "category": path.parent.name if path.parent != settings.policy_dir else "",
        "suffix": path.suffix.lower(),
        "size": path.stat().st_size,
        "text_length": len(text),
        "content": text,
    }


def search_policy_library(settings: PolicyRagSettings, query: str, k: int | None = None) -> list[dict[str, Any]]:
    results = []
    for item in search_policy_documents(query, settings, k or settings.retrieval_k):
        source = str(item.get("source") or "")
        results.append(
            {
                "document_id": encode_policy_id(source) if source and not source.startswith("policy_rules.json_") else None,
                "source": source,
                "type": item.get("type", ""),
                "score": item.get("score", 0),
                "rule_id": item.get("rule_id"),
                "article_ref": item.get("article_ref"),
                "excerpt": make_excerpt(str(item.get("content") or ""), query),
            }
        )
    return results


def encode_policy_id(source: str) -> str:
    return urlsafe_b64encode(source.encode("utf-8")).decode("ascii").rstrip("=")


def decode_policy_id(document_id: str) -> str:
    padding = "=" * (-len(document_id) % 4)
    return urlsafe_b64decode((document_id + padding).encode("ascii")).decode("utf-8")


def make_excerpt(text: str, query: str = "", length: int = 220) -> str:
    if not text:
        return ""
    start = 0
    query = query.strip()
    if query:
        positions = [text.find(part) for part in query if part.strip()]
        positions = [pos for pos in positions if pos >= 0]
        if positions:
            start = max(0, min(positions) - 50)
    excerpt = text[start : start + length].replace("\n", " ")
    return excerpt + ("..." if start + length < len(text) else "")
