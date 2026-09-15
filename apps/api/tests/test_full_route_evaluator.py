from __future__ import annotations

import pytest

from transitpulse_ml.full_route_evaluator import (
    evaluate_complete_test_runs,
    strict_complete_test_runs,
)


def _label(
    *,
    from_sequence: int,
    to_sequence: int,
    from_stop: str,
    to_stop: str,
    observed_start: str,
    observed_end: str,
    travel_seconds: float,
) -> dict[str, object]:
    return {
        "service_day": "2026-09-12",
        "static_feed_id": "feed-1",
        "trip_gtfs_id": "trip-1",
        "vehicle_id": "vehicle-1",
        "route_gtfs_id": "004",
        "direction_id": 0,
        "from_stop_sequence": from_sequence,
        "to_stop_sequence": to_sequence,
        "from_stop_id": from_stop,
        "to_stop_id": to_stop,
        "observed_start": observed_start,
        "observed_end": observed_end,
        "travel_seconds": travel_seconds,
        "scheduled_seconds": 10,
    }


def _complete_audit() -> dict[str, object]:
    return {
        "complete_terminal_to_terminal_runs": [
            {
                "service_day": "2026-09-12",
                "static_feed_id": "feed-1",
                "trip_gtfs_id": "trip-1",
                "vehicle_id": "vehicle-1",
                "edges": 2,
            }
        ]
    }


def test_full_route_evaluator_scores_only_audit_confirmed_complete_test_runs() -> None:
    labels = [
        _label(
            from_sequence=1,
            to_sequence=2,
            from_stop="a",
            to_stop="b",
            observed_start="2026-09-12T08:00:00-06:00",
            observed_end="2026-09-12T08:00:12-06:00",
            travel_seconds=12,
        ),
        _label(
            from_sequence=2,
            to_sequence=3,
            from_stop="b",
            to_stop="c",
            observed_start="2026-09-12T08:00:12-06:00",
            observed_end="2026-09-12T08:00:27-06:00",
            travel_seconds=15,
        ),
    ]
    model_rows = [
        {"split": "train", "route_gtfs_id": "004", "from_stop_id": "a", "to_stop_id": "b", "target_travel_seconds": 10},
        {"split": "train", "route_gtfs_id": "004", "from_stop_id": "b", "to_stop_id": "c", "target_travel_seconds": 20},
        {"split": "test", "service_day": "2026-09-12"},
    ]

    result = evaluate_complete_test_runs(
        labels=labels,
        model_rows=model_rows,
        terminal_coverage_audit=_complete_audit(),
        random_seed=7,
    )

    assert result["complete_test_runs"] == 1
    assert result["complete_test_run_service_days"] == ["2026-09-12"]
    assert result["segment_travel_time"]["mae"] == pytest.approx(3.5)
    assert result["conditional_route_travel_time"]["mae"] == pytest.approx(3)
    assert result["conditional_schedule_duration_deviation"]["mae"] == pytest.approx(3)
    assert result["dwell_departure_validation"]["status"] == "NOT REACHED"
    assert result["validation_status"].startswith("BLOCKED FOR GATE 5")


def test_full_route_evaluator_rejects_an_audit_listed_discontinuous_run() -> None:
    labels = [
        _label(
            from_sequence=1,
            to_sequence=2,
            from_stop="a",
            to_stop="b",
            observed_start="2026-09-12T08:00:00-06:00",
            observed_end="2026-09-12T08:00:12-06:00",
            travel_seconds=12,
        ),
        _label(
            from_sequence=3,
            to_sequence=4,
            from_stop="c",
            to_stop="d",
            observed_start="2026-09-12T08:00:13-06:00",
            observed_end="2026-09-12T08:00:25-06:00",
            travel_seconds=12,
        ),
    ]

    with pytest.raises(ValueError, match="discontinuous"):
        strict_complete_test_runs(
            labels,
            complete_run_keys={("2026-09-12", "feed-1", "trip-1", "vehicle-1")},
            test_service_days={"2026-09-12"},
        )
