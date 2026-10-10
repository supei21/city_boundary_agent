from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class PolicyRuleEngine:
    def __init__(self, rules_path: Path):
        self.rules_path = rules_path
        self.rules = self._load_rules()

    def analyze(self, parcel: dict[str, Any]) -> dict[str, Any]:
        matched = self.match_rules(parcel)
        if not matched:
            return {
                "matched_rules": [],
                "total_score": 0,
                "suggested_action": "待定",
                "reason": "未匹配到政策规则，建议结合检索依据人工复核。",
            }
        total_score = sum(float(rule.get("score_adjustment", 0)) for rule in matched[:3])
        forbidden = [
            rule for rule in matched
            if float(rule.get("score_adjustment", 0)) <= -0.8
            or any(keyword in str(rule.get("action", "")) for keyword in ("禁止", "不得", "严禁"))
        ]
        if forbidden:
            suggested_action = forbidden[0].get("action") or "禁止"
        elif total_score > 0:
            suggested_action = matched[0].get("action") or "可考虑"
        elif total_score < 0:
            suggested_action = matched[0].get("action") or "限制"
        else:
            suggested_action = "待定"
        return {
            "matched_rules": matched[:8],
            "total_score": round(total_score, 3),
            "suggested_action": suggested_action,
            "reason": "；".join(
                f"规则{rule.get('rule_id')}：{rule.get('action')}"
                for rule in matched[:5]
            ),
        }

    def match_rules(self, parcel: dict[str, Any]) -> list[dict[str, Any]]:
        matched = []
        for rule in self.rules:
            if not self._action_scope_matches(rule, parcel):
                continue
            if all(self._eval_condition(expected, parcel.get(key)) for key, expected in rule.get("trigger_conditions", {}).items()):
                matched.append(self._summarize_rule(rule))
        return sorted(matched, key=self._priority, reverse=True)

    @staticmethod
    def _action_scope_matches(rule: dict[str, Any], parcel: dict[str, Any]) -> bool:
        """Prevent inclusion-only rules from being applied to exclusions, and vice versa."""
        proposed_action = str(parcel.get("proposed_action", ""))
        if proposed_action not in {"调入", "调出"}:
            return True
        scope_text = " ".join(
            str(rule.get(key, ""))
            for key in ("constraint_expression", "action")
        )
        opposite = "调出" if proposed_action == "调入" else "调入"
        return not (opposite in scope_text and proposed_action not in scope_text)

    def _load_rules(self) -> list[dict[str, Any]]:
        if not self.rules_path.exists():
            return []
        payload = json.loads(self.rules_path.read_text(encoding="utf-8"))
        return payload.get("rules", [])

    def _eval_condition(self, expected: Any, actual: Any) -> bool:
        if actual is None:
            return expected is None
        if isinstance(expected, dict):
            op = expected.get("op", "==")
            value = expected.get("value")
            if op == "range" and isinstance(value, list) and len(value) == 2:
                return self._compare(actual, ">=", value[0]) and self._compare(actual, "<=", value[1])
            if op in {">=", ">", "<=", "<", "=="}:
                return self._compare(actual, op, value)
            if op == "in":
                return self._value_in_list(actual, value if isinstance(value, list) else [value])
            if op == "not_in":
                return not self._value_in_list(actual, value if isinstance(value, list) else [value])
            if op == "contains":
                return str(value) in str(actual)
            if op == "regex":
                return re.search(str(value), str(actual)) is not None
            return False
        if isinstance(expected, list):
            return self._value_in_list(actual, expected)
        if isinstance(expected, bool):
            return bool(actual) == expected
        if isinstance(expected, (int, float)):
            return self._compare(actual, "==", expected)
        return self._text_match(actual, expected)

    def _value_in_list(self, actual: Any, expected_values: list[Any]) -> bool:
        actual_values = actual if isinstance(actual, list) else [actual]
        return any(self._text_match(left, right) for left in actual_values for right in expected_values)

    def _text_match(self, actual: Any, expected: Any) -> bool:
        actual_text = str(actual).strip()
        expected_text = str(expected).strip()
        return actual_text == expected_text or actual_text in expected_text or expected_text in actual_text

    def _compare(self, actual: Any, op: str, expected: Any) -> bool:
        try:
            left = float(actual)
            right = float(expected)
        except (TypeError, ValueError):
            if op == "==":
                return self._text_match(actual, expected)
            return False
        if op == ">=":
            return left >= right
        if op == ">":
            return left > right
        if op == "<=":
            return left <= right
        if op == "<":
            return left < right
        if op == "==":
            return left == right
        return False

    def _summarize_rule(self, rule: dict[str, Any]) -> dict[str, Any]:
        return {
            "rule_id": rule.get("rule_id"),
            "policy_name": rule.get("policy_name"),
            "action": rule.get("action"),
            "score_adjustment": rule.get("score_adjustment", 0),
            "source_text": rule.get("source_text", ""),
            "article_ref": rule.get("article_ref", ""),
            "category": rule.get("category", ""),
            "clause_type": rule.get("clause_type", ""),
        }

    @staticmethod
    def _priority(rule: dict[str, Any]) -> float:
        score = float(rule.get("score_adjustment", 0))
        action = str(rule.get("action", ""))
        base = 10000 if score <= -0.8 or any(keyword in action for keyword in ("禁止", "不得", "严禁")) else 0
        return base + abs(score) * 100
