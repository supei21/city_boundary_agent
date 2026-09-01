from __future__ import annotations

from boundary_agent.models import BlockInput, ConstraintResult
from boundary_agent.workflow.rules import check_constraints


def evaluate_constraints(blocks: list[BlockInput], config: dict) -> list[ConstraintResult]:
    return [check_constraints(block, config) for block in blocks]
