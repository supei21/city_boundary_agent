from __future__ import annotations

from typing import Any


def _first(parcel: dict[str, Any], *keys: str) -> Any:
    lowered = {str(key).lower(): key for key in parcel}
    for alias in keys:
        key = alias if alias in parcel else lowered.get(alias.lower())
        if key is not None and parcel.get(key) not in (None, ""):
            return parcel[key]
    return None


def _as_bool(value: Any) -> bool | None:
    if value is True or value is False or value is None:
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "是", "有", "涉及", "重叠"}:
        return True
    if text in {"false", "0", "no", "n", "否", "无", "不涉及", "不重叠"}:
        return False
    return None


def _is_development_action(parcel: dict[str, Any]) -> bool:
    action = str(_first(parcel, "proposed_action", "adjustment_action", "candidate_decision") or "").strip().lower()
    if any(word in action for word in ("调出", "退出", "移出", "exclusion", "remove")):
        return False
    return any(word in action for word in ("调入", "纳入", "inclusion", "include", "建设", "开发")) or not action


def evaluate_hard_constraints(parcel: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a small deterministic set without an external rule library."""

    development_action = _is_development_action(parcel)
    checks: list[dict[str, Any]] = []
    missing: list[str] = []

    def add_check(constraint_id: str, name: str, triggered: bool, field: str, value: Any, reason: str) -> None:
        checks.append({
            "constraint_id": constraint_id,
            "name": name,
            "triggered": bool(triggered),
            "field": field,
            "value": value,
            "reason": reason if triggered else "未触发",
        })

    ecological = _as_bool(_first(parcel, "ecological_redline", "eco_redline", "生态保护红线"))
    farmland = _as_bool(_first(parcel, "permanent_farmland", "basic_farmland", "永久基本农田"))
    disaster = _as_bool(_first(parcel, "prohibited_disaster_zone", "disaster_prohibited", "地质灾害禁建区"))
    disaster_level = str(_first(parcel, "disaster_risk", "disaster_risk_level") or "").strip().lower()
    military = _as_bool(_first(parcel, "military_restricted_zone", "military_zone", "军事禁区"))
    water_level = str(_first(parcel, "drinking_water_source_level", "water_source_level", "饮用水水源保护级别") or "").strip().lower()
    planning = _as_bool(_first(parcel, "planning_compliance", "land_use_plan_compliance", "规划符合性"))

    if ecological is None:
        missing.append("是否涉及生态保护红线")
    if farmland is None:
        missing.append("是否涉及永久基本农田")
    if disaster is None and not disaster_level:
        missing.append("尚未提供地质灾害约束数据")
    if planning is None:
        missing.append("是否符合已批准国土空间规划")

    add_check("HC_ECO_REDLINE", "生态保护红线", development_action and ecological is True,
              "ecological_redline", ecological,
              "拟调入或开发建设地块涉及生态保护红线，默认不得进入一般开发建设评价。")
    add_check("HC_PERMANENT_FARMLAND", "永久基本农田", development_action and farmland is True,
              "permanent_farmland", farmland,
              "拟调入或开发建设地块涉及永久基本农田，普通建设项目默认不可行。")
    add_check("HC_DISASTER_PROHIBITED", "法定地质灾害禁建区",
              development_action and (disaster is True or disaster_level in {"prohibited", "禁止建设", "禁建"}),
              "prohibited_disaster_zone/disaster_risk", disaster if disaster is not None else disaster_level,
              "地块位于明确的地质灾害禁建区，拟开发建设不可行。")
    add_check("HC_WATER_SOURCE_LEVEL1", "饮用水水源一级保护区",
              development_action and water_level in {"1", "一级", "level1", "level 1", "一级保护区"},
              "drinking_water_source_level", water_level or None,
              "地块位于饮用水水源一级保护区，非保护目的建设活动默认不可行。")
    add_check("HC_MILITARY_RESTRICTED", "军事禁区", development_action and military is True,
              "military_restricted_zone", military, "地块涉及军事禁区，非军事建设活动不可行。")
    add_check("HC_PLANNING_NONCOMPLIANT", "法定规划不符合", development_action and planning is False,
              "planning_compliance/land_use_plan_compliance", planning,
              "地块明确不符合已批准国土空间规划，不能直接实施调入或开发建设。")

    triggered = [item for item in checks if item["triggered"]]
    return {
        "engine": "built_in_minimal_constraints",
        "version": "1.0",
        "development_action": development_action,
        "hard_constraint": bool(triggered),
        "triggered_constraints": triggered,
        "checks": checks,
        "missing_information": list(dict.fromkeys(missing)),
    }
