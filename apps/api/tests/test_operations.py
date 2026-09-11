from datetime import datetime, timedelta, timezone

from transitpulse_api.operations import (
    OperationsThresholds,
    calculate_headways,
    classify_delay,
    classify_service,
)


THRESHOLDS = OperationsThresholds(
    on_time_seconds=60,
    major_delay_seconds=300,
    bunching_ratio=0.5,
    gap_ratio=1.75,
)


def test_delay_classification_thresholds_are_explicit() -> None:
    assert classify_delay(None, THRESHOLDS) == "NO_LIVE_DATA"
    assert classify_delay(60, THRESHOLDS) == "ON_TIME"
    assert classify_delay(-61, THRESHOLDS) == "EARLY"
    assert classify_delay(61, THRESHOLDS) == "MINOR_DELAY"
    assert classify_delay(300, THRESHOLDS) == "MAJOR_DELAY"


def test_headway_calculation_detects_bunching_and_gap_against_schedule_baseline() -> None:
    start = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    analysis = calculate_headways(
        [start, start + timedelta(minutes=4), start + timedelta(minutes=20)],
        THRESHOLDS,
        expected_headway_seconds=600,
    )
    assert analysis.headways_seconds == (240, 960)
    assert analysis.bunching is True
    assert analysis.service_gap is False
    assert classify_service(
        has_fresh_live_data=True,
        delays_seconds=[400],
        headway=analysis,
        thresholds=THRESHOLDS,
    ) == "BUNCHING"


def test_service_gap_and_stale_state_are_not_hidden() -> None:
    start = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    analysis = calculate_headways(
        [start, start + timedelta(minutes=10), start + timedelta(minutes=31)],
        THRESHOLDS,
        expected_headway_seconds=600,
    )
    assert analysis.bunching is False
    assert analysis.service_gap is True
    assert classify_service(
        has_fresh_live_data=False,
        delays_seconds=[0],
        headway=analysis,
        thresholds=THRESHOLDS,
    ) == "NO_LIVE_DATA"
