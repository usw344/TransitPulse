from datetime import datetime

import pytest

from transitpulse_ml.simulator import (
    ScenarioConfig,
    Segment,
    StopArrivalEvent,
    TrainSegmentHourMedianPredictor,
    TrainSegmentMedianPredictor,
    simulate_run,
    summarize_anchored_stop_headways,
)


class FixedPredictor:
    def predict_seconds(self, segment: Segment, prediction_at: datetime) -> float:
        return 30.0 + segment.from_stop_sequence


def test_simulation_is_deterministic_and_accounts_for_scenario_time() -> None:
    segments = [
        Segment(1, 2, "004", "a", "b", 60),
        Segment(2, 3, "004", "b", "c", 60),
    ]
    scenario = ScenarioConfig("test", random_seed=7, arrival_anchor_shift_seconds=10)
    start = datetime.fromisoformat("2026-09-10T08:00:00-06:00")
    first = simulate_run(segments=segments, predictor=FixedPredictor(), run_start_at=start, scenario=scenario)
    second = simulate_run(segments=segments, predictor=FixedPredictor(), run_start_at=start, scenario=scenario)
    assert first == second
    assert first.events[0].arrival_at_from_stop == "2026-09-10T08:00:10-06:00"
    assert first.events[1].arrival_at_from_stop == "2026-09-10T08:00:41-06:00"
    assert first.span_end_arrival_at == "2026-09-10T08:01:13-06:00"


def test_simulation_rejects_non_increasing_sequences() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        simulate_run(
            segments=[Segment(2, 3, "004", "a", "b", 60), Segment(2, 3, "004", "b", "c", 60)],
            predictor=FixedPredictor(),
            run_start_at=datetime.fromisoformat("2026-09-10T08:00:00-06:00"),
            scenario=ScenarioConfig("test", random_seed=7),
        )


def test_train_baseline_never_uses_a_held_out_value() -> None:
    predictor = TrainSegmentHourMedianPredictor(
        [{"route_gtfs_id": "004", "from_stop_id": "a", "to_stop_id": "b", "hour": 8, "target_travel_seconds": 31}]
    )
    segment = Segment(1, 2, "004", "a", "b", 60)
    assert predictor.predict_seconds(segment, datetime.fromisoformat("2026-09-10T08:00:00-06:00")) == 31
    assert predictor.predict_seconds(segment, datetime.fromisoformat("2026-09-10T09:00:00-06:00")) == 31


def test_segment_median_predictor_has_no_hour_feature() -> None:
    predictor = TrainSegmentMedianPredictor(
        [
            {"route_gtfs_id": "004", "from_stop_id": "a", "to_stop_id": "b", "hour": 8, "target_travel_seconds": 20},
            {"route_gtfs_id": "004", "from_stop_id": "a", "to_stop_id": "b", "hour": 18, "target_travel_seconds": 40},
        ]
    )
    segment = Segment(1, 2, "004", "a", "b", 60)
    assert predictor.predict_seconds(segment, datetime.fromisoformat("2026-09-10T08:00:00-06:00")) == 30
    assert predictor.predict_seconds(segment, datetime.fromisoformat("2026-09-10T18:00:00-06:00")) == 30


def test_simulation_rejects_a_discontinuous_stop_path() -> None:
    with pytest.raises(ValueError, match="contiguous stop path"):
        simulate_run(
            segments=[Segment(1, 2, "004", "a", "b", 60), Segment(3, 4, "004", "c", "d", 60)],
            predictor=FixedPredictor(),
            run_start_at=datetime.fromisoformat("2026-09-10T08:00:00-06:00"),
            scenario=ScenarioConfig("test", random_seed=7),
        )


def test_anchored_headway_diagnostic_is_explicitly_limited() -> None:
    events = [
        StopArrivalEvent("2026-09-10", "004", 0, "stop-b", 2, "run-a", datetime.fromisoformat("2026-09-10T08:00:00-06:00"), datetime.fromisoformat("2026-09-10T08:01:00-06:00")),
        StopArrivalEvent("2026-09-10", "004", 0, "stop-b", 2, "run-b", datetime.fromisoformat("2026-09-10T08:10:00-06:00"), datetime.fromisoformat("2026-09-10T08:14:00-06:00")),
        StopArrivalEvent("2026-09-10", "004", 0, "stop-b", 2, "run-c", datetime.fromisoformat("2026-09-10T08:23:00-06:00"), datetime.fromisoformat("2026-09-10T08:25:00-06:00")),
    ]

    result = summarize_anchored_stop_headways(events)

    assert result["status"] == "PARTIAL_ANCHORED_DIAGNOSTIC_ONLY"
    assert result["eligible_adjacent_pairs"] == 2
    assert result["headway_seconds"]["mae"] == 150
    assert "not a schedule" in result["interpretation"]
