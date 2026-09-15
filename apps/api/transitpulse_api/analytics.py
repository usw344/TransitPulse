"""Deterministic, source-labelled reliability calculations.

The recorder captures vehicle states, not a certified arrival/departure event
stream.  The helpers in this module therefore derive *stop-sequence entry*
events only when a vehicle changes trip/sequence.  Callers must keep that
distinction visible to riders and operators rather than presenting it as a
measured platform arrival.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from statistics import median, pstdev
from typing import Iterable

from transitpulse_api.operations import OperationsThresholds, calculate_headways


@dataclass(frozen=True)
class RecordedPoint:
    """The small immutable subset of a recorded state used by analytics."""

    at: datetime
    vehicle_id: str
    trip_id: str | None
    current_stop_sequence: int | None
    delay_seconds: int | None


@dataclass(frozen=True)
class ObservedStopEvent:
    """A vehicle entering a new GTFS stop sequence in the selected range."""

    at: datetime
    vehicle_id: str
    trip_id: str | None
    stop_sequence: int
    delay_seconds: int | None


@dataclass(frozen=True)
class DelayDistribution:
    sample_count: int
    median_seconds: int | None
    percentile_10_seconds: int | None
    percentile_90_seconds: int | None
    minimum_seconds: int | None
    maximum_seconds: int | None


@dataclass(frozen=True)
class HeadwayMetrics:
    sample_count: int
    median_seconds: int | None
    baseline_seconds: int | None
    bunching_event_count: int
    service_gap_event_count: int
    variability_seconds: int | None


@dataclass(frozen=True)
class DelayBands:
    sample_count: int
    early_count: int
    on_time_count: int
    late_under_5_count: int
    late_5_to_10_count: int
    late_over_10_count: int


@dataclass(frozen=True)
class DelayBucket:
    start: datetime
    end: datetime
    observation_count: int
    delay_observation_count: int
    median_delay_seconds: int | None
    late_observation_count: int


def percentile(values: Iterable[int], fraction: float) -> int | None:
    """Return a linearly interpolated percentile, rounded to whole seconds."""

    ordered = sorted(values)
    if not ordered:
        return None
    if not 0 <= fraction <= 1:
        raise ValueError("percentile fraction must be between zero and one")
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    remainder = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * remainder)


def delay_distribution(delays_seconds: Iterable[int | None]) -> DelayDistribution:
    values = [value for value in delays_seconds if value is not None]
    return DelayDistribution(
        sample_count=len(values),
        median_seconds=round(median(values)) if values else None,
        percentile_10_seconds=percentile(values, 0.10),
        percentile_90_seconds=percentile(values, 0.90),
        minimum_seconds=min(values) if values else None,
        maximum_seconds=max(values) if values else None,
    )


def delay_bands(
    delays_seconds: Iterable[int | None],
    *,
    on_time_seconds: int,
    major_delay_seconds: int,
    severe_delay_seconds: int,
) -> DelayBands:
    """Classify direct publisher delays using centralized product thresholds."""

    if not 0 <= on_time_seconds < major_delay_seconds < severe_delay_seconds:
        raise ValueError("delay thresholds must be strictly increasing")
    values = [value for value in delays_seconds if value is not None]
    return DelayBands(
        sample_count=len(values),
        early_count=sum(value < -on_time_seconds for value in values),
        on_time_count=sum(-on_time_seconds <= value <= on_time_seconds for value in values),
        late_under_5_count=sum(on_time_seconds < value <= major_delay_seconds for value in values),
        late_5_to_10_count=sum(major_delay_seconds < value <= severe_delay_seconds for value in values),
        late_over_10_count=sum(value > severe_delay_seconds for value in values),
    )


def observed_stop_sequence_events(points: Iterable[RecordedPoint]) -> list[ObservedStopEvent]:
    """Collapse repeated snapshots into observable vehicle sequence changes.

    A vehicle serves each stop of a trip once, so each (vehicle, trip, stop
    sequence) yields at most one arrival.  Comparing against the immediately
    previous snapshot alone is not enough: a feed that reports 40 -> 41 -> 40
    -> 41 emits an arrival each time, inflating the event count and deflating
    every headway derived from it.  Repeat visits to an already-observed stop
    on the same trip are therefore treated as feed jitter and dropped, while a
    later trip legitimately revisits the same sequence under a new trip id.
    """

    last_sequence: dict[str, tuple[str | None, int]] = {}
    observed_stops: set[tuple[str, str | None, int]] = set()
    events: list[ObservedStopEvent] = []
    for point in sorted(points, key=lambda item: (item.at, item.vehicle_id)):
        if point.current_stop_sequence is None:
            continue
        state = (point.trip_id, point.current_stop_sequence)
        previous = last_sequence.get(point.vehicle_id)
        if previous is None:
            # A window boundary only tells us where a vehicle already was; it
            # is not evidence that it entered that stop at this timestamp.
            last_sequence[point.vehicle_id] = state
            observed_stops.add((point.vehicle_id, point.trip_id, point.current_stop_sequence))
            continue
        if previous == state:
            continue
        last_sequence[point.vehicle_id] = state
        arrival_key = (point.vehicle_id, point.trip_id, point.current_stop_sequence)
        if arrival_key in observed_stops:
            continue
        observed_stops.add(arrival_key)
        events.append(
            ObservedStopEvent(
                at=point.at,
                vehicle_id=point.vehicle_id,
                trip_id=point.trip_id,
                stop_sequence=point.current_stop_sequence,
                delay_seconds=point.delay_seconds,
            )
        )
    return events


def headway_metrics(
    arrivals: Iterable[datetime],
    thresholds: OperationsThresholds,
    *,
    scheduled_baseline_seconds: int | None = None,
) -> HeadwayMetrics:
    """Summarize comparable events against schedule when it is available."""

    analysis = calculate_headways(
        arrivals,
        thresholds,
        expected_headway_seconds=scheduled_baseline_seconds,
    )
    baseline = analysis.baseline_seconds
    if baseline is None or baseline <= 0:
        bunching = 0
        gaps = 0
    else:
        bunching = sum(
            value < baseline * thresholds.bunching_ratio
            for value in analysis.headways_seconds
        )
        gaps = sum(
            value > baseline * thresholds.gap_ratio
            for value in analysis.headways_seconds
        )
    return HeadwayMetrics(
        sample_count=len(analysis.headways_seconds),
        median_seconds=round(median(analysis.headways_seconds)) if analysis.headways_seconds else None,
        baseline_seconds=round(baseline) if baseline else None,
        bunching_event_count=bunching,
        service_gap_event_count=gaps,
        variability_seconds=(
            round(pstdev(analysis.headways_seconds))
            if len(analysis.headways_seconds) >= 2
            else None
        ),
    )


def delay_buckets(
    points: Iterable[RecordedPoint],
    *,
    start: datetime,
    end: datetime,
    on_time_seconds: int,
    maximum_buckets: int = 12,
) -> list[DelayBucket]:
    """Make a compact, deterministic timeline from direct recorded delays."""

    if start >= end:
        raise ValueError("bucket range must increase")
    duration_seconds = (end - start).total_seconds()
    bucket_count = min(maximum_buckets, max(1, ceil(duration_seconds / 3600)))
    bucket_seconds = duration_seconds / bucket_count
    grouped: list[list[RecordedPoint]] = [[] for _ in range(bucket_count)]
    for point in points:
        if point.at < start or point.at >= end:
            continue
        index = min(bucket_count - 1, int((point.at - start).total_seconds() / bucket_seconds))
        grouped[index].append(point)

    buckets: list[DelayBucket] = []
    for index, members in enumerate(grouped):
        bucket_start = start + timedelta(seconds=bucket_seconds * index)
        bucket_end = end if index == bucket_count - 1 else start + timedelta(seconds=bucket_seconds * (index + 1))
        delays = [member.delay_seconds for member in members if member.delay_seconds is not None]
        buckets.append(
            DelayBucket(
                start=bucket_start,
                end=bucket_end,
                observation_count=len(members),
                delay_observation_count=len(delays),
                median_delay_seconds=round(median(delays)) if delays else None,
                late_observation_count=sum(abs(delay) > on_time_seconds for delay in delays),
            )
        )
    return buckets
