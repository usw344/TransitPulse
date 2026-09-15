"""Run one deterministic contiguous observed-span diagnostic, never a terminal claim."""

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
from transitpulse_ml.simulator import ScenarioConfig, Segment, TrainSegmentMedianPredictor, simulate_run  # noqa: E402


def read_jsonl(path: Path) -> tuple[list[dict[str, object]], bytes]:
    data = path.read_bytes()
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line], data


def metrics(actual: list[float], predicted: list[float]) -> dict[str, float | int]:
    errors = sorted(abs(a - p) for a, p in zip(actual, predicted, strict=True))
    return {"n": len(errors), "mae": sum(errors) / len(errors), "median_ae": median(errors), "p90_ae": errors[round((len(errors) - 1) * .9)], "rmse": (sum((a - p) ** 2 for a, p in zip(actual, predicted, strict=True)) / len(errors)) ** .5}


def run_key(row: dict[str, object]) -> str:
    return "|".join((str(row["service_day"]), str(row["trip_gtfs_id"]), str(row["vehicle_id"])))


def contiguous(previous: dict[str, object], current: dict[str, object]) -> bool:
    return int(previous["to_stop_sequence"]) == int(current["from_stop_sequence"]) and str(previous["to_stop_id"]) == str(current["from_stop_id"])


def candidate_spans(labels: list[dict[str, object]], service_day: str, requested_run: str | None) -> list[tuple[str, list[dict[str, object]]]]:
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in labels:
        if row["service_day"] == service_day: groups[run_key(row)].append(row)
    spans: list[tuple[str, list[dict[str, object]]]] = []
    for key, rows in groups.items():
        if requested_run and key != requested_run: continue
        rows.sort(key=lambda row: (int(row["from_stop_sequence"]), str(row["observed_start"])))
        current: list[dict[str, object]] = []
        for row in rows:
            if current and not contiguous(current[-1], row):
                if len(current) >= 2: spans.append((key, current))
                current = []
            current.append(row)
        if len(current) >= 2: spans.append((key, current))
    return spans


def as_segment(row: dict[str, object]) -> Segment:
    return Segment(int(row["from_stop_sequence"]), int(row["to_stop_sequence"]), str(row["route_gtfs_id"]), str(row["from_stop_id"]), str(row["to_stop_id"]), float(row["scheduled_seconds"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--model-rows", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--baseline-metrics", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--service-day")
    parser.add_argument("--run-id")
    parser.add_argument("--arrival-anchor-shift-seconds", type=float, default=0.0)
    parser.add_argument("--travel-time-multiplier", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if args.output.exists(): parser.error(f"output exists: {args.output}")
    labels, labels_bytes = read_jsonl(args.labels); rows, rows_bytes = read_jsonl(args.model_rows)
    manifest, baseline_bytes = json.loads(args.model_manifest.read_text(encoding="utf-8")), args.baseline_metrics.read_bytes()
    baseline = json.loads(baseline_bytes.decode("utf-8"))
    rows_hash = sha256(rows_bytes).hexdigest()
    if manifest.get("rows_sha256") != rows_hash or baseline.get("source_rows_sha256") != rows_hash: parser.error("M3/M4 provenance mismatch")
    train = [row for row in rows if row["split"] == "train"]
    test_days = sorted({str(row["service_day"]) for row in rows if row["split"] == "test"})
    if len(test_days) != 1 and not args.service_day: parser.error("--service-day required for multiple test days")
    service_day = args.service_day or test_days[0]
    if service_day not in test_days: parser.error("service day must be an M3 held-out test day")
    spans = candidate_spans(labels, service_day, args.run_id)
    if not spans: parser.error("no contiguous multi-segment diagnostic span")
    selected_run_id, selected = max(spans, key=lambda item: (len(item[1]), item[0], -int(item[1][0]["from_stop_sequence"])))
    selected_model = baseline["model_selection"]["selected_model"]
    if selected_model != "segment_median": parser.error(f"unsupported M4 selected model for exact simulator provenance: {selected_model}")
    predictor = TrainSegmentMedianPredictor(train)
    scenario = ScenarioConfig("m6-contiguous-span-diagnostic-v2", random_seed=args.seed, arrival_anchor_shift_seconds=args.arrival_anchor_shift_seconds, travel_time_multiplier=args.travel_time_multiplier)
    simulation = simulate_run(segments=[as_segment(row) for row in selected], predictor=predictor, run_start_at=datetime.fromisoformat(str(selected[0]["observed_start"])), scenario=scenario)
    actual, predicted = [float(row["travel_seconds"]) for row in selected], [event.predicted_arrival_to_arrival_seconds for event in simulation.events]
    actual_span_end = datetime.fromisoformat(str(selected[-1]["observed_end"])); simulated_span_end = datetime.fromisoformat(simulation.span_end_arrival_at)
    report = {"schema_version": 3, "simulator": "deterministic-contiguous-arrival-to-arrival-v3", "scope": "Partial contiguous observed label span; not a complete route or terminal simulation.", "predictor": selected_model, "baseline_metrics_sha256": sha256(baseline_bytes).hexdigest(), "source_labels_sha256": sha256(labels_bytes).hexdigest(), "source_model_rows_sha256": rows_hash, "selected_run_id": selected_run_id, "service_day": service_day, "from_stop_sequence": selected[0]["from_stop_sequence"], "to_stop_sequence": selected[-1]["to_stop_sequence"], "simulation": simulation.to_dict(), "validation": {"segment_travel_time": metrics(actual, predicted), "span_end_arrival_error_seconds": (simulated_span_end - actual_span_end).total_seconds(), "evaluation_note": "Held-out historical diagnostic anchored at the first observed arrival. It does not forecast, simulate dwell/departure separately, or establish terminal behavior."}, "limitations": ["Strict discontinuities are split rather than stitched.", "No terminal, layover, fleet/block, headway, bunching, gap, passenger, or demand claim is supported."], "reproducible_command": f".\\.venv\\Scripts\\python.exe scripts\\run_segment_simulation.py --labels {args.labels} --model-rows {args.model_rows} --model-manifest {args.model_manifest} --baseline-metrics {args.baseline_metrics} --output {args.output} --seed {args.seed}"}
    args.output.mkdir(parents=True); (args.output / "scenario.json").write_bytes((json.dumps({"scenario": scenario.__dict__, "scope": report["scope"]}, indent=2) + "\n").encode("utf-8")); (args.output / "result.json").write_bytes((json.dumps(report, indent=2) + "\n").encode("utf-8")); print(json.dumps({"selected_run_id": selected_run_id, "from_stop_sequence": report["from_stop_sequence"], "to_stop_sequence": report["to_stop_sequence"], "validation": report["validation"]}, indent=2))


if __name__ == "__main__": main()
