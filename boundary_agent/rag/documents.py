from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from boundary_agent.rag.config import PolicyRagSettings


SUPPORTED_EXTENSIONS = {".docx", ".txt", ".pdf"}


@dataclass(frozen=True)
class PolicyDocument:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


def load_documents(settings: PolicyRagSettings) -> list[PolicyDocument]:
    docs = []
    docs.extend(load_policy_files(settings.policy_dir))
    docs.extend(load_rule_documents(settings.rules_path))
    return split_documents(docs, settings.chunk_size, settings.chunk_overlap)


def load_policy_files(policy_dir: Path) -> list[PolicyDocument]:
    if not policy_dir.exists():
        return []
    docs: list[PolicyDocument] = []
    for path in sorted(policy_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        text = read_policy_text(path)
        if not text.strip():
            continue
        source = str(path.relative_to(policy_dir))
        docs.append(PolicyDocument(clean_text(text), {"source": source, "type": "policy"}))
    return docs


def read_policy_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return read_docx_text(path)
    if suffix == ".txt":
        return read_text_file(path)
    if suffix == ".pdf":
        return read_pdf_text(path)
    return ""


def read_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for paragraph in root.findall(".//w:p", namespace):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace))
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)


def read_text_file(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def read_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def load_rule_documents(rules_path: Path) -> list[PolicyDocument]:
    if not rules_path.exists():
        return []
    payload = json.loads(rules_path.read_text(encoding="utf-8"))
    docs = []
    for rule in payload.get("rules", []):
        rule_id = rule.get("rule_id", "")
        content = "\n".join(
            [
                f"规则ID: {rule_id}",
                f"政策: {rule.get('policy_name', '')}",
                f"类别: {rule.get('category', '')}",
                f"条款类型: {rule.get('clause_type', '')}",
                f"行动: {rule.get('action', '')}",
                f"依据: {rule.get('source_text', '')}",
                f"条款引用: {rule.get('article_ref', '')}",
                f"触发条件: {json.dumps(rule.get('trigger_conditions', {}), ensure_ascii=False)}",
            ]
        )
        docs.append(
            PolicyDocument(
                content=content,
                metadata={
                    "source": f"policy_rules.json_{rule_id}",
                    "type": "rule",
                    "rule_id": rule_id,
                    "policy_name": rule.get("policy_name", ""),
                    "category": rule.get("category", ""),
                    "clause_type": rule.get("clause_type", ""),
                    "action": rule.get("action", ""),
                    "score_adjustment": rule.get("score_adjustment", 0),
                    "article_ref": rule.get("article_ref", ""),
                },
            )
        )
    return docs


def split_documents(docs: list[PolicyDocument], chunk_size: int, chunk_overlap: int) -> list[PolicyDocument]:
    chunks: list[PolicyDocument] = []
    for doc in docs:
        if len(doc.content) <= chunk_size:
            chunks.append(doc)
            continue
        start = 0
        index = 0
        while start < len(doc.content):
            end = min(len(doc.content), start + chunk_size)
            metadata = {**doc.metadata, "chunk": index}
            chunks.append(PolicyDocument(doc.content[start:end], metadata))
            if end == len(doc.content):
                break
            start = max(0, end - chunk_overlap)
            index += 1
    return chunks


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
