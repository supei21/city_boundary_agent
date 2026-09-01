from __future__ import annotations

from collections import Counter

from boundary_agent.models import BlockInput, ValidationResult


def validate_blocks(blocks: list[BlockInput]) -> ValidationResult:
    ids = [block.block_id for block in blocks]
    duplicates = [value for value, count in Counter(ids).items() if count > 1]
    blocking = [f"block_id重复：{', '.join(duplicates)}"] if duplicates else []
    usable = set()
    warnings = []
    missing = set()
    for block in blocks:
        for field in ("poi_density", "poi_industry", "poi_commercial", "poi_public", "poi_transport", "dem_relief", "road_density", "pop_density", "construction_ratio"):
            if getattr(block, field) is not None:
                usable.add(field)
        if block.population is None and block.pop_density is None:
            missing.add("人口指标")
        if block.dominant_landuse in (None, "", "待确认") and block.construction_ratio is None:
            missing.add("土地利用类型")
        if None in (block.ecological_redline, block.permanent_farmland):
            warnings.append(f"{block.block_id}法定约束信息不完整")
    if not usable:
        blocking.append("没有可用于适宜性评价的指标")
    return ValidationResult(
        valid=not blocking,
        missing_fields=sorted(missing),
        warnings=sorted(set(warnings)),
        usable_indicators=sorted(usable),
        blocking_issues=blocking,
    )
