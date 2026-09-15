"""Strict, conditional full-route scoring for audit-confirmed terminal runs.

This module is intentionally narrower than Gate 5.  It provides reproducible
route/terminal and schedule-duration-deviation evidence only after the
terminal coverage audit confirms complete, no-stitch runs.  Every simulation is
anchored to the observed first-stop arrival, so the output must not be called a
schedule-departure, layover, fleet/block, or service-regularity simulation.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import median
from typing import Any, Sequence

from transitpulse_ml.simulator import (
    ScenarioConfig,
    Segment,
    TrainSegmentMedianPredictor,
    simulate_run,
)


RunKey = tuple[str, str, str, str]


def run_key(row: dict[str, Any]) -> RunKey:
    """Return the immutable service-day/feed/trip/vehicle run identity."""

    return (
        str(row["service_day"]),
        str(row["static_feed_id"]),
        str(row["trip_gtfs_id"]),
        str(row["vehicle_id"]),
    )


def metric_summary(actual: Sequence[float], predicted: Sequence[float]) -> dict[str, float | int]:
    """Report deterministic absolute-error metrics for equally sized samples."""

    if not actual or len(actual) != len(predicted):
        raise ValueError("actual and predicted values must be non-empty and equally sized")
    errors = sorted(abs(value - estimate) for value, estimate in zip(actual, predicted, strict=True))
    return {
        "n": len(errors),
        "mae": sum(errors) / len(errors),
        "median_ae": median(errors),
        "p90_ae": errors[round((len(errors) - 1) * 0.9)],
        "rmse": (sum((value - estimate) ** 2 for value, estimate in zip(actual, predicted, strict=True)) / len(errors)) ** 0.5,
    }


def audit_run_keys(terminal_coverage_audit: dict[str, Any]) -> set[RunKey]:
    """Extract exactly the runs the strict external audit called complete."""

    return {
        (
            str(run["service_day"]),
            str(run["static_feed_id"]),
            str(run["trip_gtfs_id"]),
            str(run["vehicle_id"]),
        )
        for run in terminal_coverage_audit.get("complete_terminal_to_terminal_runs", [])
    }


def _is_contiguous(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    return (
        int(previous["to_stop_sequence"]) == int(current["from_stop_sequence"])
        and str(previous["to_stop_id"]) == str(current["from_stop_id"])
    )


def strict_complete_test_runs(
    labels: Sequence[dict[str, Any]],
    *,
    complete_run_keys: set[RunKey],
    test_service_days: set[str],
) -> list[tuple[RunKey, list[dict[str, Any]]]]:
    """Return only complete audit-listed test runs, rechecking exact adjacency."""

    grouped: dict[RunKey, list[dict[str, Any]]] = defaultdict(list)
    for row in labels:
        key = run_key(row)
        if key in complete_run_keys and key[0] in test_service_days:
            grouped[key].append(row)

    eligible: list[tuple[RunKey, list[dict[str, Any]]]] = []
    for key, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: (int(row["from_stop_sequence"]), str(row["observed_start"])))
        if len(ordered) < 2:
            raise ValueError(f"audit-listed complete run has fewer than two labels: {key}")
        if any(not _is_contiguous(previous, current) for previous, current in zip(ordered, ordered[1:])):
            raise ValueError(f"audit-listed complete run is discontinuous: {key}")
        if any(str(row["route_gtfs_id"]) != str(ordered[0]["route_gtfs_id"]) for row in ordered):
            raise ValueError(f"audit-listed complete run mixes routes: {key}")
        eligible.append((key, ordered))
    return sorted(eligible, key=lambda item: item[0])


def _segment(row: dict[str, Any]) -> Segment:
    return Segment(
        from_stop_sequence=int(row["from_stop_sequence"]),
        to_stop_sequence=int(row["to_stop_sequence"]),
        route_gtfs_id=str(row["route_gtfs_id"]),
        from_stop_id=str(row["from_stop_id"]),
        to_stop_id=str(row["to_stop_id"]),
        scheduled_seconds=float(row["scheduled_seconds"]),
    )


def evaluate_complete_test_runs(
    *,
    labels: Sequence[dict[str, Any]],
    model_rows: Sequence[dict[str, Any]],
    terminal_coverage_audit: dict[str, Any],
    random_seed: int,
) -> dict[str, Any]:
    """Score audit-confirmed complete test runs with the train-only M4 baseline.

    The returned report deliberately leaves dwell/departure, layover, and
    route/block service regularity as NOT REACHED.  Their absence cannot be
    hidden by a good conditional route-duration score.
    """

    train_rows = [row for row in model_rows if row.get("split") == "train"]
    test_days = {str(row["service_day"]) for row in model_rows if row.get("split") == "test"}
    if not train_rows:
        raise ValueError("model rows contain no train split")
    if not test_days:
        raise ValueError("model rows contain no test split")
    complete_runs = strict_complete_test_runs(
        labels,
        complete_run_keys=audit_run_keys(terminal_coverage_audit),
        test_service_days=test_days,
    )
    if not complete_runs:
        raise ValueError("no audit-confirmed complete terminal runs occur in the held-out test dates")

    predictor = TrainSegmentMedianPredictor(train_rows)
    actual_segments: list[float] = []
    predicted_segments: list[float] = []
    actual_route_durations: list[float] = []
    predicted_route_durations: list[float] = []
    actual_schedule_deviations: list[float] = []
    predicted_schedule_deviations: list[float] = []
    reported_runs: list[dict[str, Any]] = []

    for key, rows in complete_runs:
        start_at = datetime.fromisoformat(str(rows[0]["observed_start"]))
        simulation = simulate_run(
            segments=[_segment(row) for row in rows],
            predictor=predictor,
            run_start_at=start_at,
            scenario=ScenarioConfig(
                scenario_id="m7-strict-full-route-conditional-v1",
                random_seed=random_seed,
            ),
        )
        actual_values = [float(row["travel_seconds"]) for row in rows]
        predicted_values = [event.predicted_arrival_to_arrival_seconds for event in simulation.events]
        actual_duration = (
            datetime.fromisoformat(str(rows[-1]["observed_end"]))
            - datetime.fromisoformat(str(rows[0]["observed_start"]))
        ).total_seconds()
        predicted_duration = (
            datetime.fromisoformat(simulation.span_end_arrival_at)
            - datetime.fromisoformat(simulation.span_start_arrival_at)
        ).total_seconds()
        scheduled_duration = sum(float(row["scheduled_seconds"]) for row in rows)
        actual_segments.extend(actual_values)
        predicted_segments.extend(predicted_values)
        actual_route_durations.append(actual_duration)
        predicted_route_durations.append(predicted_duration)
        actual_schedule_deviations.append(actual_duration - scheduled_duration)
        predicted_schedule_deviations.append(predicted_duration - scheduled_duration)
        reported_runs.append(
            {
                "run_key": list(key),
                "route_gtfs_id": str(rows[0]["route_gtfs_id"]),
                "direction_id": rows[0].get("direction_id"),
                "segments": len(rows),
                "observed_start_at": str(rows[0]["observed_start"]),
                "observed_terminal_arrival_at": str(rows[-1]["observed_end"]),
                "predicted_terminal_arrival_at": simulation.span_end_arrival_at,
                "scheduled_route_duration_seconds": scheduled_duration,
                "observed_route_duration_seconds": actual_duration,
                "predicted_route_duration_seconds": predicted_duration,
                "conditional_terminal_arrival_error_seconds": predicted_duration - actual_duration,
            }
        )

    return {
        "predictor": "segment_median",
        "random_seed": random_seed,
        "test_service_days": sorted(test_days),
        "complete_test_runs": len(complete_runs),
        "complete_test_run_service_days": sorted({key[0] for key, _ in complete_runs}),
        "eligible_segments": len(actual_segments),
        "segment_travel_time": metric_summary(actual_segments, predicted_segments),
        "conditional_route_travel_time": metric_summary(actual_route_durations, predicted_route_durations),
        "conditional_terminal_arrival": metric_summary(actual_route_durations, predicted_route_durations),
        "conditional_schedule_duration_deviation": metric_summary(
            actual_schedule_deviations,
            predicted_schedule_deviations,
        ),
        "dwell_departure_validation": {
            "status": "NOT REACHED",
            "reason": "Observed arrival-to-arrival labels and source-published estimates do not independently identify actual dwell and departure events.",
        },
        "layover_recovery_validation": {
            "status": "NOT REACHED",
            "reason": "No validated terminal turnaround, static block, or fleet continuity evidence is included in this conditional evaluator.",
        },
        "headway_gap_bunching_validation": {
            "status": "NOT REACHED",
            "reason": "Every full-route simulation is anchored to its own observed first arrival; no schedule-departure or route/block multi-vehicle simulation is present.",
        },
        "validation_status": "BLOCKED FOR GATE 5 — strict conditional full-route timing only; dwell/departure, layover, route/block headway-gap-bunching, and multi-day acceptance remain required.",
        "limitations": [
            "The simulator starts every run at its observed first-stop arrival, so terminal timing is conditional rather than an independent schedule-departure forecast.",
            "A coverage audit confirms no-stitch route continuity but does not supply actual dwell, departure, layover, fleet, or block labels.",
        ],
        "runs": reported_runs,
    }
