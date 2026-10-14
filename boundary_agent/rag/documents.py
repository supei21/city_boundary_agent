from __future__ import annotations

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
    """Load policy originals only; JSON rule libraries are intentionally excluded."""
    return split_documents(load_policy_files(settings.policy_dir), settings.chunk_size, settings.chunk_overlap)


def load_policy_files(policy_dir: Path) -> list[PolicyDocument]:
    if not policy_dir.exists():
        return []
    docs: list[PolicyDocument] = []
    for path in sorted(policy_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        text = read_policy_text(path)
        if text.strip():
            source = str(path.relative_to(policy_dir))
            docs.append(PolicyDocument(clean_text(text), {"source": source, "type": "policy"}))
    return docs


def read_policy_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return read_docx_text(path)
    if path.suffix.lower() == ".txt":
        return read_text_file(path)
    if path.suffix.lower() == ".pdf":
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
            chunks.append(PolicyDocument(doc.content[start:end], {**doc.metadata, "chunk": index}))
            if end == len(doc.content):
                break
            start = max(0, end - chunk_overlap)
            index += 1
    return chunks


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
