from datetime import datetime, timedelta, timezone

from transitpulse_api.analytics import (
    RecordedPoint,
    delay_bands,
    delay_buckets,
    delay_distribution,
    headway_metrics,
    observed_stop_sequence_events,
    percentile,
)
from transitpulse_api.operations import OperationsThresholds


THRESHOLDS = OperationsThresholds(
    on_time_seconds=60,
    major_delay_seconds=300,
    bunching_ratio=0.5,
    gap_ratio=1.75,
)


def test_delay_distribution_uses_direct_samples_and_interpolated_percentiles() -> None:
    summary = delay_distribution([0, 60, 120, 180, 240, None])
    assert summary.sample_count == 5
    assert summary.median_seconds == 120
    assert summary.percentile_10_seconds == 24
    assert summary.percentile_90_seconds == 216
    assert percentile([], 0.5) is None


def test_delay_bands_use_central_threshold_boundaries_and_ignore_missing_values() -> None:
    bands = delay_bands(
        [-61, -60, 0, 60, 61, 300, 301, 600, 601, None],
        on_time_seconds=60,
        major_delay_seconds=300,
        severe_delay_seconds=600,
    )
    assert bands.sample_count == 9
    assert bands.early_count == 1
    assert bands.on_time_count == 3
    assert bands.late_under_5_count == 2
    assert bands.late_5_to_10_count == 2
    assert bands.late_over_10_count == 1


def test_observed_sequence_events_deduplicate_snapshots_but_keep_vehicle_progress() -> None:
    start = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    points = [
        RecordedPoint(start, "bus-1", "trip-a", 1, 0),
        RecordedPoint(start + timedelta(seconds=30), "bus-1", "trip-a", 1, 30),
        RecordedPoint(start + timedelta(minutes=1), "bus-1", "trip-a", 2, 60),
        RecordedPoint(start + timedelta(minutes=1), "bus-2", "trip-b", 1, 120),
    ]
    events = observed_stop_sequence_events(points)
    assert [(event.vehicle_id, event.stop_sequence) for event in events] == [
        ("bus-1", 2),
    ]


def test_headways_compare_observed_events_with_schedule_for_bunching_and_gaps() -> None:
    start = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    metrics = headway_metrics(
        [start, start + timedelta(minutes=4), start + timedelta(minutes=23)],
        THRESHOLDS,
        scheduled_baseline_seconds=600,
    )
    assert metrics.sample_count == 2
    assert metrics.median_seconds == 690
    assert metrics.baseline_seconds == 600
    assert metrics.bunching_event_count == 1
    assert metrics.service_gap_event_count == 1
    assert metrics.variability_seconds == 450


def test_headways_are_explicitly_sparse_with_zero_or_one_interval() -> None:
    start = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    empty = headway_metrics([], THRESHOLDS, scheduled_baseline_seconds=600)
    sparse = headway_metrics([start, start + timedelta(minutes=10)], THRESHOLDS, scheduled_baseline_seconds=600)
    assert empty.sample_count == 0
    assert empty.median_seconds is None
    assert empty.variability_seconds is None
    assert sparse.sample_count == 1
    assert sparse.median_seconds == 600
    assert sparse.variability_seconds is None


def test_empty_delay_window_returns_no_confident_values() -> None:
    summary = delay_distribution([None, None])
    assert summary.sample_count == 0
    assert summary.median_seconds is None
    assert summary.percentile_90_seconds is None


def test_delay_buckets_preserve_empty_and_sparse_intervals() -> None:
    start = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    buckets = delay_buckets(
        [
            RecordedPoint(start + timedelta(minutes=10), "bus-1", "trip-a", 1, 120),
            RecordedPoint(start + timedelta(hours=2, minutes=10), "bus-2", "trip-b", 1, None),
        ],
        start=start,
        end=start + timedelta(hours=3),
        on_time_seconds=60,
    )
    assert len(buckets) == 3
    assert buckets[0].median_delay_seconds == 120
    assert buckets[0].late_observation_count == 1
    assert buckets[1].observation_count == 0
    assert buckets[2].delay_observation_count == 0


def test_sequence_regressions_do_not_emit_repeat_arrivals() -> None:
    """Feed jitter must not manufacture extra arrivals at one stop."""

    start = datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc)
    # A vehicle observed flapping between stop 40 and 41 on the same trip.
    sequences = [40, 40, 41, 40, 41, 40, 41, 42]
    points = [
        RecordedPoint(
            at=start + timedelta(seconds=30 * index),
            vehicle_id="2417",
            trip_id="32904627",
            current_stop_sequence=sequence,
            delay_seconds=0,
        )
        for index, sequence in enumerate(sequences)
    ]
    events = observed_stop_sequence_events(points)
    # 40 is the window-boundary baseline; only 41 and 42 are genuine progress.
    assert [event.stop_sequence for event in events] == [41, 42]


def test_same_sequence_is_counted_again_on_a_new_trip() -> None:
    """A later trip legitimately revisits the same stop sequence."""

    start = datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc)
    points = [
        RecordedPoint(at=start, vehicle_id="2417", trip_id="trip-a", current_stop_sequence=5, delay_seconds=0),
        RecordedPoint(at=start + timedelta(minutes=1), vehicle_id="2417", trip_id="trip-a", current_stop_sequence=6, delay_seconds=0),
        RecordedPoint(at=start + timedelta(minutes=2), vehicle_id="2417", trip_id="trip-b", current_stop_sequence=5, delay_seconds=0),
        RecordedPoint(at=start + timedelta(minutes=3), vehicle_id="2417", trip_id="trip-b", current_stop_sequence=6, delay_seconds=0),
    ]
    events = observed_stop_sequence_events(points)
    assert [(event.trip_id, event.stop_sequence) for event in events] == [
        ("trip-a", 6),
        ("trip-b", 5),
        ("trip-b", 6),
    ]
