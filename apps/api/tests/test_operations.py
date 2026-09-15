from datetime import datetime, timedelta, timezone

from transitpulse_api.operations import (
    ATTENTION_STATES,
    OperationsThresholds,
    arrivals_within_horizon,
    calculate_headways,
    classify_delay,
    classify_service,
)


THRESHOLDS = OperationsThresholds(
    on_time_seconds=60,
    major_delay_seconds=300,
    bunching_ratio=0.5,
    gap_ratio=1.75,
    prediction_horizon_seconds=5400,
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


def test_prediction_horizon_excludes_next_service_period_arrivals() -> None:
    """A next-day prediction must not become a rider-visible service gap."""

    reference = datetime(2026, 9, 11, 22, 40, tzinfo=timezone.utc)
    arrivals = [
        reference + timedelta(minutes=2),
        reference + timedelta(minutes=22),
        reference + timedelta(minutes=42),
        # End-of-service boundary: the feed's next published arrival is 7.9h out.
        reference + timedelta(seconds=28280),
    ]
    kept, excluded = arrivals_within_horizon(
        arrivals, reference=reference, horizon_seconds=THRESHOLDS.prediction_horizon_seconds
    )
    assert excluded == 1
    assert len(kept) == 3

    bounded = calculate_headways(kept, THRESHOLDS, excluded_beyond_horizon=excluded)
    assert bounded.service_gap is False
    assert bounded.excluded_beyond_horizon == 1
    assert bounded.horizon_seconds == THRESHOLDS.prediction_horizon_seconds

    unbounded = calculate_headways(arrivals, THRESHOLDS)
    assert unbounded.service_gap is True, "regression guard: the defect this fixes"


def test_prediction_horizon_keeps_genuine_in_horizon_gap() -> None:
    """Bounding the horizon must not suppress a real gap inside current service."""

    reference = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    arrivals = [
        reference + timedelta(minutes=1),
        reference + timedelta(minutes=11),
        reference + timedelta(minutes=21),
        reference + timedelta(minutes=61),
    ]
    kept, excluded = arrivals_within_horizon(
        arrivals, reference=reference, horizon_seconds=THRESHOLDS.prediction_horizon_seconds
    )
    assert excluded == 0
    analysis = calculate_headways(kept, THRESHOLDS, excluded_beyond_horizon=excluded)
    assert analysis.service_gap is True


def test_prediction_horizon_is_inert_without_a_reference_timestamp() -> None:
    """With no published feed time, arrivals are passed through unfiltered."""

    reference = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    arrivals = [reference, reference + timedelta(hours=9)]
    kept, excluded = arrivals_within_horizon(arrivals, reference=None, horizon_seconds=5400)
    assert excluded == 0
    assert len(kept) == 2
    kept, excluded = arrivals_within_horizon(arrivals, reference=reference, horizon_seconds=0)
    assert excluded == 0
    assert len(kept) == 2


def test_absent_delay_evidence_is_not_reported_as_on_time() -> None:
    """A route with no delay sample must not be asserted as running on time."""

    empty = calculate_headways([], THRESHOLDS)
    assert classify_service(
        has_fresh_live_data=True,
        delays_seconds=[],
        headway=empty,
        thresholds=THRESHOLDS,
    ) == "NO_LIVE_DATA"

    # A real on-time sample still reports ON_TIME.
    assert classify_service(
        has_fresh_live_data=True,
        delays_seconds=[10],
        headway=empty,
        thresholds=THRESHOLDS,
    ) == "ON_TIME"


def test_route_status_describes_the_typical_trip_not_the_single_worst() -> None:
    """One late trip must not label a whole route MAJOR DELAY beside an average of
    +2 minutes; on a real afternoon that flagged nine routes in ten."""

    empty = calculate_headways([], THRESHOLDS)

    def status(delays: list[int]) -> str:
        return classify_service(
            has_fresh_live_data=True, delays_seconds=delays, headway=empty, thresholds=THRESHOLDS
        )

    assert status([20, 40, 400]) == "MINOR_DELAY"  # worst trip 400 s, average 153 s
    assert status([0, 30, 60, 90]) == "ON_TIME"  # one trip late, average 45 s
    assert status([400, 420, 380]) == "MAJOR_DELAY"
    assert status([-120, -90]) == "EARLY"
    # Spacing faults still outrank any delay figure.
    bunched = calculate_headways(
        [datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc), datetime(2026, 9, 7, 12, 2, tzinfo=timezone.utc)],
        THRESHOLDS,
        expected_headway_seconds=600,
    )
    assert classify_service(
        has_fresh_live_data=True, delays_seconds=[0], headway=bunched, thresholds=THRESHOLDS
    ) == "BUNCHING"


def test_only_exceptions_need_attention() -> None:
    assert ATTENTION_STATES == {"SERVICE_GAP", "BUNCHING", "MAJOR_DELAY", "EARLY"}
    assert "MINOR_DELAY" not in ATTENTION_STATES
