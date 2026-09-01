from __future__ import annotations

import csv
import io
import json
from typing import Any


NULLS = {"", "null", "none", "nan", "待确认", "未知"}
BOOL_TRUE = {"true", "1", "yes", "是"}
BOOL_FALSE = {"false", "0", "no", "否"}


def coerce(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.lower() in NULLS or text in NULLS:
        return None
    if text.lower() in BOOL_TRUE or text in BOOL_TRUE:
        return True
    if text.lower() in BOOL_FALSE or text in BOOL_FALSE:
        return False
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return text


def parse_json(data: bytes | str) -> list[dict]:
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    obj = json.loads(text)
    blocks = obj.get("blocks", obj) if isinstance(obj, dict) else obj
    if not isinstance(blocks, list):
        raise ValueError("JSON必须是街区数组，或包含blocks数组")
    return blocks


def parse_csv(data: bytes | str) -> list[dict]:
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    return [{key: coerce(value) for key, value in row.items()} for row in csv.DictReader(io.StringIO(text))]
