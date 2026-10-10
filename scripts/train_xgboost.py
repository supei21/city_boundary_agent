from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from dbfread import DBF
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from xgboost import DMatrix, XGBClassifier


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from boundary_agent.tools.model_features import (  # noqa: E402
    build_model_feature_frame,
    make_feature_schema,
)


DEFAULT_DBF = Path(r"D:\downloadzhuomian\城镇开发边界\街区数据_shp\Blocks_Wuhan_Lastest.dbf")
RANDOM_STATE = 42


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the traceable parcel development XGBoost model.")
    parser.add_argument("--dbf", type=Path, default=DEFAULT_DBF)
    parser.add_argument("--label-threshold", type=float, default=0.01)
    parser.add_argument("--min-built-change-area-m2", type=float, default=1000.0)
    parser.add_argument("--test-size", type=float, default=0.20)
    parser.add_argument("--group-field", default="block_name")
    parser.add_argument("--n-jobs", type=int, default=1)
    return parser.parse_args()


def read_dbf(path: Path) -> pd.DataFrame:
    errors = []
    for encoding in ("utf-8", "gbk", "gb18030", "cp936", "latin1"):
        try:
            return pd.DataFrame(iter(DBF(str(path), load=True, encoding=encoding, char_decode_errors="ignore")))
        except Exception as exc:
            errors.append(f"{encoding}: {exc}")
    raise RuntimeError("无法读取DBF：" + " | ".join(errors))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def dominant_categories(series: pd.Series, minimum_count: int = 20) -> list[str]:
    cleaned = series.fillna("").astype(str).str.strip()
    counts = cleaned[cleaned.ne("")].value_counts()
    categories = [str(value) for value, count in counts.items() if int(count) >= minimum_count]
    return sorted(categories, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value))


def build_change_labels(
    source: pd.DataFrame,
    ratio_threshold: float,
    min_change_area_m2: float,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return valid rows, joint labels, and absolute built-area change in m²."""
    if ratio_threshold < 0 or min_change_area_m2 < 0:
        raise ValueError("标签阈值和最小变化面积不能为负数")
    required = {"Bulit_CHAG", "area_km2"}
    missing = required.difference(source.columns)
    if missing:
        raise ValueError(f"训练数据缺少标签计算字段: {', '.join(sorted(missing))}")
    ratio = pd.to_numeric(source["Bulit_CHAG"], errors="coerce")
    parcel_area_km2 = pd.to_numeric(source["area_km2"], errors="coerce")
    valid = ratio.notna() & np.isfinite(ratio) & parcel_area_km2.gt(0) & np.isfinite(parcel_area_km2)
    change_area_m2 = ratio * parcel_area_km2 * 1_000_000
    positive = ratio.gt(ratio_threshold) & change_area_m2.gt(min_change_area_m2)
    return valid, positive.astype(int), change_area_m2


def model_params(scale_pos_weight: float, n_jobs: int) -> dict[str, Any]:
    return {
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "n_estimators": 800,
        "learning_rate": 0.03,
        "max_depth": 3,
        "min_child_weight": 5,
        "gamma": 0.05,
        "subsample": 0.80,
        "colsample_bytree": 0.80,
        "reg_alpha": 0.10,
        "reg_lambda": 2.0,
        "scale_pos_weight": scale_pos_weight,
        "random_state": RANDOM_STATE,
        "n_jobs": n_jobs,
        "missing": np.nan,
    }


def metrics(y_true: pd.Series, probability: np.ndarray) -> dict[str, Any]:
    predicted = (probability >= 0.5).astype(int)
    return {
        "roc_auc": float(roc_auc_score(y_true, probability)),
        "pr_auc_average_precision": float(average_precision_score(y_true, probability)),
        "brier_score": float(brier_score_loss(y_true, probability)),
        "precision": float(precision_score(y_true, predicted, zero_division=0)),
        "recall": float(recall_score(y_true, predicted, zero_division=0)),
        "f1": float(f1_score(y_true, predicted, zero_division=0)),
        "confusion_matrix": confusion_matrix(y_true, predicted).astype(int).tolist(),
    }


def main() -> None:
    args = parse_args()
    source = read_dbf(args.dbf)
    valid, joint_label, change_area_m2 = build_change_labels(
        source, args.label_threshold, args.min_built_change_area_m2
    )

    schema = make_feature_schema(dominant_categories(source["dominant_l"]))
    records = source.to_dict(orient="records")
    initial_names = list(schema["feature_names"])
    x, missing = build_model_feature_frame(records, initial_names, schema)

    # A feature must contain finite variation to be part of the persisted model.
    constants = [
        column for column in x.columns
        if x[column].dropna().nunique() <= 1
    ]
    feature_names = [column for column in x.columns if column not in constants]
    schema["feature_names"] = feature_names
    schema["constant_features_removed"] = constants
    schema["missing_features_during_training"] = missing
    x = x[feature_names]

    x = x.loc[valid].reset_index(drop=True)
    y = joint_label.loc[valid].reset_index(drop=True)
    source_valid = source.loc[valid].reset_index(drop=True)
    ratio_valid = pd.to_numeric(source_valid["Bulit_CHAG"], errors="coerce")
    area_valid = change_area_m2.loc[valid].reset_index(drop=True)
    counts = y.value_counts()
    negative = int(counts.get(0, 0))
    positive = int(counts.get(1, 0))
    if not positive or not negative:
        raise ValueError("标签必须同时包含正样本和负样本")
    weight = negative / positive

    train_index, test_index = train_test_split(
        np.arange(len(y)),
        test_size=args.test_size,
        random_state=RANDOM_STATE,
        stratify=y,
    )
    params = model_params(weight, args.n_jobs)
    validation_model = XGBClassifier(**params, early_stopping_rounds=50)
    validation_model.fit(
        x.iloc[train_index],
        y.iloc[train_index],
        eval_set=[(x.iloc[test_index], y.iloc[test_index])],
        verbose=False,
    )
    best_trees = int(validation_model.best_iteration or 0) + 1
    random_metrics = metrics(y.iloc[test_index], validation_model.predict_proba(x.iloc[test_index])[:, 1])
    random_metrics["train_roc_auc"] = float(
        roc_auc_score(y.iloc[train_index], validation_model.predict_proba(x.iloc[train_index])[:, 1])
    )
    random_metrics["train_size"] = int(len(train_index))
    random_metrics["test_size"] = int(len(test_index))

    group_metrics: dict[str, Any]
    if args.group_field in source_valid.columns and source_valid[args.group_field].nunique() > 1:
        groups = source_valid[args.group_field].fillna("UNKNOWN").astype(str)
        splitter = GroupShuffleSplit(n_splits=1, test_size=args.test_size, random_state=RANDOM_STATE)
        group_train, group_test = next(splitter.split(x, y, groups))
        if y.iloc[group_train].nunique() == 2 and y.iloc[group_test].nunique() == 2:
            group_model = XGBClassifier(**{**params, "n_estimators": best_trees})
            group_model.fit(x.iloc[group_train], y.iloc[group_train], verbose=False)
            group_metrics = metrics(y.iloc[group_test], group_model.predict_proba(x.iloc[group_test])[:, 1])
            group_metrics.update({
                "available": True,
                "group_field": args.group_field,
                "train_groups": int(groups.iloc[group_train].nunique()),
                "test_groups": int(groups.iloc[group_test].nunique()),
                "train_size": int(len(group_train)),
                "test_size": int(len(group_test)),
            })
        else:
            group_metrics = {"available": False, "warning": "空间分组后测试集出现单一类别"}
    else:
        group_metrics = {"available": False, "warning": f"缺少有效分组字段 {args.group_field}"}

    final_model = XGBClassifier(**{**params, "n_estimators": best_trees})
    final_model.fit(x, y, verbose=False)

    models_dir = PROJECT_ROOT / "models"
    outputs_dir = PROJECT_ROOT / "outputs"
    models_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    final_model.save_model(models_dir / "xgb_boundary.json")
    write_json(models_dir / "feature_names.json", feature_names)
    write_json(models_dir / "feature_schema.json", schema)

    report = {
        # Keep the established keys consumed by the current diagnostics UI.
        "train_roc_auc": random_metrics["train_roc_auc"],
        "test_roc_auc": random_metrics["roc_auc"],
        "test_pr_auc_average_precision": random_metrics["pr_auc_average_precision"],
        "test_brier_score": random_metrics["brier_score"],
        "test_precision": random_metrics["precision"],
        "test_recall": random_metrics["recall"],
        "test_f1": random_metrics["f1"],
        "test_confusion_matrix": random_metrics["confusion_matrix"],
        "random_holdout": random_metrics,
        "group_holdout": group_metrics,
        "feature_count": len(feature_names),
        "label_counts": {"0": negative, "1": positive},
        "label_valid_rows": int(valid.sum()),
        "label_excluded_invalid_rows": int((~valid).sum()),
        "ratio_only_positive_count": int(ratio_valid.gt(args.label_threshold).sum()),
        "ratio_positive_below_area_min_count": int((ratio_valid.gt(args.label_threshold) & area_valid.le(args.min_built_change_area_m2)).sum()),
        "large_absolute_change_below_ratio_count": int((ratio_valid.le(args.label_threshold) & area_valid.gt(args.min_built_change_area_m2)).sum()),
        "min_built_change_area_m2": args.min_built_change_area_m2,
        "positive_ratio": float(positive / len(y)),
        "scale_pos_weight": float(weight),
        "best_n_estimators": best_trees,
        "label_warning": "当前仍为建成区变化代理标签；获得真实调入调出图斑后应重新训练决策校准模型。",
    }
    write_json(models_dir / "metrics.json", report)
    write_json(models_dir / "training_config.json", {
        "dbf_path": str(args.dbf),
        "label_field": "label_inclusion",
        "label_source": "Bulit_CHAG",
        "label_threshold": args.label_threshold,
        "min_built_change_area_m2": args.min_built_change_area_m2,
        "change_area_formula": "Bulit_CHAG * area_km2 * 1000000",
        "positive_rule": f"Bulit_CHAG > {args.label_threshold} and built_change_area_m2 > {args.min_built_change_area_m2}",
        "feature_schema_version": schema["schema_version"],
        "used_features": feature_names,
        "excluded_features": schema["directly_excluded"],
        "unavailable_requested_features": schema["requested_but_unavailable"],
        "xgboost_params": {**params, "n_estimators": best_trees, "missing": "NaN"},
    })

    matrix = DMatrix(x, feature_names=feature_names)
    contributions = final_model.get_booster().predict(matrix, pred_contribs=True)
    importance = pd.DataFrame({
        "feature": feature_names,
        "mean_abs_shap": np.abs(contributions[:, :-1]).mean(axis=0),
    }).sort_values("mean_abs_shap", ascending=False)
    importance["feature_label"] = importance["feature"].map(schema["feature_labels"]).fillna(importance["feature"])
    importance.to_csv(outputs_dir / "shap_importance.csv", index=False, encoding="utf-8-sig")

    print(json.dumps({
        "model": str(models_dir / "xgb_boundary.json"),
        "feature_count": len(feature_names),
        "best_n_estimators": best_trees,
        "random_holdout": random_metrics,
        "group_holdout": group_metrics,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
