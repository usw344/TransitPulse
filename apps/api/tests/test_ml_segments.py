from datetime import datetime, timedelta, timezone

import pytest

from transitpulse_ml.segments import (
    MatchedObservation,
    TripStop,
    extract_segment_traversals,
    project_onto_polyline,
)


BASE = datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc)


def observation(seconds: int, progress: float, sequence: int, *, recorded_offset: int = 0):
    return MatchedObservation(
        observed_at=BASE + timedelta(seconds=seconds),
        recorded_at=BASE + timedelta(seconds=seconds + recorded_offset),
        current_stop_sequence=sequence,
        progress_m=progress,
        lateral_error_m=4.0,
    )


def test_polyline_projection_returns_progress_and_lateral_error() -> None:
    match = project_onto_polyline(
        (-113.5000, 53.5005),
        [(-113.5000, 53.5000), (-113.5000, 53.5010)],
    )
    assert match.progress_m == pytest.approx(55.6, abs=0.5)
    assert match.lateral_error_m == pytest.approx(0.0, abs=0.1)


def test_extracts_arrival_crossings_with_sparse_gtfs_sequences() -> None:
    stops = [
        TripStop("A", 10, 100.0, 3600, 3610),
        TripStop("B", 30, 300.0, 3720, 3730),
        TripStop("C", 55, 500.0, 3840, 3850),
    ]
    result = extract_segment_traversals(
        stops,
        [
            observation(0, 50, 10),
            observation(30, 150, 30),
            observation(60, 250, 30),
            observation(90, 350, 55),
            observation(120, 450, 55),
            observation(150, 550, 55),
        ],
    )
    assert result.rejected_reason is None
    assert [(row.from_stop_id, row.to_stop_id) for row in result.traversals] == [
        ("A", "B"),
        ("B", "C"),
    ]
    assert [row.travel_seconds for row in result.traversals] == [60.0, 60.0]
    assert [row.scheduled_seconds for row in result.traversals] == [120, 120]


def test_duplicate_timestamp_uses_latest_recording_deterministically() -> None:
    stops = [TripStop("A", 1, 100, 0, 0), TripStop("B", 8, 200, 100, 100)]
    result = extract_segment_traversals(
        stops,
        [
            observation(0, 50, 1),
            observation(30, 90, 1),
            observation(30, 150, 8, recorded_offset=2),
            observation(60, 250, 8),
        ],
    )
    assert len(result.traversals) == 1
    assert result.traversals[0].travel_seconds == pytest.approx(30.0)


@pytest.mark.parametrize(
    ("observations", "reason"),
    [
        ([observation(0, 100, 1), observation(30, 50, 8)], "nonmonotonic_vehicle_progress"),
        ([observation(0, 100, 999), observation(30, 200, 8)], "unmatched_trip_stop_sequence"),
    ],
)
def test_rejects_invalid_runs(observations, reason: str) -> None:
    stops = [TripStop("A", 1, 100, 0, 0), TripStop("B", 8, 200, 100, 100)]
    assert extract_segment_traversals(stops, observations).rejected_reason == reason


def test_large_bracketing_gap_does_not_fabricate_label() -> None:
    stops = [TripStop("A", 1, 100, 0, 0), TripStop("B", 8, 200, 100, 100)]
    result = extract_segment_traversals(
        stops,
        [observation(0, 50, 1), observation(300, 250, 8)],
    )
    assert result.traversals == ()
