"""Small deterministic, segment-level discrete-event transit simulator.

The simulator is intentionally not a passenger-demand model.  It advances a
single vehicle through an ordered sequence using a supplied prediction function
and records the configuration/seed needed to reproduce a scenario.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from collections import defaultdict
from statistics import median
from typing import Protocol, Sequence


@dataclass(frozen=True)
class Segment:
    """One directed stop-to-stop traversal in an ordered vehicle run."""

    from_stop_sequence: int
    to_stop_sequence: int
    route_gtfs_id: str
    from_stop_id: str
    to_stop_id: str
    scheduled_seconds: float


class TravelTimePredictor(Protocol):
    """Prediction boundary used by the simulator; implementations must be causal."""

    def predict_seconds(self, segment: Segment, prediction_at: datetime) -> float: ...


class TrainSegmentHourMedianPredictor:
    """Causal segment/hour median baseline fitted only from supplied train rows."""

    def __init__(self, train_rows: Sequence[dict[str, object]]) -> None:
        segment_values: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        hour_values: dict[tuple[str, str, str, int], list[float]] = defaultdict(list)
        for row in train_rows:
            key = (str(row["route_gtfs_id"]), str(row["from_stop_id"]), str(row["to_stop_id"]))
            value = float(row["target_travel_seconds"])
            segment_values[key].append(value)
            hour_values[(*key, int(row["hour"]))].append(value)
        if not segment_values:
            raise ValueError("at least one train row is required")
        self.global_median = median(value for values in segment_values.values() for value in values)
        self.segment = {key: median(values) for key, values in segment_values.items()}
        self.segment_hour = {key: median(values) for key, values in hour_values.items()}

    def predict_seconds(self, segment: Segment, prediction_at: datetime) -> float:
        key = (segment.route_gtfs_id, segment.from_stop_id, segment.to_stop_id)
        return self.segment_hour.get((*key, prediction_at.hour), self.segment.get(key, self.global_median))


class TrainSegmentMedianPredictor:
    """Exact causal M4 ``segment_median`` baseline fitted only on train rows."""

    def __init__(self, train_rows: Sequence[dict[str, object]]) -> None:
        values_by_segment: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        all_values: list[float] = []
        for row in train_rows:
            key = (str(row["route_gtfs_id"]), str(row["from_stop_id"]), str(row["to_stop_id"]))
            value = float(row["target_travel_seconds"])
            values_by_segment[key].append(value)
            all_values.append(value)
        if not all_values:
            raise ValueError("at least one train row is required")
        self.global_median = median(all_values)
        self.segment = {key: median(values) for key, values in values_by_segment.items()}

    def predict_seconds(self, segment: Segment, prediction_at: datetime) -> float:
        del prediction_at  # Segment-only selection deliberately has no clock feature.
        key = (segment.route_gtfs_id, segment.from_stop_id, segment.to_stop_id)
        return self.segment.get(key, self.global_median)


@dataclass(frozen=True)
class ScenarioConfig:
    scenario_id: str
    random_seed: int
    arrival_anchor_shift_seconds: float = 0.0
    travel_time_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if not self.scenario_id:
            raise ValueError("scenario_id is required")
        if self.travel_time_multiplier <= 0:
            raise ValueError("travel_time_multiplier must be positive")


@dataclass(frozen=True)
class SegmentEvent:
    from_stop_sequence: int
    to_stop_sequence: int
    from_stop_id: str
    to_stop_id: str
    arrival_at_from_stop: str
    predicted_arrival_at_to_stop: str
    predicted_arrival_to_arrival_seconds: float


@dataclass(frozen=True)
class StopArrivalEvent:
    """One observed/predicted arrival pair from a contiguous diagnostic span.

    These events retain the original run anchor. They may support a narrow
    conditional headway diagnostic, but never a service-level headway forecast:
    each simulated arrival remains anchored to an observed run start.
    """

    service_day: str
    route_gtfs_id: str
    direction_id: int | None
    to_stop_id: str
    to_stop_sequence: int
    run_id: str
    observed_arrival_at: datetime
    predicted_arrival_at: datetime


def _headway_error_metrics(actual: list[float], predicted: list[float]) -> dict[str, float | int]:
    errors = sorted(abs(actual_value - predicted_value) for actual_value, predicted_value in zip(actual, predicted, strict=True))
    return {
        "n": len(errors),
        "mae": sum(errors) / len(errors),
        "median_ae": median(errors),
        "p90_ae": errors[round((len(errors) - 1) * 0.9)],
        "rmse": (sum((actual_value - predicted_value) ** 2 for actual_value, predicted_value in zip(actual, predicted, strict=True)) / len(errors)) ** 0.5,
    }


def summarize_anchored_stop_headways(events: Sequence[StopArrivalEvent]) -> dict[str, object]:
    """Compare adjacent arrivals only within an observed, held-out stop group.

    This is deliberately narrower than a headway/gap/bunching simulation. It
    compares arrival spacing from independent observed runs after each run has
    been anchored at its own first observed stop. It has no block, layover,
    fleet, or schedule-departure model and therefore cannot pass Gate 5.
    """

    groups: dict[tuple[str, str, int | None, str, int], list[StopArrivalEvent]] = defaultdict(list)
    for event in events:
        groups[(event.service_day, event.route_gtfs_id, event.direction_id, event.to_stop_id, event.to_stop_sequence)].append(event)

    actual_headways: list[float] = []
    predicted_headways: list[float] = []
    skipped_same_run_pairs = 0
    for arrivals in groups.values():
        ordered = sorted(arrivals, key=lambda item: (item.observed_arrival_at, item.run_id))
        for previous, current in zip(ordered, ordered[1:]):
            if previous.run_id == current.run_id:
                skipped_same_run_pairs += 1
                continue
            actual_headway = (current.observed_arrival_at - previous.observed_arrival_at).total_seconds()
            if actual_headway <= 0:
                continue
            actual_headways.append(actual_headway)
            predicted_headways.append((current.predicted_arrival_at - previous.predicted_arrival_at).total_seconds())

    if not actual_headways:
        return {
            "status": "INSUFFICIENT_PARTIAL_EVIDENCE",
            "eligible_arrival_events": len(events),
            "eligible_adjacent_pairs": 0,
            "skipped_same_run_pairs": skipped_same_run_pairs,
            "reason": "No adjacent arrivals from distinct observed runs share a held-out stop group.",
        }
    return {
        "status": "PARTIAL_ANCHORED_DIAGNOSTIC_ONLY",
        "eligible_arrival_events": len(events),
        "eligible_adjacent_pairs": len(actual_headways),
        "skipped_same_run_pairs": skipped_same_run_pairs,
        "headway_seconds": _headway_error_metrics(actual_headways, predicted_headways),
        "interpretation": "Conditional stop-arrival spacing after every simulated run is anchored to its own observed start; not a schedule, fleet, gap, or bunching validation.",
        "reason": "Complete terminal paths, dwell/departure, layover, fleet/block, and multi-day validation remain required for Gate 5.",
    }


@dataclass(frozen=True)
class SimulationResult:
    scenario: ScenarioConfig
    route_gtfs_id: str
    span_start_arrival_at: str
    span_end_arrival_at: str
    events: list[SegmentEvent]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def simulate_run(
    *,
    segments: Sequence[Segment],
    predictor: TravelTimePredictor,
    run_start_at: datetime,
    scenario: ScenarioConfig,
) -> SimulationResult:
    """Advance one vehicle through ordered segments without mutating inputs."""
    if not segments:
        raise ValueError("at least one segment is required")
    if any(segment.route_gtfs_id != segments[0].route_gtfs_id for segment in segments):
        raise ValueError("all simulated segments must share a route")
    if any(current.from_stop_sequence <= previous.from_stop_sequence for previous, current in zip(segments, segments[1:])):
        raise ValueError("segment sequences must be strictly increasing")
    if any(
        current.from_stop_sequence != previous.to_stop_sequence or current.from_stop_id != previous.to_stop_id
        for previous, current in zip(segments, segments[1:])
    ):
        raise ValueError("simulated segments must form a contiguous stop path")

    cursor = run_start_at + timedelta(seconds=scenario.arrival_anchor_shift_seconds)
    events: list[SegmentEvent] = []
    for index, segment in enumerate(segments):
        raw_prediction = predictor.predict_seconds(segment, cursor)
        if raw_prediction <= 0:
            raise ValueError("predictor must return a positive travel time")
        travel_seconds = raw_prediction * scenario.travel_time_multiplier
        arrival = cursor + timedelta(seconds=travel_seconds)
        events.append(
            SegmentEvent(
                from_stop_sequence=segment.from_stop_sequence,
                to_stop_sequence=segment.to_stop_sequence,
                from_stop_id=segment.from_stop_id,
                to_stop_id=segment.to_stop_id,
                arrival_at_from_stop=cursor.isoformat(),
                predicted_arrival_at_to_stop=arrival.isoformat(),
                predicted_arrival_to_arrival_seconds=travel_seconds,
            )
        )
        cursor = arrival
    return SimulationResult(
        scenario=scenario,
        route_gtfs_id=segments[0].route_gtfs_id,
        span_start_arrival_at=(run_start_at + timedelta(seconds=scenario.arrival_anchor_shift_seconds)).isoformat(),
        span_end_arrival_at=cursor.isoformat(),
        events=events,
    )
