from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from boundary_agent.paths import RESULTS_DIR


def save_result(result: dict, database: str | Path | None = None) -> None:
    db = Path(database) if database else RESULTS_DIR / "decisions.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP, payload TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO tasks(task_id, payload) VALUES (?, ?)", (result["task_id"], json.dumps(result, ensure_ascii=False)))
