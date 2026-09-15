"""Small, explainable route-operations calculations.

Thresholds are configuration rather than asserted industry standards.  The
headway helper only evaluates comparable predicted arrivals (same stop), so it
does not manufacture a route-level travel metric from unrelated vehicle points.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Iterable, Literal


ServiceState = Literal[
    "ON_TIME", "EARLY", "MINOR_DELAY", "MAJOR_DELAY", "BUNCHING", "SERVICE_GAP", "NO_LIVE_DATA"
]

#: States that put a route in the network "needs attention" queue. A route whose
#: current trips average 1-5 minutes late is ordinary operations for this network,
#: not an exception; listing it made nine routes in ten "need attention".
ATTENTION_STATES: frozenset[str] = frozenset({"SERVICE_GAP", "BUNCHING", "MAJOR_DELAY", "EARLY"})


@dataclass(frozen=True)
class OperationsThresholds:
    on_time_seconds: int
    major_delay_seconds: int
    bunching_ratio: float
    gap_ratio: float
    prediction_horizon_seconds: int = 5400


@dataclass(frozen=True)
class HeadwayAnalysis:
    headways_seconds: tuple[int, ...]
    baseline_seconds: float | None
    bunching: bool
    service_gap: bool
    #: Predicted arrivals dropped for being beyond the comparable-service horizon.
    excluded_beyond_horizon: int = 0
    horizon_seconds: int | None = None


def arrivals_within_horizon(
    arrivals: Iterable[datetime],
    *,
    reference: datetime | None,
    horizon_seconds: int,
) -> tuple[tuple[datetime, ...], int]:
    """Keep only predicted arrivals that describe comparable current service.

    GTFS-Realtime trip updates routinely publish an arrival for the next service
    period, and a stale prediction can sit hours in the future.  Feeding those
    into an adjacent-arrival headway makes the end-of-service boundary look like
    a rider-visible service gap, so they are excluded and counted rather than
    silently mixed into current operations.
    """

    ordered = tuple(sorted(set(arrivals)))
    if reference is None or horizon_seconds <= 0:
        return ordered, 0
    kept = tuple(
        arrival
        for arrival in ordered
        if (arrival - reference).total_seconds() <= horizon_seconds
    )
    return kept, len(ordered) - len(kept)


def classify_delay(delay_seconds: int | None, thresholds: OperationsThresholds) -> ServiceState:
    if delay_seconds is None:
        return "NO_LIVE_DATA"
    if abs(delay_seconds) <= thresholds.on_time_seconds:
        return "ON_TIME"
    if delay_seconds < -thresholds.on_time_seconds:
        return "EARLY"
    if delay_seconds < thresholds.major_delay_seconds:
        return "MINOR_DELAY"
    return "MAJOR_DELAY"


def calculate_headways(
    arrivals: Iterable[datetime],
    thresholds: OperationsThresholds,
    *,
    expected_headway_seconds: float | None = None,
    excluded_beyond_horizon: int = 0,
) -> HeadwayAnalysis:
    """Compare adjacent arrivals at one stop to schedule or observed baseline."""

    ordered = sorted(set(arrivals))
    gaps = tuple(
        max(0, round((following - preceding).total_seconds()))
        for preceding, following in zip(ordered, ordered[1:])
    )
    baseline = expected_headway_seconds or (float(median(gaps)) if gaps else None)
    if baseline is None or baseline <= 0:
        return HeadwayAnalysis(
            gaps,
            baseline,
            False,
            False,
            excluded_beyond_horizon=excluded_beyond_horizon,
            horizon_seconds=thresholds.prediction_horizon_seconds,
        )
    return HeadwayAnalysis(
        headways_seconds=gaps,
        baseline_seconds=baseline,
        bunching=any(gap < baseline * thresholds.bunching_ratio for gap in gaps),
        service_gap=any(gap > baseline * thresholds.gap_ratio for gap in gaps),
        excluded_beyond_horizon=excluded_beyond_horizon,
        horizon_seconds=thresholds.prediction_horizon_seconds,
    )


def classify_service(
    *,
    has_fresh_live_data: bool,
    delays_seconds: Iterable[int],
    headway: HeadwayAnalysis,
    thresholds: OperationsThresholds,
) -> ServiceState:
    """Classify a route's current service.

    Rider-visible spacing faults take priority. Otherwise the status describes the
    route's typical current trip: the average delay of its current trips, which is
    the same figure the product shows beside the status. Keying the status to the
    single worst trip labelled a route MAJOR DELAY next to an average of +2 min.
    Individual late trips are still counted and reported as evidence.
    """

    if not has_fresh_live_data:
        return "NO_LIVE_DATA"
    if headway.bunching:
        return "BUNCHING"
    if headway.service_gap:
        return "SERVICE_GAP"
    delays = list(delays_seconds)
    if not delays:
        # No delay sample and no spacing exception is an absence of evidence.
        # Reporting ON_TIME here asserts good service for a route that may have
        # no vehicles running at all.
        return "NO_LIVE_DATA"
    return classify_delay(round(sum(delays) / len(delays)), thresholds)
