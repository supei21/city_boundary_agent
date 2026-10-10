from __future__ import annotations

from boundary_agent.models import BlockInput, ConstraintResult
from boundary_agent.rag.policy_client import assess_blocks_with_rag
from boundary_agent.workflow.rules import check_constraints


def evaluate_constraints(blocks: list[BlockInput], config: dict) -> list[ConstraintResult]:
    policy_by_block = assess_blocks_with_rag(blocks, config)
    return [
        check_constraints(block, config, policy_by_block.get(block.block_id))
        for block in blocks
    ]
