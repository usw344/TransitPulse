"""Evaluate reproducible causal baselines on one chronological M3 dataset."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from statistics import median

from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.preprocessing import OrdinalEncoder


CATEGORICAL = ("route_gtfs_id", "from_stop_id", "to_stop_id")
NUMERIC = ("scheduled_seconds", "distance_m", "hour", "weekday", "direction_id")
MODEL_NAMES = ("scheduled", "segment_median", "segment_hour_median", "recent_rolling_median", "hist_gradient_boosting")


def read_jsonl(path: Path) -> tuple[list[dict[str, object]], bytes]:
    data = path.read_bytes()
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line], data


def metrics(actual: list[float], predicted: list[float]) -> dict[str, float | int]:
    errors = sorted(abs(a - p) for a, p in zip(actual, predicted, strict=True))
    return {"n": len(errors), "mae": sum(errors) / len(errors), "median_ae": median(errors), "p90_ae": errors[round((len(errors) - 1) * 0.9)], "rmse": (sum((a - p) ** 2 for a, p in zip(actual, predicted, strict=True)) / len(errors)) ** 0.5}


def segment_key(row: dict[str, object]) -> tuple[str, str, str]:
    return (str(row["route_gtfs_id"]), str(row["from_stop_id"]), str(row["to_stop_id"]))


def row_id(row: dict[str, object]) -> str:
    return sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def as_features(rows: list[dict[str, object]], encoder: OrdinalEncoder) -> list[list[float]]:
    categories = encoder.transform([[row[field] for field in CATEGORICAL] for row in rows])
    return [[*encoded, *[float(row[field]) if row[field] is not None else -1.0 for field in NUMERIC]] for encoded, row in zip(categories, rows, strict=True)]


def time_group(row: dict[str, object]) -> str:
    hour = int(row["hour"])
    return "am_peak_07_10" if 7 <= hour < 10 else "pm_peak_15_19" if 15 <= hour < 19 else "other"


def support_group(row: dict[str, object], supports: dict[tuple[str, str, str], int]) -> str:
    count = supports.get(segment_key(row), 0)
    return "unseen_in_train" if count == 0 else "sparse_1_4_train_rows" if count < 5 else "supported_5_plus_train_rows"


def grouped_metrics(rows: list[dict[str, object]], predictions: dict[str, list[float]], supports: dict[tuple[str, str, str], int]) -> dict[str, dict[str, dict[str, dict[str, float | int]]]]:
    dimensions = {"route": lambda row: str(row["route_gtfs_id"]), "time_of_day": time_group, "segment_support": lambda row: support_group(row, supports)}
    result: dict[str, dict[str, dict[str, dict[str, float | int]]]] = {}
    for dimension, classifier in dimensions.items():
        groups: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(rows):
            groups[classifier(row)].append(index)
        result[dimension] = {group: {model: metrics([float(rows[index]["target_travel_seconds"]) for index in indexes], [values[index] for index in indexes]) for model, values in predictions.items()} for group, indexes in sorted(groups.items())}
    return result


def recent_rolling_predictions(*, rows: list[dict[str, object]], initial_history: list[dict[str, object]], fallback_by_segment: dict[tuple[str, str, str], float], global_fallback: float) -> list[float]:
    """Use only labels whose outcomes are known at each prediction timestamp."""
    completed_by_segment: dict[tuple[str, str, str], list[tuple[datetime, float]]] = defaultdict(list)
    for row in initial_history:
        completed_by_segment[segment_key(row)].append((datetime.fromisoformat(str(row["outcome_available_at"])), float(row["target_travel_seconds"])))
    for values in completed_by_segment.values():
        values.sort()
    all_candidates = sorted(rows, key=lambda row: datetime.fromisoformat(str(row["outcome_available_at"])))
    candidate_index = 0
    output = [0.0] * len(rows)
    for original_index, row in sorted(enumerate(rows), key=lambda item: datetime.fromisoformat(str(item[1]["prediction_at"]))):
        prediction_at = datetime.fromisoformat(str(row["prediction_at"]))
        while candidate_index < len(all_candidates) and datetime.fromisoformat(str(all_candidates[candidate_index]["outcome_available_at"])) <= prediction_at:
            completed = all_candidates[candidate_index]
            completed_by_segment[segment_key(completed)].append((datetime.fromisoformat(str(completed["outcome_available_at"])), float(completed["target_travel_seconds"])))
            candidate_index += 1
        values = completed_by_segment[segment_key(row)]
        output[original_index] = median(value for _, value in values[-5:]) if values else fallback_by_segment.get(segment_key(row), global_fallback)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): parser.error(f"output exists: {args.output}")
    rows, rows_bytes = read_jsonl(args.rows)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    rows_sha256 = sha256(rows_bytes).hexdigest()
    if manifest.get("rows_sha256") != rows_sha256: parser.error("M3 manifest rows_sha256 does not match the exact input bytes")
    train, validation, test = ([row for row in rows if row["split"] == split] for split in ("train", "validation", "test"))
    if not train or not validation or not test: parser.error("requires chronological train, validation, and test rows")
    supports: dict[tuple[str, str, str], int] = defaultdict(int)
    values_by_segment: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    values_by_hour: dict[tuple[str, str, str, int], list[float]] = defaultdict(list)
    for row in train:
        key, value = segment_key(row), float(row["target_travel_seconds"])
        supports[key] += 1; values_by_segment[key].append(value); values_by_hour[(*key, int(row["hour"]))].append(value)
    segment = {key: median(values) for key, values in values_by_segment.items()}
    segment_hour = {key: median(values) for key, values in values_by_hour.items()}
    global_median = median(float(row["target_travel_seconds"]) for row in train)
    encoder = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
    encoder.fit([[row[field] for field in CATEGORICAL] for row in train])
    model_config = {"max_iter": 200, "learning_rate": 0.08, "max_leaf_nodes": 15, "l2_regularization": 1.0, "random_state": 7}
    model = HistGradientBoostingRegressor(**model_config).fit(as_features(train, encoder), [float(row["target_travel_seconds"]) for row in train])
    results: dict[str, dict[str, dict[str, float | int]]] = {}
    subgroup_results: dict[str, dict[str, dict[str, dict[str, dict[str, float | int]]]]] = {}
    prediction_records: list[dict[str, object]] = []
    previous_outcomes = train.copy()
    for split, sample in (("validation", validation), ("test", test)):
        current_predictions = {
            "scheduled": [float(row["scheduled_seconds"]) for row in sample],
            "segment_median": [segment.get(segment_key(row), global_median) for row in sample],
            "segment_hour_median": [segment_hour.get((*segment_key(row), int(row["hour"])), segment.get(segment_key(row), global_median)) for row in sample],
            "recent_rolling_median": recent_rolling_predictions(rows=sample, initial_history=previous_outcomes, fallback_by_segment=segment, global_fallback=global_median),
            "hist_gradient_boosting": model.predict(as_features(sample, encoder)).tolist(),
        }
        actual = [float(row["target_travel_seconds"]) for row in sample]
        results[split] = {name: metrics(actual, current_predictions[name]) for name in MODEL_NAMES}
        subgroup_results[split] = grouped_metrics(sample, current_predictions, supports)
        prediction_records.extend({"row_id": row_id(row), "split": split, "prediction_at": row["prediction_at"], "target_travel_seconds": row["target_travel_seconds"], "predictions": {name: current_predictions[name][index] for name in MODEL_NAMES}} for index, row in enumerate(sample))
        previous_outcomes.extend(sample)
    prediction_bytes = "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in prediction_records).encode("utf-8")
    report = {
        "schema_version": 2, "experiment_contract": "m4-causal-chronological-baselines-v2", "source_rows": str(args.rows), "source_rows_sha256": rows_sha256, "source_manifest": str(args.manifest), "source_labels_sha256": manifest.get("source_labels_sha256"), "split_counts": {"train": len(train), "validation": len(validation), "test": len(test)},
        "prediction_time_policy": "The rolling baseline adds only outcomes with outcome_available_at <= each row's prediction_at. Other models train only on the train split.",
        "models": {"scheduled": "Static scheduled seconds.", "segment_median": "Train-only directed segment median.", "segment_hour_median": "Train-only directed segment and clock-hour median.", "recent_rolling_median": "Last five completed causal outcomes for a segment; train segment/global fallbacks.", "hist_gradient_boosting": {"categorical": CATEGORICAL, "numeric": NUMERIC, "config": model_config}},
        "results": results, "subgroup_results": subgroup_results,
        "prediction_records": {"path": "predictions.jsonl", "row_count": len(prediction_records), "sha256": sha256(prediction_bytes).hexdigest()},
        "model_selection": {"selected_on": "validation", "selected_model": min(MODEL_NAMES, key=lambda name: results["validation"][name]["mae"]), "validation_mae_seconds": {name: results["validation"][name]["mae"] for name in MODEL_NAMES}, "test_metrics_are_reported_after_selection_and_must_not_drive_model_choice": True},
        "subgroup_definitions": {"time_of_day": "AM peak 07:00-09:59, PM peak 15:00-18:59, otherwise other", "segment_support": "Train directed-segment sample count: unseen, sparse 1-4, supported 5+"},
        "reproducible_command": f".\\.venv\\Scripts\\python.exe scripts\\evaluate_baselines.py --rows {args.rows} --manifest {args.manifest} --output {args.output}",
        "limitations": ["Four sparse, discontinuous service days only.", "Rolling labels are historical outcome values and are used only when their outcome timestamp has passed.", "This is an offline backtest, not a live forecast or passenger-impact estimate."],
    }
    args.output.mkdir(parents=True)
    (args.output / "predictions.jsonl").write_bytes(prediction_bytes)
    (args.output / "metrics.json").write_bytes((json.dumps(report, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"split_counts": report["split_counts"], "results": results}, indent=2))


if __name__ == "__main__": main()
