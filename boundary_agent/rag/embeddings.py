from __future__ import annotations

import hashlib
import math
import re
from collections import Counter


def embed_text(text: str, dimensions: int = 384) -> list[float]:
    vector = [0.0] * dimensions
    tokens = _tokens(text)
    counts = Counter(tokens)
    for token, count in counts.items():
        digest = hashlib.md5(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "little") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _tokens(text: str) -> list[str]:
    compact = re.sub(r"\s+", "", text.lower())
    tokens = re.findall(r"[a-z0-9_]+", text.lower())
    tokens.extend(compact[i : i + 2] for i in range(max(0, len(compact) - 1)))
    tokens.extend(compact[i : i + 3] for i in range(max(0, len(compact) - 2)))
    return [token for token in tokens if token.strip()]
