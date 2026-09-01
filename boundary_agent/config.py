from __future__ import annotations

from pathlib import Path

import yaml

from boundary_agent.paths import CONFIG_FILE


def load_config(path: str | Path | None = None) -> dict:
    config_path = Path(path) if path else CONFIG_FILE
    with config_path.open("r", encoding="utf-8-sig") as stream:
        return yaml.safe_load(stream)
