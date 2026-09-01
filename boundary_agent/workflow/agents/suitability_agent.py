from __future__ import annotations

from boundary_agent.models import BlockInput, SuitabilityResult
from boundary_agent.workflow.rules import calculate_suitability


def evaluate_blocks(blocks: list[BlockInput], config: dict) -> list[SuitabilityResult]:
    return [calculate_suitability(block, config) for block in blocks]
