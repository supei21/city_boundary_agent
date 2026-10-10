from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from boundary_agent.rag.config import PolicyRagSettings
from boundary_agent.rag.documents import PolicyDocument, load_documents
from boundary_agent.rag.embeddings import cosine_similarity, embed_text


_INDEX_CACHE: dict[tuple[str, str, int, int, int], dict[str, Any]] = {}
_SEARCH_CACHE: dict[tuple[str, str, int, int, int], "PolicySearchBackend"] = {}


def search_policy_documents(query: str, settings: PolicyRagSettings, k: int | None = None) -> list[dict[str, Any]]:
    backend = load_search_backend(settings)
    return backend.search(query, max(1, k or settings.retrieval_k))


def load_search_backend(settings: PolicyRagSettings) -> "PolicySearchBackend":
    cache_key = _settings_cache_key(settings)
    cached = _SEARCH_CACHE.get(cache_key)
    if cached is not None:
        return cached
    index = load_or_build_index(settings)
    backend = PolicySearchBackend(index.get("documents", []))
    _SEARCH_CACHE[cache_key] = backend
    return backend


@dataclass
class PolicySearchBackend:
    documents: list[dict[str, Any]]

    def __post_init__(self) -> None:
        self._nearest = None
        self._matrix = None
        self._use_nearest_neighbors = False
        self._build_nearest_neighbors()

    def search(self, query: str, k: int) -> list[dict[str, Any]]:
        if not self.documents:
            return []
        query_vector = embed_text(query)
        candidate_count = min(len(self.documents), max(k * 4, k + 12))
        if self._use_nearest_neighbors:
            scored = self._search_nearest_neighbors(query_vector, candidate_count)
        else:
            scored = self._search_linear(query_vector, candidate_count)
        balanced = _select_balanced_results(scored, k)
        return [_format_result(score, item) for score, item in balanced]

    def _build_nearest_neighbors(self) -> None:
        try:
            import numpy as np
            from sklearn.neighbors import NearestNeighbors
        except Exception:
            return
        matrix = np.array([item["embedding"] for item in self.documents], dtype="float32")
        if matrix.size == 0:
            return
        nearest = NearestNeighbors(metric="cosine", algorithm="brute")
        nearest.fit(matrix)
        self._matrix = matrix
        self._nearest = nearest
        self._use_nearest_neighbors = True

    def _search_nearest_neighbors(self, query_vector: list[float], k: int) -> list[tuple[float, dict[str, Any]]]:
        import numpy as np

        limit = min(k, len(self.documents))
        distances, indices = self._nearest.kneighbors(
            np.array([query_vector], dtype="float32"),
            n_neighbors=limit,
        )
        return [
            (1.0 - float(distance), self.documents[int(index)])
            for distance, index in zip(distances[0], indices[0])
        ]

    def _search_linear(self, query_vector: list[float], k: int) -> list[tuple[float, dict[str, Any]]]:
        scored = []
        for item in self.documents:
            score = cosine_similarity(query_vector, item["embedding"])
            scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[:k]


def _format_result(score: float, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": item["metadata"].get("source", ""),
        "type": item["metadata"].get("type", ""),
        "score": round(score, 6),
        "rule_id": item["metadata"].get("rule_id"),
        "policy_name": item["metadata"].get("policy_name"),
        "category": item["metadata"].get("category"),
        "article_ref": item["metadata"].get("article_ref"),
        "content": item["content"],
    }


def _select_balanced_results(
    scored: list[tuple[float, dict[str, Any]]],
    k: int,
) -> list[tuple[float, dict[str, Any]]]:
    """Keep high-scoring rules while guaranteeing readable policy-file evidence."""

    if k <= 0:
        return []
    policies: list[tuple[float, dict[str, Any]]] = []
    rules: list[tuple[float, dict[str, Any]]] = []
    others: list[tuple[float, dict[str, Any]]] = []
    seen_policy_sources: set[str] = set()
    for pair in scored:
        item_type = str(pair[1].get("metadata", {}).get("type") or "")
        if item_type == "policy":
            source = str(pair[1].get("metadata", {}).get("source") or "")
            if source in seen_policy_sources:
                continue
            seen_policy_sources.add(source)
            policies.append(pair)
        elif item_type == "rule":
            rules.append(pair)
        else:
            others.append(pair)

    policy_target = min(len(policies), max(1, k // 2))
    rule_target = min(len(rules), k - policy_target)
    selected = policies[:policy_target] + rules[:rule_target]
    selected_ids = {id(item) for _, item in selected}
    for pair in sorted(scored + others, key=lambda value: value[0], reverse=True):
        if len(selected) >= k:
            break
        if id(pair[1]) in selected_ids:
            continue
        selected.append(pair)
        selected_ids.add(id(pair[1]))
    return sorted(selected, key=lambda value: value[0], reverse=True)[:k]


def search_policy_documents_linear(query: str, settings: PolicyRagSettings, k: int | None = None) -> list[dict[str, Any]]:
    index = load_or_build_index(settings)
    query_vector = embed_text(query)
    scored = []
    for item in index.get("documents", []):
        score = cosine_similarity(query_vector, item["embedding"])
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [_format_result(score, item) for score, item in scored[: max(1, k or settings.retrieval_k)]]


def load_or_build_index(settings: PolicyRagSettings) -> dict[str, Any]:
    cache_key = _settings_cache_key(settings)
    cached = _INDEX_CACHE.get(cache_key)
    if cached is not None:
        return cached

    docs = load_documents(settings)
    source_hash = documents_hash(docs)
    if settings.index_path.exists():
        try:
            payload = json.loads(settings.index_path.read_text(encoding="utf-8"))
            if payload.get("source_hash") == source_hash:
                _INDEX_CACHE[cache_key] = payload
                return payload
        except json.JSONDecodeError:
            pass
    payload = build_index_payload(docs, source_hash)
    settings.index_path.parent.mkdir(parents=True, exist_ok=True)
    settings.index_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _INDEX_CACHE[cache_key] = payload
    return payload


def build_index_payload(docs: list[PolicyDocument], source_hash: str) -> dict[str, Any]:
    return {
        "source_hash": source_hash,
        "count": len(docs),
        "documents": [
            {
                "content": doc.content,
                "metadata": doc.metadata,
                "embedding": embed_text(doc.content),
            }
            for doc in docs
        ],
    }


def documents_hash(docs: list[PolicyDocument]) -> str:
    digest = hashlib.md5()
    for doc in docs:
        digest.update(doc.content.encode("utf-8"))
        digest.update(json.dumps(doc.metadata, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return digest.hexdigest()


def _settings_cache_key(settings: PolicyRagSettings) -> tuple[str, str, int, int, int]:
    return (
        str(settings.policy_dir.resolve()),
        str(settings.rules_path.resolve()),
        settings.retrieval_k,
        settings.chunk_size,
        settings.chunk_overlap,
    )
