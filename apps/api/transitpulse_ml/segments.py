"""Deterministic geometry and label primitives for directed transit segments.

This module is intentionally independent of SQL and model libraries so its
failure behavior can be exercised with synthetic trajectories before any
large dataset query is allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import cos, radians, sqrt
from typing import Iterable, Sequence


EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class ShapeMatch:
    progress_m: float
    lateral_error_m: float


@dataclass(frozen=True)
class TripStop:
    stop_id: str
    stop_sequence: int
    progress_m: float
    arrival_seconds: int
    departure_seconds: int


@dataclass(frozen=True)
class MatchedObservation:
    observed_at: datetime
    recorded_at: datetime
    current_stop_sequence: int
    progress_m: float
    lateral_error_m: float


@dataclass(frozen=True)
class SegmentTraversal:
    from_stop_id: str
    to_stop_id: str
    from_stop_sequence: int
    to_stop_sequence: int
    observed_start: datetime
    observed_end: datetime
    outcome_available_at: datetime
    travel_seconds: float
    scheduled_seconds: int
    distance_m: float
    max_lateral_error_m: float
    max_bracketing_gap_seconds: float


@dataclass(frozen=True)
class ExtractionResult:
    traversals: tuple[SegmentTraversal, ...]
    rejected_reason: str | None = None


def _polyline_matches(
    point: tuple[float, float], shape: Sequence[tuple[float, float]]
) -> list[ShapeMatch]:
    """Return a projection for every nonzero segment of a lon/lat polyline."""

    if len(shape) < 2:
        raise ValueError("shape must contain at least two points")
    origin_lat = sum(lat for _, lat in shape) / len(shape)
    target_x, target_y = _local_xy(point, origin_lat)
    xy = [_local_xy(item, origin_lat) for item in shape]
    cumulative = 0.0
    matches: list[ShapeMatch] = []
    for start, end in zip(xy[:-1], xy[1:], strict=True):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length_squared = dx * dx + dy * dy
        if length_squared == 0:
            continue
        fraction = max(
            0.0,
            min(
                1.0,
                ((target_x - start[0]) * dx + (target_y - start[1]) * dy)
                / length_squared,
            ),
        )
        projected_x = start[0] + fraction * dx
        projected_y = start[1] + fraction * dy
        length = sqrt(length_squared)
        matches.append(
            ShapeMatch(
                progress_m=cumulative + fraction * length,
                lateral_error_m=sqrt((target_x - projected_x) ** 2 + (target_y - projected_y) ** 2),
            )
        )
        cumulative += length
    if not matches:
        raise ValueError("shape has no nonzero-length edge")
    return matches


def _local_xy(point: tuple[float, float], origin_lat: float) -> tuple[float, float]:
    lon, lat = point
    return (
        radians(lon) * EARTH_RADIUS_M * cos(radians(origin_lat)),
        radians(lat) * EARTH_RADIUS_M,
    )


def project_onto_polyline(
    point: tuple[float, float], shape: Sequence[tuple[float, float]]
) -> ShapeMatch:
    """Return distance along a lon/lat polyline and lateral error in metres."""

    return min(_polyline_matches(point, shape), key=lambda match: match.lateral_error_m)


def has_ambiguous_projection(
    point: tuple[float, float],
    shape: Sequence[tuple[float, float]],
    *,
    max_error_delta_m: float = 8.0,
    min_progress_separation_m: float = 100.0,
) -> bool:
    """Whether two materially separate shape positions fit a point equally well."""

    if max_error_delta_m < 0 or min_progress_separation_m < 0:
        raise ValueError("ambiguity thresholds must be non-negative")
    matches = sorted(_polyline_matches(point, shape), key=lambda match: match.lateral_error_m)
    best = matches[0]
    return any(
        candidate.lateral_error_m <= best.lateral_error_m + max_error_delta_m
        and abs(candidate.progress_m - best.progress_m) >= min_progress_separation_m
        for candidate in matches[1:]
    )


def _interpolated_crossing(
    observations: Sequence[MatchedObservation], target_progress_m: float
) -> tuple[datetime, datetime, float, float] | None:
    for before, after in zip(observations[:-1], observations[1:], strict=True):
        if before.progress_m <= target_progress_m <= after.progress_m:
            distance = after.progress_m - before.progress_m
            gap_seconds = (after.observed_at - before.observed_at).total_seconds()
            if distance == 0 or gap_seconds <= 0:
                continue
            fraction = (target_progress_m - before.progress_m) / distance
            crossing = before.observed_at + (after.observed_at - before.observed_at) * fraction
            return (
                crossing,
                max(after.observed_at, after.recorded_at),
                gap_seconds,
                max(before.lateral_error_m, after.lateral_error_m),
            )
    return None


def extract_segment_traversals(
    stops: Sequence[TripStop],
    observations: Iterable[MatchedObservation],
    *,
    max_lateral_error_m: float = 75.0,
    max_observation_gap_seconds: float = 120.0,
    max_regression_m: float = 35.0,
    max_speed_mps: float = 40.0,
) -> ExtractionResult:
    """Interpolate arrival crossings and return defensible segment labels.

    A run is rejected as a unit when its provenance/progression is internally
    inconsistent. Individual segments without tight crossing brackets are
    omitted rather than fabricated.
    """

    if len(stops) < 2:
        return ExtractionResult((), "fewer_than_two_stops")
    if any(right.stop_sequence <= left.stop_sequence for left, right in zip(stops[:-1], stops[1:], strict=True)):
        return ExtractionResult((), "nonincreasing_static_stop_sequence")
    if any(right.progress_m <= left.progress_m for left, right in zip(stops[:-1], stops[1:], strict=True)):
        return ExtractionResult((), "nonmonotonic_stop_shape_projection")

    valid_sequences = {stop.stop_sequence for stop in stops}
    ordered = sorted(observations, key=lambda item: (item.observed_at, item.recorded_at))
    deduplicated: list[MatchedObservation] = []
    for observation in ordered:
        if observation.current_stop_sequence not in valid_sequences:
            return ExtractionResult((), "unmatched_trip_stop_sequence")
        if observation.lateral_error_m > max_lateral_error_m:
            continue
        if deduplicated and observation.observed_at == deduplicated[-1].observed_at:
            # Later recorder capture wins deterministically for one source timestamp.
            deduplicated[-1] = observation
        else:
            deduplicated.append(observation)
    if len(deduplicated) < 2:
        return ExtractionResult((), "insufficient_map_matched_observations")

    for before, after in zip(deduplicated[:-1], deduplicated[1:], strict=True):
        gap = (after.observed_at - before.observed_at).total_seconds()
        if gap <= 0:
            return ExtractionResult((), "nonpositive_observation_interval")
        if before.progress_m - after.progress_m > max_regression_m:
            return ExtractionResult((), "nonmonotonic_vehicle_progress")
        if after.current_stop_sequence < before.current_stop_sequence:
            return ExtractionResult((), "regressing_vehicle_stop_sequence")

    crossings = [_interpolated_crossing(deduplicated, stop.progress_m) for stop in stops]
    traversals: list[SegmentTraversal] = []
    for left_stop, right_stop, left_crossing, right_crossing in zip(
        stops[:-1], stops[1:], crossings[:-1], crossings[1:], strict=True
    ):
        if left_crossing is None or right_crossing is None:
            continue
        start, left_available_at, left_gap, left_error = left_crossing
        end, right_available_at, right_gap, right_error = right_crossing
        travel_seconds = (end - start).total_seconds()
        distance_m = right_stop.progress_m - left_stop.progress_m
        bracketing_gap = max(left_gap, right_gap)
        if travel_seconds <= 0 or bracketing_gap > max_observation_gap_seconds:
            continue
        if distance_m / travel_seconds > max_speed_mps:
            continue
        scheduled_seconds = right_stop.arrival_seconds - left_stop.arrival_seconds
        if scheduled_seconds <= 0:
            continue
        traversals.append(
            SegmentTraversal(
                from_stop_id=left_stop.stop_id,
                to_stop_id=right_stop.stop_id,
                from_stop_sequence=left_stop.stop_sequence,
                to_stop_sequence=right_stop.stop_sequence,
                observed_start=start,
                observed_end=end,
                outcome_available_at=max(left_available_at, right_available_at),
                travel_seconds=travel_seconds,
                scheduled_seconds=scheduled_seconds,
                distance_m=distance_m,
                max_lateral_error_m=max(left_error, right_error),
                max_bracketing_gap_seconds=bracketing_gap,
            )
        )
    return ExtractionResult(tuple(traversals))
