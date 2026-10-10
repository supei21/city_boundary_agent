from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from boundary_agent.gis import SHP_FIELD_ALIASES
from boundary_agent.paths import PROJECT_ROOT
from boundary_agent.tools.model_features import (
    build_model_feature_frame,
    load_feature_schema,
)


DEFAULT_MODEL_DIR = PROJECT_ROOT / "models"


class TrainedBoundaryModelError(RuntimeError):
    pass


@dataclass(slots=True)
class PredictionBundle:
    blocks: list[dict[str, Any]]
    records: list[dict[str, Any]]
    model_dir: str
    feature_count: int
    missing_features: list[str]
    source: str
    warning: str | None = None


class XGBoostJsonPredictor:
    """Minimal XGBoost binary:logistic predictor for saved JSON models."""

    def __init__(self, model_path: Path, feature_names: list[str]) -> None:
        with Path(model_path).open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        learner = payload["learner"]
        self.feature_names = feature_names
        self.trees = learner["gradient_booster"]["model"]["trees"]
        base_score = self._parse_base_score(
            learner["learner_model_param"].get("base_score", "0.5")
        )
        self.base_margin = self._logit(base_score)

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        x = frame[self.feature_names].apply(pd.to_numeric, errors="coerce")
        x = x.replace([np.inf, -np.inf], np.nan)
        # XGBoost's DMatrix evaluates numeric features as float32.  Matching that
        # precision is important for values that sit exactly on a learned split.
        x = x.astype(np.float32)
        return np.asarray([self._predict_row(row) for _, row in x.iterrows()], dtype=float)

    def _predict_row(self, row: pd.Series) -> float:
        margin = self.base_margin
        for tree in self.trees:
            node = 0
            left_children = tree["left_children"]
            right_children = tree["right_children"]
            default_left = tree["default_left"]
            split_indices = tree["split_indices"]
            split_conditions = tree["split_conditions"]
            base_weights = tree["base_weights"]
            while left_children[node] != -1:
                feature_index = int(split_indices[node])
                value = row.iloc[feature_index]
                threshold = np.float32(split_conditions[node])
                if pd.isna(value):
                    node = left_children[node] if default_left[node] else right_children[node]
                elif np.float32(value) < threshold:
                    node = left_children[node]
                else:
                    node = right_children[node]
            margin += float(base_weights[node])
        return self._sigmoid(margin)

    @staticmethod
    def _parse_base_score(value: Any) -> float:
        text = str(value).strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        return float(text)

    @staticmethod
    def _logit(value: float) -> float:
        clipped = min(max(value, 1e-12), 1 - 1e-12)
        return math.log(clipped / (1 - clipped))

    @staticmethod
    def _sigmoid(value: float) -> float:
        if value >= 0:
            z = math.exp(-value)
            return 1 / (1 + z)
        z = math.exp(value)
        return z / (1 + z)


def resolve_model_dir(config: dict[str, Any]) -> Path:
    configured = (
        os.getenv("BOUNDARY_TRAINED_MODEL_DIR")
        or config.get("trained_model", {}).get("model_dir")
        or str(DEFAULT_MODEL_DIR)
    )
    model_dir = Path(configured).expanduser()
    if not model_dir.is_absolute():
        model_dir = PROJECT_ROOT / model_dir
    return model_dir.resolve()


def trained_model_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("trained_model", {}).get("enabled", True))


def load_feature_names(model_dir: Path) -> list[str]:
    path = model_dir / "feature_names.json"
    if not path.exists():
        raise TrainedBoundaryModelError(f"未找到模型特征文件：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_training_metrics(model_dir: Path) -> dict[str, Any]:
    path = model_dir / "metrics.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_predictor(model_dir: Path) -> tuple[XGBoostJsonPredictor, list[str]]:
    model_path = model_dir / "xgb_boundary.json"
    if not model_path.exists():
        raise TrainedBoundaryModelError(f"未找到XGBoost JSON模型：{model_path}")
    feature_names = load_feature_names(model_dir)
    return XGBoostJsonPredictor(model_path, feature_names), feature_names


def enrich_blocks_with_model_predictions(
    blocks: list[dict[str, Any]],
    config: dict[str, Any],
) -> PredictionBundle:
    potential_bundle = _bundle_from_development_potential(blocks)
    if potential_bundle is not None:
        return potential_bundle

    if not trained_model_enabled(config):
        return PredictionBundle(
            blocks=[dict(item) for item in blocks],
            records=[],
            model_dir="",
            feature_count=0,
            missing_features=[],
            source="proxy",
            warning="训练模型工具未启用，沿用工作流内置代理概率。",
        )

    model_dir = resolve_model_dir(config)
    predictor, feature_names = load_predictor(model_dir)
    feature_schema = load_feature_schema(model_dir)
    frame, missing_features = build_model_feature_frame(
        blocks,
        feature_names,
        feature_schema,
    )
    available_features = [feature for feature in feature_names if frame[feature].notna().any()]
    min_coverage = float(config.get("trained_model", {}).get("min_feature_coverage", 0.5))
    coverage = len(available_features) / max(len(feature_names), 1)
    if coverage < min_coverage:
        raise TrainedBoundaryModelError(
            f"模型特征覆盖率不足（{coverage:.0%}，要求不低于{min_coverage:.0%}）"
        )
    probabilities = predictor.predict_proba(frame)

    enriched: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for block, probability in zip(blocks, probabilities, strict=True):
        score = round(float(probability) * 100, 2)
        updated = dict(block)
        updated["development_probability"] = score
        updated["model_probability"] = round(float(probability), 6)
        updated["model_source"] = "xgb_boundary_json"
        enriched.append(updated)
        records.append(
            {
                "block_id": str(updated.get("block_id")),
                "block_name": updated.get("block_name"),
                "development_probability": score,
                "model_probability": round(float(probability), 6),
            }
        )

    records.sort(key=lambda item: (-float(item["development_probability"]), str(item["block_id"])))
    return PredictionBundle(
        blocks=enriched,
        records=records,
        model_dir=str(model_dir),
        feature_count=len(feature_names),
        missing_features=missing_features,
        source="xgb_boundary_json",
    )


def _bundle_from_development_potential(
    blocks: list[dict[str, Any]],
) -> PredictionBundle | None:
    if not blocks:
        return None
    has_field = [
        block.get("development_potential") is not None or block.get("dev_score") is not None
        for block in blocks
    ]
    if not any(has_field):
        return None
    if not all(has_field):
        raise TrainedBoundaryModelError("综合发展潜力字段不完整，部分地块缺少 dev_score")

    enriched: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for block in blocks:
        raw_value = block.get("development_potential", block.get("dev_score"))
        try:
            potential = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise TrainedBoundaryModelError("dev_score 必须是 0 到 1 之间的数值") from exc
        if not math.isfinite(potential) or not 0 <= potential <= 1:
            raise TrainedBoundaryModelError(f"dev_score 超出 0 到 1 范围：{raw_value}")

        probability = round(potential * 100, 2)
        updated = dict(block)
        updated["development_potential"] = potential
        updated["development_probability"] = probability
        updated["model_probability"] = round(potential, 6)
        updated["model_source"] = "uploaded_development_potential"
        enriched.append(updated)
        records.append(
            {
                "block_id": str(updated.get("block_id")),
                "block_name": updated.get("block_name"),
                "development_probability": probability,
                "model_probability": round(potential, 6),
            }
        )

    records.sort(key=lambda item: (-float(item["development_probability"]), str(item["block_id"])))
    return PredictionBundle(
        blocks=enriched,
        records=records,
        model_dir="input:dev_score",
        feature_count=1,
        missing_features=[],
        source="uploaded_development_potential",
    )


def model_status(config: dict[str, Any]) -> dict[str, Any]:
    if not trained_model_enabled(config):
        return {"enabled": False, "available": False, "warning": "训练模型工具未启用"}
    model_dir = resolve_model_dir(config)
    try:
        feature_names = load_feature_names(model_dir)
        metrics = load_training_metrics(model_dir)
        feature_schema = load_feature_schema(model_dir)
    except Exception as exc:
        return {
            "enabled": True,
            "available": False,
            "model_dir": str(model_dir),
            "warning": str(exc),
        }
    return {
        "enabled": True,
        "available": (model_dir / "xgb_boundary.json").exists(),
        "model_dir": str(model_dir),
        "feature_count": len(feature_names),
        "feature_schema_version": (feature_schema or {}).get("schema_version", 1),
        "metrics": metrics,
    }


def _build_feature_frame(
    blocks: list[dict[str, Any]],
    feature_names: list[str],
) -> tuple[pd.DataFrame, list[str]]:
    rows = []
    missing_features: set[str] = set()
    for block in blocks:
        row = {}
        lower_map = {str(key).lower(): key for key in block}
        for feature in feature_names:
            value = _lookup_feature(block, lower_map, feature)
            if value is None:
                missing_features.add(feature)
            row[feature] = value
        rows.append(row)
    return pd.DataFrame(rows, columns=feature_names), sorted(missing_features)


def _lookup_feature(block: dict[str, Any], lower_map: dict[str, str], feature: str) -> Any:
    if feature in block:
        return block[feature]
    lowered_key = lower_map.get(feature.lower())
    if lowered_key is not None:
        return block[lowered_key]
    canonical = SHP_FIELD_ALIASES.get(feature)
    if canonical and canonical in block:
        return block[canonical]
    canonical_key = lower_map.get(str(canonical).lower()) if canonical else None
    if canonical_key:
        return block[canonical_key]
    return None
