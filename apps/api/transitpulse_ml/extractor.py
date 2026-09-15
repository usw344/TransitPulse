"""Bounded, read-only GTFS-Realtime segment-label extraction.

The extractor deliberately separates streamed raw observations from the small
static context loaded for each feed/trip.  It never updates source tables and
refuses uncertain trip, calendar, loop, shape, or trajectory evidence.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
import json
from itertools import groupby
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from transitpulse_ml.segments import (
    MatchedObservation,
    SegmentTraversal,
    TripStop,
    extract_segment_traversals,
    has_ambiguous_projection,
    project_onto_polyline,
)
from transitpulse_ml.service_day import ServiceSchedule, resolve_service_day


@dataclass(frozen=True)
class ExtractionConfig:
    start_at: datetime
    end_at: datetime
    timezone_name: str | None = None
    route_ids: tuple[str, ...] = ()
    max_source_rows: int = 100_000
    max_group_observations: int = 20_000
    max_lateral_error_m: float = 75.0
    max_observation_gap_seconds: float = 120.0
    max_scheduled_segment_seconds: int = 1_800
    include_terminal_segments: bool = False

    def __post_init__(self) -> None:
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("extraction bounds must be timezone-aware")
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        if self.max_source_rows < 1 or self.max_group_observations < 2:
            raise ValueError("source and group limits must be positive")


@dataclass(frozen=True)
class RawObservation:
    static_feed_id: UUID
    trip_gtfs_id: str
    route_gtfs_id: str | None
    vehicle_id: str
    observed_at: datetime
    recorded_at: datetime
    longitude: float
    latitude: float
    current_stop_sequence: int


@dataclass(frozen=True)
class StaticTripContext:
    feed_id: UUID
    trip_gtfs_id: str
    route_gtfs_id: str
    direction_id: int | None
    shape: tuple[tuple[float, float], ...]
    stops: tuple[TripStop, ...]
    schedule: ServiceSchedule
    scheduled_start_seconds: int
    timezone_name: str | None
    terminal_stop_sequences: frozenset[int]
    rejected_reason: str | None = None


@dataclass(frozen=True)
class SegmentLabel:
    static_feed_id: UUID
    service_day: date
    trip_gtfs_id: str
    route_gtfs_id: str
    direction_id: int | None
    vehicle_id: str
    traversal: SegmentTraversal


@dataclass
class ExtractionCensus:
    candidate_observations: int = 0
    candidate_runs: int = 0
    candidate_traversals: int = 0
    accepted_labels: int = 0
    rejected: Counter[str] = field(default_factory=Counter)

    def reject(self, reason: str) -> None:
        self.rejected[reason] += 1


def _row_to_observation(row: Any) -> RawObservation:
    return RawObservation(
        static_feed_id=row.static_feed_id,
        trip_gtfs_id=row.trip_gtfs_id,
        route_gtfs_id=row.route_gtfs_id,
        vehicle_id=row.vehicle_id,
        observed_at=row.observed_at,
        recorded_at=row.recorded_at,
        longitude=float(row.longitude),
        latitude=float(row.latitude),
        current_stop_sequence=int(row.current_stop_sequence),
    )


def iter_observation_groups(
    connection: Connection, config: ExtractionConfig, census: ExtractionCensus
) -> Iterator[tuple[tuple[UUID, str, str], tuple[RawObservation, ...]]]:
    """Yield bounded feed/trip/vehicle groups without loading a window at once."""

    route_clause = ""
    params: dict[str, Any] = {
        "start_at": config.start_at,
        "end_at": config.end_at,
        "limit": config.max_source_rows + 1,
    }
    if config.route_ids:
        route_clause = " AND vo.route_gtfs_id = ANY(:route_ids)"
        params["route_ids"] = list(config.route_ids)
    statement = text(
        """
        SELECT vo.static_feed_id, vo.trip_gtfs_id, vo.route_gtfs_id, vo.vehicle_id,
               vo.observed_at, vo.recorded_at, ST_X(vo.position) AS longitude,
               ST_Y(vo.position) AS latitude, vo.current_stop_sequence
        FROM vehicle_observations AS vo
        WHERE vo.observed_at >= :start_at
          AND vo.observed_at < :end_at
          AND vo.trip_gtfs_id IS NOT NULL
          AND vo.current_stop_sequence IS NOT NULL
          AND vo.position IS NOT NULL
        """
        + route_clause
        + """
        ORDER BY vo.static_feed_id, vo.trip_gtfs_id, vo.vehicle_id,
                 vo.observed_at, vo.recorded_at, vo.id
        LIMIT :limit
        """
    )
    result = connection.execution_options(stream_results=True, yield_per=2_000).execute(statement, params)
    rows = (_row_to_observation(row) for row in result)
    for key, grouped in groupby(rows, key=lambda item: (item.static_feed_id, item.trip_gtfs_id, item.vehicle_id)):
        group = tuple(grouped)
        census.candidate_observations += len(group)
        if census.candidate_observations > config.max_source_rows:
            raise ValueError("source_row_limit_reached: narrow the extraction window or route scope")
        if len(group) > config.max_group_observations:
            census.reject("group_observation_limit_reached")
            continue
        census.candidate_runs += 1
        yield key, group


def _load_static_context(
    connection: Connection, *, feed_id: UUID, trip_gtfs_id: str
) -> StaticTripContext | None:
    rows = connection.execute(
        text(
            """
            SELECT t.gtfs_trip_id, r.gtfs_route_id, t.direction_id,
                   ST_AsGeoJSON(sh.geometry) AS shape_geojson,
                   st.stop_sequence, st.arrival_seconds, st.departure_seconds,
                   s.gtfs_stop_id, ST_X(s.location) AS longitude, ST_Y(s.location) AS latitude,
                   st.pickup_type, st.drop_off_type, a.timezone AS agency_timezone,
                   sc.monday, sc.tuesday, sc.wednesday, sc.thursday, sc.friday,
                   sc.saturday, sc.sunday, sc.start_date, sc.end_date, sc.id AS service_id
            FROM trips AS t
            JOIN routes AS r ON r.id = t.route_id
            JOIN agencies AS a ON a.id = r.agency_id
            LEFT JOIN shapes AS sh ON sh.id = t.shape_id
            JOIN stop_times AS st ON st.trip_id = t.id
            JOIN stops AS s ON s.id = st.stop_id
            JOIN service_calendars AS sc ON sc.id = t.service_id
            WHERE t.feed_id = :feed_id AND t.gtfs_trip_id = :trip_gtfs_id
            ORDER BY st.stop_sequence
            """
        ),
        {"feed_id": feed_id, "trip_gtfs_id": trip_gtfs_id},
    ).mappings().all()
    if not rows:
        return None
    first = rows[0]
    if first["shape_geojson"] is None:
        return StaticTripContext(
            feed_id, trip_gtfs_id, first["gtfs_route_id"], first["direction_id"], (), (),
            ServiceSchedule(frozenset(), None, None, {}), 0, first["agency_timezone"], frozenset(), "missing_trip_shape"
        )
    shape = tuple(tuple(point) for point in json.loads(first["shape_geojson"])["coordinates"])
    if len(shape) < 2:
        return StaticTripContext(
            feed_id, trip_gtfs_id, first["gtfs_route_id"], first["direction_id"], (), (),
            ServiceSchedule(frozenset(), None, None, {}), 0, first["agency_timezone"], frozenset(), "invalid_trip_shape"
        )
    stops: list[TripStop] = []
    for row in rows:
        if row["longitude"] is None or row["latitude"] is None:
            return StaticTripContext(
                feed_id, trip_gtfs_id, first["gtfs_route_id"], first["direction_id"], shape, (),
                ServiceSchedule(frozenset(), None, None, {}), 0, first["agency_timezone"], frozenset(), "stop_without_coordinates"
            )
        point = (float(row["longitude"]), float(row["latitude"]))
        if has_ambiguous_projection(point, shape):
            return StaticTripContext(
                feed_id, trip_gtfs_id, first["gtfs_route_id"], first["direction_id"], shape, (),
                ServiceSchedule(frozenset(), None, None, {}), 0, first["agency_timezone"], frozenset(), "ambiguous_stop_shape_projection"
            )
        if row["arrival_seconds"] is None or row["departure_seconds"] is None:
            return StaticTripContext(
                feed_id, trip_gtfs_id, first["gtfs_route_id"], first["direction_id"], shape, (),
                ServiceSchedule(frozenset(), None, None, {}), 0, first["agency_timezone"], frozenset(), "missing_stop_schedule"
            )
        match = project_onto_polyline(point, shape)
        stops.append(
            TripStop(
                row["gtfs_stop_id"], int(row["stop_sequence"]), match.progress_m,
                int(row["arrival_seconds"]), int(row["departure_seconds"]),
            )
        )
    service_id = first["service_id"]
    exceptions = {
        row.date: int(row.exception_type)
        for row in connection.execute(
            text("SELECT date, exception_type FROM calendar_dates WHERE service_id = :service_id"),
            {"service_id": service_id},
        ).mappings()
    }
    weekdays = frozenset(
        day for day, name in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))
        if first[name]
    )
    schedule = ServiceSchedule(weekdays, first["start_date"], first["end_date"], exceptions)
    terminal_sequences = frozenset(
        int(row["stop_sequence"])
        for row in rows
        if row["pickup_type"] == 1 or row["drop_off_type"] == 1
    )
    return StaticTripContext(
        feed_id, trip_gtfs_id, first["gtfs_route_id"], first["direction_id"], shape,
        tuple(stops), schedule, min(stop.departure_seconds for stop in stops),
        first["agency_timezone"], terminal_sequences, None,
    )


def _labels_for_run(
    context: StaticTripContext,
    observations: Iterable[RawObservation],
    config: ExtractionConfig,
    census: ExtractionCensus,
) -> list[SegmentLabel]:
    if context.rejected_reason:
        census.reject(context.rejected_reason)
        return []
    by_service_day: dict[date, list[RawObservation]] = {}
    for observation in observations:
        if observation.route_gtfs_id != context.route_gtfs_id:
            census.reject("realtime_static_route_mismatch")
            return []
        timezone_name = config.timezone_name or context.timezone_name
        if not timezone_name:
            census.reject("missing_feed_timezone")
            return []
        if has_ambiguous_projection((observation.longitude, observation.latitude), context.shape):
            census.reject("ambiguous_vehicle_shape_projection")
            return []
        service_day = resolve_service_day(
            observation.observed_at,
            scheduled_start_seconds=context.scheduled_start_seconds,
            timezone_name=timezone_name,
            is_active=context.schedule.is_active_on,
        )
        if service_day is None:
            census.reject("unresolved_or_inactive_service_day")
            continue
        by_service_day.setdefault(service_day, []).append(observation)
    labels: list[SegmentLabel] = []
    for service_day, run in by_service_day.items():
        matched = [
            MatchedObservation(
                observed_at=item.observed_at,
                recorded_at=item.recorded_at,
                current_stop_sequence=item.current_stop_sequence,
                progress_m=project_onto_polyline((item.longitude, item.latitude), context.shape).progress_m,
                lateral_error_m=project_onto_polyline((item.longitude, item.latitude), context.shape).lateral_error_m,
            )
            for item in run
        ]
        result = extract_segment_traversals(
            context.stops,
            matched,
            max_lateral_error_m=config.max_lateral_error_m,
            max_observation_gap_seconds=config.max_observation_gap_seconds,
        )
        if result.rejected_reason:
            census.reject(result.rejected_reason)
            continue
        census.candidate_traversals += len(result.traversals)
        for traversal in result.traversals:
            if not config.include_terminal_segments and (
                traversal.from_stop_sequence in context.terminal_stop_sequences
                or traversal.to_stop_sequence in context.terminal_stop_sequences
            ):
                census.reject("terminal_or_layover_segment")
                continue
            if traversal.scheduled_seconds > config.max_scheduled_segment_seconds:
                census.reject("terminal_or_scheduled_gap_segment")
                continue
            labels.append(
                SegmentLabel(
                    context.feed_id, service_day, context.trip_gtfs_id, context.route_gtfs_id,
                    context.direction_id, run[0].vehicle_id, traversal,
                )
            )
    census.accepted_labels += len(labels)
    return labels


def extract_labels(engine: Engine, config: ExtractionConfig) -> tuple[list[SegmentLabel], ExtractionCensus]:
    """Extract labels with an explicit read-only transaction and bounded input."""

    census = ExtractionCensus()
    labels: list[SegmentLabel] = []
    context_cache: dict[tuple[UUID, str], StaticTripContext | None] = {}
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        for (feed_id, trip_gtfs_id, _vehicle_id), observations in iter_observation_groups(connection, config, census):
            cache_key = (feed_id, trip_gtfs_id)
            context = context_cache.get(cache_key)
            if context is None and cache_key not in context_cache:
                context = _load_static_context(connection, feed_id=feed_id, trip_gtfs_id=trip_gtfs_id)
                context_cache[cache_key] = context
            if context is None:
                census.reject("missing_static_trip")
                continue
            labels.extend(_labels_for_run(context, observations, config, census))
        connection.rollback()
    return labels, census
