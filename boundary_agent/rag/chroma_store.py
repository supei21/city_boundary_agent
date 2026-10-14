from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from boundary_agent.rag.config import PolicyRagSettings
from boundary_agent.rag.documents import load_documents


def _source_fingerprint(settings: PolicyRagSettings) -> str:
    digest = hashlib.sha256()
    paths = [p for p in settings.policy_dir.rglob("*") if p.is_file()]
    for path in sorted(paths, key=lambda p: str(p).lower()):
        stat = path.stat()
        digest.update(str(path.resolve()).encode("utf-8"))
        digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode("ascii"))
    digest.update(f"{settings.chunk_size}:{settings.chunk_overlap}".encode("ascii"))
    return digest.hexdigest()


def _score(distance: float) -> float:
    return round(max(0.0, min(1.0, 1.0 - (float(distance) ** 2) / 2.0)), 6)


class ChromaPolicyStore:
    def __init__(self, settings: PolicyRagSettings):
        self.settings = settings
        self._db = self._load_or_build()

    def _embeddings(self):
        if not self.settings.embedding_model.exists():
            raise FileNotFoundError(f"Embedding 模型不存在：{self.settings.embedding_model}")
        from langchain_community.embeddings import SentenceTransformerEmbeddings

        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"
        return SentenceTransformerEmbeddings(
            model_name=str(self.settings.embedding_model),
            model_kwargs={"device": device},
            encode_kwargs={"normalize_embeddings": True},
        )

    def _load_or_build(self):
        from langchain_chroma import Chroma
        from langchain_core.documents import Document

        persist = self.settings.chroma_path
        marker = persist / "source_hash.json"
        fingerprint = _source_fingerprint(self.settings)
        embeddings = self._embeddings()
        if marker.exists():
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
                if payload.get("fingerprint") == fingerprint:
                    return Chroma(
                        collection_name="city_boundary_policy",
                        persist_directory=str(persist),
                        embedding_function=embeddings,
                    )
            except Exception:
                pass

        if persist.exists():
            shutil.rmtree(persist)
        persist.mkdir(parents=True, exist_ok=True)
        source_docs = load_documents(self.settings)
        docs = [Document(page_content=item.content, metadata=item.metadata) for item in source_docs]
        if not docs:
            raise RuntimeError("政策库中没有可用于构建 Chroma 的文档")
        db = Chroma.from_documents(
            documents=docs,
            embedding=embeddings,
            collection_name="city_boundary_policy",
            persist_directory=str(persist),
        )
        marker.write_text(
            json.dumps({"fingerprint": fingerprint, "document_count": len(docs)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return db

    def search(self, query: str, k: int, item_type: str | None = None) -> list[dict[str, Any]]:
        where = {"type": item_type} if item_type else None
        pairs = self._db.similarity_search_with_score(query, k=k, filter=where)
        return [
            {
                "source": doc.metadata.get("source", ""),
                "type": doc.metadata.get("type", ""),
                "score": _score(distance),
                "distance": round(float(distance), 6),
                "rule_id": doc.metadata.get("rule_id"),
                "policy_name": doc.metadata.get("policy_name"),
                "category": doc.metadata.get("category"),
                "article_ref": doc.metadata.get("article_ref"),
                "action": doc.metadata.get("action"),
                "content": doc.page_content,
            }
            for doc, distance in pairs
        ]

    def similarity_search_with_score(self, query: str, k: int, item_type: str):
        return self._db.similarity_search_with_score(query, k=k, filter={"type": item_type})

    def dual_search(self, query: str, k: int) -> list[dict[str, Any]]:
        return self.search(query, k, "policy")


_STORES: dict[tuple[str, ...], ChromaPolicyStore] = {}


def get_chroma_store(settings: PolicyRagSettings) -> ChromaPolicyStore:
    key = (
        str(settings.policy_dir.resolve()),
        str(settings.embedding_model.resolve()),
        str(settings.chroma_path.resolve()),
        str(settings.chunk_size),
        str(settings.chunk_overlap),
    )
    store = _STORES.get(key)
    if store is None:
        store = ChromaPolicyStore(settings)
        _STORES[key] = store
    return store

