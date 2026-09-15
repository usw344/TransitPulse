"""Score contiguous observed label spans without calling partial spans terminals.

This evaluator intentionally withholds Gate 5 claims when M2 labels do not
cover a complete terminal-to-terminal path.  It is a diagnostic for causal
segment predictions and their accumulation over only contiguous observed spans.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
from transitpulse_ml.simulator import (  # noqa: E402
    ScenarioConfig,
    Segment,
    StopArrivalEvent,
    TrainSegmentMedianPredictor,
    simulate_run,
    summarize_anchored_stop_headways,
)


def read_jsonl(path: Path) -> tuple[list[dict[str, object]], bytes]:
    data = path.read_bytes()
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line], data


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def metric_summary(actual: list[float], predicted: list[float]) -> dict[str, float | int]:
    errors = sorted(abs(actual_value - predicted_value) for actual_value, predicted_value in zip(actual, predicted, strict=True))
    return {"n": len(errors), "mae": sum(errors) / len(errors), "median_ae": median(errors), "p90_ae": percentile(errors, 0.9), "rmse": (sum((actual_value - predicted_value) ** 2 for actual_value, predicted_value in zip(actual, predicted, strict=True)) / len(errors)) ** 0.5}


def run_key(row: dict[str, object]) -> str:
    return "|".join((str(row["service_day"]), str(row["trip_gtfs_id"]), str(row["vehicle_id"])))


def is_contiguous(previous: dict[str, object], current: dict[str, object]) -> bool:
    return int(previous["to_stop_sequence"]) == int(current["from_stop_sequence"]) and str(previous["to_stop_id"]) == str(current["from_stop_id"])


def contiguous_spans(labels: list[dict[str, object]], test_days: set[str]) -> tuple[list[tuple[str, list[dict[str, object]]]], dict[str, int]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for label in labels:
        if str(label["service_day"]) in test_days: grouped[run_key(label)].append(label)
    spans: list[tuple[str, list[dict[str, object]]]] = []
    diagnostics = {"held_out_labels_total": sum(len(rows) for rows in grouped.values()), "observed_runs": len(grouped), "run_path_discontinuities": 0, "single_label_spans_excluded": 0}
    for key, rows in grouped.items():
        rows.sort(key=lambda row: (int(row["from_stop_sequence"]), str(row["observed_start"])))
        current: list[dict[str, object]] = []
        for row in rows:
            if current and not is_contiguous(current[-1], row):
                diagnostics["run_path_discontinuities"] += 1
                if len(current) >= 2: spans.append((key, current))
                else: diagnostics["single_label_spans_excluded"] += 1
                current = []
            current.append(row)
        if len(current) >= 2: spans.append((key, current))
        elif current: diagnostics["single_label_spans_excluded"] += 1
    return sorted(spans, key=lambda item: (item[0], int(item[1][0]["from_stop_sequence"]))), diagnostics


def as_segment(row: dict[str, object]) -> Segment:
    return Segment(int(row["from_stop_sequence"]), int(row["to_stop_sequence"]), str(row["route_gtfs_id"]), str(row["from_stop_id"]), str(row["to_stop_id"]), float(row["scheduled_seconds"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--model-rows", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--baseline-metrics", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if args.output.exists(): parser.error(f"output exists: {args.output}")
    labels, labels_bytes = read_jsonl(args.labels)
    model_rows, model_rows_bytes = read_jsonl(args.model_rows)
    model_manifest = json.loads(args.model_manifest.read_text(encoding="utf-8"))
    baseline_bytes = args.baseline_metrics.read_bytes()
    baseline = json.loads(baseline_bytes.decode("utf-8"))
    rows_hash = sha256(model_rows_bytes).hexdigest()
    if model_manifest.get("rows_sha256") != rows_hash or baseline.get("source_rows_sha256") != rows_hash: parser.error("M3/M4 provenance mismatch")
    train = [row for row in model_rows if row["split"] == "train"]
    test_days = {str(row["service_day"]) for row in model_rows if row["split"] == "test"}
    if not train or not test_days: parser.error("model rows must include chronological train and test splits")
    selected_model = baseline["model_selection"]["selected_model"]
    if selected_model != "segment_median": parser.error(f"unsupported M4 selected model for exact simulator provenance: {selected_model}")
    predictor = TrainSegmentMedianPredictor(train)
    train_residuals = [float(row["target_travel_seconds"]) - predictor.predict_seconds(Segment(0, 1, str(row["route_gtfs_id"]), str(row["from_stop_id"]), str(row["to_stop_id"]), float(row["scheduled_seconds"])), datetime.fromisoformat(str(row["prediction_at"]))) for row in train]
    lower_residual, upper_residual = percentile(train_residuals, .1), percentile(train_residuals, .9)
    spans, diagnostics = contiguous_spans(labels, test_days)
    if not spans: parser.error("no contiguous multi-segment held-out spans; validation is blocked")
    actual_segments: list[float] = []
    predicted_segments: list[float] = []
    actual_spans: list[float] = []
    predicted_spans: list[float] = []
    stop_arrivals: list[StopArrivalEvent] = []
    covered, reported_spans = 0, []
    for run_id, span in spans:
        simulation = simulate_run(segments=[as_segment(row) for row in span], predictor=predictor, run_start_at=datetime.fromisoformat(str(span[0]["observed_start"])), scenario=ScenarioConfig("m7-contiguous-span-diagnostic-v2", random_seed=args.seed))
        actual_values = [float(row["travel_seconds"]) for row in span]
        predicted_values = [event.predicted_arrival_to_arrival_seconds for event in simulation.events]
        actual_segments.extend(actual_values); predicted_segments.extend(predicted_values)
        covered += sum(predicted + lower_residual <= actual <= predicted + upper_residual for actual, predicted in zip(actual_values, predicted_values, strict=True))
        actual_span_seconds = (datetime.fromisoformat(str(span[-1]["observed_end"])) - datetime.fromisoformat(str(span[0]["observed_start"]))).total_seconds()
        predicted_span_seconds = (datetime.fromisoformat(simulation.span_end_arrival_at) - datetime.fromisoformat(simulation.span_start_arrival_at)).total_seconds()
        actual_spans.append(actual_span_seconds); predicted_spans.append(predicted_span_seconds)
        for row, event in zip(span, simulation.events, strict=True):
            direction = row.get("direction_id")
            stop_arrivals.append(
                StopArrivalEvent(
                    service_day=str(row["service_day"]),
                    route_gtfs_id=str(row["route_gtfs_id"]),
                    direction_id=int(direction) if direction is not None else None,
                    to_stop_id=str(row["to_stop_id"]),
                    to_stop_sequence=int(row["to_stop_sequence"]),
                    run_id=run_id,
                    observed_arrival_at=datetime.fromisoformat(str(row["observed_end"])),
                    predicted_arrival_at=datetime.fromisoformat(event.predicted_arrival_at_to_stop),
                )
            )
        reported_spans.append({"run_id": run_id, "from_stop_sequence": span[0]["from_stop_sequence"], "to_stop_sequence": span[-1]["to_stop_sequence"], "segments": len(span), "span_end_arrival_error_seconds": predicted_span_seconds - actual_span_seconds})
    anchored_headways = summarize_anchored_stop_headways(stop_arrivals)
    report = {
        "schema_version": 4, "simulator": "deterministic-contiguous-arrival-to-arrival-v4", "predictor": selected_model, "random_seed": args.seed, "test_service_days": sorted(test_days),
        "source_labels": str(args.labels), "source_labels_sha256": sha256(labels_bytes).hexdigest(), "source_model_rows": str(args.model_rows), "source_model_rows_sha256": rows_hash, "baseline_metrics": str(args.baseline_metrics), "baseline_metrics_sha256": sha256(baseline_bytes).hexdigest(),
        "evaluator_script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(), "simulator_source_sha256": sha256((REPO_ROOT / "apps" / "api" / "transitpulse_ml" / "simulator.py").read_bytes()).hexdigest(),
        **diagnostics, "eligible_contiguous_spans": len(spans), "eligible_labels": len(actual_segments), "excluded_held_out_labels": diagnostics["held_out_labels_total"] - len(actual_segments),
        "segment_travel_time": metric_summary(actual_segments, predicted_segments), "contiguous_span_travel_time": metric_summary(actual_spans, predicted_spans),
        "empirical_train_residual_interval": {"nominal_coverage": .8, "lower_residual_seconds": lower_residual, "upper_residual_seconds": upper_residual, "held_out_coverage": covered / len(actual_segments), "interpretation": "Uncalibrated empirical train-residual diagnostic only."},
        "full_route_terminal_validation": {"status": "NOT REACHED", "validated_complete_terminal_runs": 0, "reason": "The M2 label artifact has strict quality exclusions and does not contain complete terminal-to-terminal paths. Partial contiguous spans are deliberately not called terminal arrivals."},
        "headway_gap_bunching_validation": {"status": "NOT REACHED", "reason": "No route/block-level, multi-vehicle schedule simulation or corresponding held-out operational-pattern evaluation exists.", "anchored_stop_arrival_diagnostic": anchored_headways},
        "spans": reported_spans, "validation_status": "BLOCKED FOR GATE 5 — diagnostic partial contiguous spans only; no complete-route terminal, dwell/layover, headway, gap, bunching, or multi-day validation.",
        "limitations": ["Only one held-out service day.", "All discontinuous paths are split rather than stitched.", "Labels are arrival-to-arrival, so dwell and departure timing are not separately estimated.", "No passenger demand, fleet, block, capacity, traffic, weather, or operator constraints."],
        "reproducible_command": f".\\.venv\\Scripts\\python.exe scripts\\evaluate_simulator.py --labels {args.labels} --model-rows {args.model_rows} --model-manifest {args.model_manifest} --baseline-metrics {args.baseline_metrics} --output {args.output} --seed {args.seed}",
    }
    args.output.mkdir(parents=True); (args.output / "metrics.json").write_bytes((json.dumps(report, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({key: report[key] for key in ("eligible_contiguous_spans", "eligible_labels", "segment_travel_time", "contiguous_span_travel_time", "full_route_terminal_validation", "validation_status")}, indent=2))


if __name__ == "__main__": main()
