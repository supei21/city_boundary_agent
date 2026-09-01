from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from boundary_agent.io import parse_json
from boundary_agent.paths import RESULTS_DIR, SAMPLE_DATA_FILE
from boundary_agent.workflow.graph import run_workflow


blocks = parse_json(SAMPLE_DATA_FILE.read_bytes())
result = run_workflow(blocks)
output = RESULTS_DIR / "decisions.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
print(output)
