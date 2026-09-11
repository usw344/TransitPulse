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


@dataclass(frozen=True)
class OperationsThresholds:
    on_time_seconds: int
    major_delay_seconds: int
    bunching_ratio: float
    gap_ratio: float


@dataclass(frozen=True)
class HeadwayAnalysis:
    headways_seconds: tuple[int, ...]
    baseline_seconds: float | None
    bunching: bool
    service_gap: bool


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
    arrivals: Iterable[datetime], thresholds: OperationsThresholds, *, expected_headway_seconds: float | None = None
) -> HeadwayAnalysis:
    """Compare adjacent arrivals at one stop to schedule or observed baseline."""

    ordered = sorted(set(arrivals))
    gaps = tuple(
        max(0, round((following - preceding).total_seconds()))
        for preceding, following in zip(ordered, ordered[1:])
    )
    baseline = expected_headway_seconds or (float(median(gaps)) if gaps else None)
    if baseline is None or baseline <= 0:
        return HeadwayAnalysis(gaps, baseline, False, False)
    return HeadwayAnalysis(
        headways_seconds=gaps,
        baseline_seconds=baseline,
        bunching=any(gap < baseline * thresholds.bunching_ratio for gap in gaps),
        service_gap=any(gap > baseline * thresholds.gap_ratio for gap in gaps),
    )


def classify_service(
    *,
    has_fresh_live_data: bool,
    delays_seconds: Iterable[int],
    headway: HeadwayAnalysis,
    thresholds: OperationsThresholds,
) -> ServiceState:
    """Prioritize potentially rider-visible spacing faults over a mean delay."""

    if not has_fresh_live_data:
        return "NO_LIVE_DATA"
    if headway.bunching:
        return "BUNCHING"
    if headway.service_gap:
        return "SERVICE_GAP"
    delays = list(delays_seconds)
    if not delays:
        return "ON_TIME"
    late_delays = [delay for delay in delays if delay > thresholds.on_time_seconds]
    worst = max(late_delays) if late_delays else min(delays)
    return classify_delay(worst, thresholds)
