"""GTFS-Realtime fetch, normalization, static matching, and current-state persistence.

The City of Edmonton feeds are standard GTFS-Realtime protobuf snapshots.  This
module deliberately has no web-framework dependency so the recorder and API
share the same ingestion path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from urllib.request import Request, urlopen
from uuid import UUID

from geoalchemy2.elements import WKTElement
from google.transit import gtfs_realtime_pb2
from sqlalchemy import select
from sqlalchemy.orm import Session

from transitpulse_api.config import settings
from transitpulse_api.models import (
    GtfsFeed,
    RealtimeAlert,
    RealtimeFeedStatus,
    RealtimeTripObservation,
    RealtimeTripState,
    RealtimeVehicleState,
    Route,
    Trip,
    VehicleObservation,
)


FEED_KINDS = ("vehicle_positions", "trip_updates", "alerts")


class RealtimeFeedError(RuntimeError):
    """A source could not be fetched or decoded as GTFS-Realtime."""


@dataclass(frozen=True)
class ParsedRealtimeFeed:
    source_timestamp: datetime | None
    full_dataset: bool
    entities: tuple[Any, ...]


@dataclass(frozen=True)
class NormalizedVehicle:
    entity_id: str
    vehicle_id: str
    trip_id: str | None
    route_id: str | None
    latitude: float | None
    longitude: float | None
    bearing: float | None
    speed: float | None
    observed_at: datetime | None
    current_stop_sequence: int | None
    current_status: str | None
    schedule_relationship: str | None


@dataclass(frozen=True)
class NormalizedTripUpdate:
    entity_id: str
    trip_id: str
    route_id: str | None
    schedule_relationship: str | None
    delay_seconds: int | None
    next_stop_id: str | None
    next_stop_sequence: int | None
    next_arrival_at: datetime | None
    next_departure_at: datetime | None
    next_arrival_delay_seconds: int | None
    next_departure_delay_seconds: int | None
    observed_at: datetime | None


@dataclass(frozen=True)
class NormalizedAlert:
    entity_id: str
    header: str | None
    description: str | None
    url: str | None
    cause: str | None
    effect: str | None
    active_periods: tuple[dict[str, str | None], ...]
    affected_routes: tuple[str, ...]
    affected_stops: tuple[str, ...]


@dataclass(frozen=True)
class IngestionResult:
    static_feed_id: UUID | None
    feed_counts: dict[str, int]
    recorded_counts: dict[str, int]
    failures: dict[str, str]


@dataclass(frozen=True)
class TripObservationCandidates:
    """Bounded active-vehicle candidates plus explicit no-timing exclusions."""

    items: dict[str, tuple[NormalizedVehicle, NormalizedTripUpdate]]
    skipped_no_timing_vehicle_trips: int


@dataclass(frozen=True)
class TripObservationRecordingResult:
    inserted: int
    skipped_no_timing_vehicle_trips: int


def fetch_realtime_bytes(url: str) -> bytes:
    """Fetch a protobuf snapshot with an explicit identifying User-Agent."""

    request = Request(url, headers={"User-Agent": "TransitPulse/0.4 (GTFS-Realtime)"})
    try:
        with urlopen(request, timeout=settings.realtime_request_timeout_seconds) as response:
            return response.read()
    except OSError as error:
        raise RealtimeFeedError(f"fetch failed: {error.reason if hasattr(error, 'reason') else error}") from error


def parse_realtime_feed(payload: bytes) -> ParsedRealtimeFeed:
    """Parse a GTFS-Realtime protobuf snapshot without assuming optional fields."""

    message = gtfs_realtime_pb2.FeedMessage()
    try:
        message.ParseFromString(payload)
    except Exception as error:  # protobuf raises several implementation-specific types
        raise RealtimeFeedError("source is not valid GTFS-Realtime protobuf") from error
    if not message.header.gtfs_realtime_version:
        raise RealtimeFeedError("GTFS-Realtime feed has no header version")
    source_timestamp = _timestamp(message.header.timestamp) if message.header.HasField("timestamp") else None
    return ParsedRealtimeFeed(
        source_timestamp=source_timestamp,
        full_dataset=(
            message.header.incrementality
            == gtfs_realtime_pb2.FeedHeader.FULL_DATASET
        ),
        entities=tuple(entity for entity in message.entity if not entity.is_deleted),
    )


def normalize_vehicle_entities(feed: ParsedRealtimeFeed) -> list[NormalizedVehicle]:
    vehicles: list[NormalizedVehicle] = []
    for entity in feed.entities:
        if not entity.HasField("vehicle"):
            continue
        vehicle = entity.vehicle
        vehicle_id = _optional_message_string(vehicle.vehicle, "id")
        if vehicle_id is None:
            # No durable identifier means it cannot safely represent a current vehicle.
            continue
        position = vehicle.position if vehicle.HasField("position") else None
        latitude = float(position.latitude) if position is not None else None
        longitude = float(position.longitude) if position is not None else None
        trip = vehicle.trip if vehicle.HasField("trip") else None
        vehicles.append(
            NormalizedVehicle(
                entity_id=entity.id,
                vehicle_id=vehicle_id,
                trip_id=_optional_message_string(trip, "trip_id"),
                route_id=_optional_message_string(trip, "route_id"),
                latitude=latitude,
                longitude=longitude,
                bearing=float(position.bearing) if position is not None and position.HasField("bearing") else None,
                speed=float(position.speed) if position is not None and position.HasField("speed") else None,
                observed_at=_timestamp(vehicle.timestamp) if vehicle.HasField("timestamp") else None,
                current_stop_sequence=int(vehicle.current_stop_sequence) if vehicle.HasField("current_stop_sequence") else None,
                current_status=_enum_name(
                    gtfs_realtime_pb2.VehiclePosition.VehicleStopStatus,
                    vehicle.current_status,
                ) if vehicle.HasField("current_status") else None,
                schedule_relationship=_trip_relationship(trip),
            )
        )
    return vehicles


def normalize_trip_update_entities(feed: ParsedRealtimeFeed) -> list[NormalizedTripUpdate]:
    updates: list[NormalizedTripUpdate] = []
    for entity in feed.entities:
        if not entity.HasField("trip_update"):
            continue
        update = entity.trip_update
        trip_id = _optional_message_string(update.trip, "trip_id")
        if trip_id is None:
            continue
        delay = int(update.delay) if update.HasField("delay") else None
        next_stop_id: str | None = None
        next_stop_sequence: int | None = None
        next_arrival_at: datetime | None = None
        next_departure_at: datetime | None = None
        next_arrival_delay_seconds: int | None = None
        next_departure_delay_seconds: int | None = None
        for stop_update in update.stop_time_update:
            if stop_update.schedule_relationship == gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SKIPPED:
                continue
            arrival = stop_update.arrival if stop_update.HasField("arrival") else None
            departure = stop_update.departure if stop_update.HasField("departure") else None
            arrival_at = _timestamp(arrival.time) if arrival is not None and arrival.HasField("time") else None
            departure_at = _timestamp(departure.time) if departure is not None and departure.HasField("time") else None
            if arrival_at is None and departure_at is None:
                continue
            next_stop_id = _optional_message_string(stop_update, "stop_id")
            next_stop_sequence = int(stop_update.stop_sequence) if stop_update.HasField("stop_sequence") else None
            next_arrival_at = arrival_at or departure_at
            next_departure_at = departure_at
            next_arrival_delay_seconds = int(arrival.delay) if arrival is not None and arrival.HasField("delay") else None
            next_departure_delay_seconds = int(departure.delay) if departure is not None and departure.HasField("delay") else None
            event_delay = next_arrival_delay_seconds if next_arrival_delay_seconds is not None else next_departure_delay_seconds
            delay = event_delay if event_delay is not None else delay
            break
        updates.append(
            NormalizedTripUpdate(
                entity_id=entity.id,
                trip_id=trip_id,
                route_id=_optional_message_string(update.trip, "route_id"),
                schedule_relationship=_trip_relationship(update.trip),
                delay_seconds=delay,
                next_stop_id=next_stop_id,
                next_stop_sequence=next_stop_sequence,
                next_arrival_at=next_arrival_at,
                next_departure_at=next_departure_at,
                next_arrival_delay_seconds=next_arrival_delay_seconds,
                next_departure_delay_seconds=next_departure_delay_seconds,
                observed_at=_timestamp(update.timestamp) if update.HasField("timestamp") else None,
            )
        )
    return updates


def normalize_alert_entities(feed: ParsedRealtimeFeed) -> list[NormalizedAlert]:
    alerts: list[NormalizedAlert] = []
    for entity in feed.entities:
        if not entity.HasField("alert"):
            continue
        alert = entity.alert
        periods = tuple(
            {
                "start": _timestamp(period.start).isoformat() if period.HasField("start") else None,
                "end": _timestamp(period.end).isoformat() if period.HasField("end") else None,
            }
            for period in alert.active_period
        )
        affected_routes = tuple(
            sorted(
                {
                    informed.route_id
                    for informed in alert.informed_entity
                    if informed.HasField("route_id") and informed.route_id
                }
            )
        )
        affected_stops = tuple(
            sorted(
                {
                    informed.stop_id
                    for informed in alert.informed_entity
                    if informed.HasField("stop_id") and informed.stop_id
                }
            )
        )
        alerts.append(
            NormalizedAlert(
                entity_id=entity.id,
                header=_translated_text(alert.header_text),
                description=_translated_text(alert.description_text),
                url=_translated_text(alert.url),
                cause=_enum_name(gtfs_realtime_pb2.Alert.Cause, alert.cause) if alert.HasField("cause") else None,
                effect=_enum_name(gtfs_realtime_pb2.Alert.Effect, alert.effect) if alert.HasField("effect") else None,
                active_periods=periods,
                affected_routes=affected_routes,
                affected_stops=affected_stops,
            )
        )
    return alerts


def ingest_realtime(
    session: Session,
    *,
    fetcher: Callable[[str], bytes] = fetch_realtime_bytes,
    now: datetime | None = None,
) -> IngestionResult:
    """Fetch all official sources and atomically refresh whatever succeeded.

    A failing source only updates its own health record; previously known state
    remains available and its age is exposed to API consumers.
    """

    observed_now = now or datetime.now(timezone.utc)
    static_feed = session.scalar(
        select(GtfsFeed)
        .where(GtfsFeed.import_status == "succeeded")
        .order_by(GtfsFeed.imported_at.desc())
        .limit(1)
    )
    source_urls = {
        "vehicle_positions": settings.realtime_vehicle_positions_url,
        "trip_updates": settings.realtime_trip_updates_url,
        "alerts": settings.realtime_alerts_url,
    }
    parsed: dict[str, ParsedRealtimeFeed] = {}
    failures: dict[str, str] = {}
    for kind, url in source_urls.items():
        try:
            parsed[kind] = parse_realtime_feed(fetcher(url))
        except (RealtimeFeedError, OSError) as error:
            failures[kind] = str(error)

    for kind, url in source_urls.items():
        status = session.get(RealtimeFeedStatus, kind)
        if status is None:
            status = RealtimeFeedStatus(feed_kind=kind, source_url=url)
            session.add(status)
        status.source_url = url
        status.static_feed_id = static_feed.id if static_feed is not None else None
        status.last_attempt_at = observed_now
        if kind in failures:
            status.error_message = failures[kind]
            continue
        message = parsed[kind]
        status.last_success_at = observed_now
        status.source_timestamp = message.source_timestamp
        status.entity_count = len(message.entities)
        status.error_message = None

    counts = {kind: len(message.entities) for kind, message in parsed.items()}
    recorded_counts: dict[str, int] = {}
    if static_feed is not None:
        _persist_current_state(session, static_feed.id, parsed)
        vehicle_inserted = _record_vehicle_observations(session, static_feed.id, parsed, recorded_at=observed_now)
        trip_observations = _record_active_trip_observations(session, static_feed.id, parsed, recorded_at=observed_now)
        recorded_counts = {
            "vehicle_observations_inserted": vehicle_inserted,
            "trip_observations_inserted": trip_observations.inserted,
            "trip_observations_skipped_no_timing": trip_observations.skipped_no_timing_vehicle_trips,
        }
    session.commit()
    return IngestionResult(
        static_feed_id=static_feed.id if static_feed is not None else None,
        feed_counts=counts,
        recorded_counts=recorded_counts,
        failures=failures,
    )


def _persist_current_state(
    session: Session, static_feed_id: UUID, parsed: dict[str, ParsedRealtimeFeed]
) -> None:
    updates = normalize_trip_update_entities(parsed["trip_updates"]) if "trip_updates" in parsed else []
    update_by_trip = {update.trip_id: update for update in updates}
    vehicles = normalize_vehicle_entities(parsed["vehicle_positions"]) if "vehicle_positions" in parsed else []
    route_ids = {item.route_id for item in [*vehicles, *updates] if item.route_id}
    trip_ids = {item.trip_id for item in [*vehicles, *updates] if item.trip_id}
    matched_trips = {
        trip.gtfs_trip_id: trip
        for trip in session.scalars(
            select(Trip).where(Trip.feed_id == static_feed_id, Trip.gtfs_trip_id.in_(trip_ids))
        )
    } if trip_ids else {}
    matched_routes = {
        route.gtfs_route_id: route
        for route in session.scalars(
            select(Route).where(Route.feed_id == static_feed_id, Route.gtfs_route_id.in_(route_ids))
        )
    } if route_ids else {}

    if "vehicle_positions" in parsed:
        seen_vehicle_ids = set()
        for item in vehicles:
            seen_vehicle_ids.add(item.vehicle_id)
            state = session.scalar(
                select(RealtimeVehicleState).where(
                    RealtimeVehicleState.static_feed_id == static_feed_id,
                    RealtimeVehicleState.vehicle_id == item.vehicle_id,
                )
            )
            if state is None:
                state = RealtimeVehicleState(static_feed_id=static_feed_id, vehicle_id=item.vehicle_id, entity_id=item.entity_id)
                session.add(state)
            matched_trip = matched_trips.get(item.trip_id) if item.trip_id else None
            matched_route = matched_routes.get(item.route_id) if item.route_id else (matched_trip.route if matched_trip else None)
            update = update_by_trip.get(item.trip_id) if item.trip_id else None
            state.entity_id = item.entity_id
            state.trip_gtfs_id = item.trip_id
            state.route_gtfs_id = item.route_id or (matched_route.gtfs_route_id if matched_route else None)
            state.matched_trip_id = matched_trip.id if matched_trip else None
            state.matched_route_id = matched_route.id if matched_route else None
            state.position = WKTElement(f"POINT({item.longitude} {item.latitude})", srid=4326) if item.longitude is not None and item.latitude is not None else None
            state.bearing = item.bearing
            state.speed = item.speed
            state.observed_at = item.observed_at
            state.source_timestamp = parsed["vehicle_positions"].source_timestamp
            state.current_stop_sequence = item.current_stop_sequence
            state.current_status = item.current_status
            state.schedule_relationship = item.schedule_relationship
            state.delay_seconds = update.delay_seconds if update else None
        if parsed["vehicle_positions"].full_dataset:
            stale_states = session.scalars(
                select(RealtimeVehicleState).where(
                    RealtimeVehicleState.static_feed_id == static_feed_id,
                    RealtimeVehicleState.vehicle_id.not_in(seen_vehicle_ids),
                )
            ).all()
            for state in stale_states:
                session.delete(state)

    if "trip_updates" in parsed:
        seen_trip_ids = set()
        for item in updates:
            seen_trip_ids.add(item.trip_id)
            state = session.scalar(
                select(RealtimeTripState).where(
                    RealtimeTripState.static_feed_id == static_feed_id,
                    RealtimeTripState.trip_gtfs_id == item.trip_id,
                )
            )
            if state is None:
                state = RealtimeTripState(static_feed_id=static_feed_id, trip_gtfs_id=item.trip_id, entity_id=item.entity_id)
                session.add(state)
            matched_trip = matched_trips.get(item.trip_id)
            matched_route = matched_routes.get(item.route_id) if item.route_id else (matched_trip.route if matched_trip else None)
            state.entity_id = item.entity_id
            state.route_gtfs_id = item.route_id or (matched_route.gtfs_route_id if matched_route else None)
            state.matched_trip_id = matched_trip.id if matched_trip else None
            state.matched_route_id = matched_route.id if matched_route else None
            state.schedule_relationship = item.schedule_relationship
            state.delay_seconds = item.delay_seconds
            state.next_stop_id = item.next_stop_id
            state.next_stop_sequence = item.next_stop_sequence
            state.next_arrival_at = item.next_arrival_at
            state.observed_at = item.observed_at
            state.source_timestamp = parsed["trip_updates"].source_timestamp
        if parsed["trip_updates"].full_dataset:
            for state in session.scalars(
                select(RealtimeTripState).where(
                    RealtimeTripState.static_feed_id == static_feed_id,
                    RealtimeTripState.trip_gtfs_id.not_in(seen_trip_ids),
                )
            ).all():
                session.delete(state)

    if "alerts" in parsed:
        alerts = normalize_alert_entities(parsed["alerts"])
        seen_alert_ids = set()
        for item in alerts:
            seen_alert_ids.add(item.entity_id)
            state = session.scalar(
                select(RealtimeAlert).where(
                    RealtimeAlert.static_feed_id == static_feed_id,
                    RealtimeAlert.entity_id == item.entity_id,
                )
            )
            if state is None:
                state = RealtimeAlert(static_feed_id=static_feed_id, entity_id=item.entity_id)
                session.add(state)
            state.header = item.header
            state.description = item.description
            state.url = item.url
            state.cause = item.cause
            state.effect = item.effect
            state.active_periods = list(item.active_periods)
            state.affected_routes = list(item.affected_routes)
            state.affected_stops = list(item.affected_stops)
            state.source_timestamp = parsed["alerts"].source_timestamp
        if parsed["alerts"].full_dataset:
            for state in session.scalars(
                select(RealtimeAlert).where(
                    RealtimeAlert.static_feed_id == static_feed_id,
                    RealtimeAlert.entity_id.not_in(seen_alert_ids),
                )
            ).all():
                session.delete(state)


def _record_vehicle_observations(
    session: Session,
    static_feed_id: UUID,
    parsed: dict[str, ParsedRealtimeFeed],
    *,
    recorded_at: datetime,
) -> int:
    """Store only new vehicle facts; repeated unchanged snapshots are no-ops."""

    feed = parsed.get("vehicle_positions")
    if feed is None:
        return 0
    updates = normalize_trip_update_entities(parsed["trip_updates"]) if "trip_updates" in parsed else []
    delays = {update.trip_id: update.delay_seconds for update in updates}
    candidates: list[tuple[str, NormalizedVehicle, int | None]] = []
    for vehicle in normalize_vehicle_entities(feed):
        delay = delays.get(vehicle.trip_id) if vehicle.trip_id else None
        key = observation_key(static_feed_id, feed.source_timestamp, vehicle, delay)
        candidates.append((key, vehicle, delay))
    if not candidates:
        return 0
    existing = set(
        session.scalars(
            select(VehicleObservation.observation_key).where(
                VehicleObservation.observation_key.in_([key for key, _, _ in candidates])
            )
        )
    )
    inserted = 0
    for key, vehicle, delay in candidates:
        if key in existing:
            continue
        session.add(
            VehicleObservation(
                observation_key=key,
                static_feed_id=static_feed_id,
                source_timestamp=feed.source_timestamp,
                observed_at=vehicle.observed_at,
                recorded_at=recorded_at,
                vehicle_id=vehicle.vehicle_id,
                trip_gtfs_id=vehicle.trip_id,
                route_gtfs_id=vehicle.route_id,
                position=WKTElement(f"POINT({vehicle.longitude} {vehicle.latitude})", srid=4326)
                if vehicle.longitude is not None and vehicle.latitude is not None
                else None,
                bearing=vehicle.bearing,
                speed=vehicle.speed,
                current_stop_sequence=vehicle.current_stop_sequence,
                current_status=vehicle.current_status,
                schedule_relationship=vehicle.schedule_relationship,
                delay_seconds=delay,
            )
        )
        inserted += 1
    return inserted


def _record_active_trip_observations(
    session: Session,
    static_feed_id: UUID,
    parsed: dict[str, ParsedRealtimeFeed],
    *,
    recorded_at: datetime,
) -> TripObservationRecordingResult:
    """Record one source-published next-stop fact per active vehicle and poll.

    Trip Updates can contain a high-volume full list of future stop updates.
    Keeping only the first usable stop update for trips that also have a
    VehiclePositions entry in this poll preserves a bounded-rate, immutable
    operational-history fact.  This intentionally does not pretend to record
    every Trip Update or to establish actual departure/dwell ground truth.
    """

    trip_feed = parsed.get("trip_updates")
    if trip_feed is None:
        return TripObservationRecordingResult(inserted=0, skipped_no_timing_vehicle_trips=0)
    candidate_set = active_trip_observation_candidates(static_feed_id, parsed)
    candidates = candidate_set.items
    if not candidates:
        return TripObservationRecordingResult(
            inserted=0,
            skipped_no_timing_vehicle_trips=candidate_set.skipped_no_timing_vehicle_trips,
        )
    existing = set(
        session.scalars(
            select(RealtimeTripObservation.observation_key).where(
                RealtimeTripObservation.observation_key.in_(list(candidates))
            )
        )
    )
    inserted = 0
    for key, (vehicle, update) in candidates.items():
        if key in existing:
            continue
        session.add(
            RealtimeTripObservation(
                observation_key=key,
                static_feed_id=static_feed_id,
                source_timestamp=trip_feed.source_timestamp,
                observed_at=update.observed_at,
                recorded_at=recorded_at,
                vehicle_id=vehicle.vehicle_id,
                entity_id=update.entity_id,
                trip_gtfs_id=update.trip_id,
                route_gtfs_id=update.route_id or vehicle.route_id,
                schedule_relationship=update.schedule_relationship,
                delay_seconds=update.delay_seconds,
                next_stop_id=update.next_stop_id,
                next_stop_sequence=update.next_stop_sequence,
                next_arrival_at=update.next_arrival_at,
                next_departure_at=update.next_departure_at,
                next_arrival_delay_seconds=update.next_arrival_delay_seconds,
                next_departure_delay_seconds=update.next_departure_delay_seconds,
            )
        )
        inserted += 1
    return TripObservationRecordingResult(
        inserted=inserted,
        skipped_no_timing_vehicle_trips=candidate_set.skipped_no_timing_vehicle_trips,
    )


def active_trip_observation_candidates(
    static_feed_id: UUID,
    parsed: dict[str, ParsedRealtimeFeed],
) -> TripObservationCandidates:
    """Return timed active-vehicle candidates and count no-timing exclusions."""

    trip_feed = parsed.get("trip_updates")
    vehicle_feed = parsed.get("vehicle_positions")
    if trip_feed is None or vehicle_feed is None:
        return TripObservationCandidates(items={}, skipped_no_timing_vehicle_trips=0)
    vehicles_by_trip: dict[str, list[NormalizedVehicle]] = {}
    for vehicle in normalize_vehicle_entities(vehicle_feed):
        if vehicle.trip_id:
            vehicles_by_trip.setdefault(vehicle.trip_id, []).append(vehicle)

    candidates: dict[str, tuple[NormalizedVehicle, NormalizedTripUpdate]] = {}
    skipped_no_timing_vehicle_trips = 0
    for update in normalize_trip_update_entities(trip_feed):
        matching_vehicles = vehicles_by_trip.get(update.trip_id, [])
        if update.next_arrival_at is None and update.next_departure_at is None:
            skipped_no_timing_vehicle_trips += len(matching_vehicles)
            continue
        for vehicle in matching_vehicles:
            key = trip_observation_key(static_feed_id, trip_feed.source_timestamp, vehicle, update)
            candidates[key] = (vehicle, update)
    return TripObservationCandidates(
        items=candidates,
        skipped_no_timing_vehicle_trips=skipped_no_timing_vehicle_trips,
    )


def observation_key(
    static_feed_id: UUID,
    source_timestamp: datetime | None,
    vehicle: NormalizedVehicle,
    delay_seconds: int | None,
) -> str:
    """Stable duplicate key: static feed + source fact, not recorder poll time."""

    fact = {
        "static_feed_id": str(static_feed_id),
        "source_timestamp": source_timestamp.isoformat() if source_timestamp else None,
        "vehicle_id": vehicle.vehicle_id,
        "trip_id": vehicle.trip_id,
        "route_id": vehicle.route_id,
        "latitude": vehicle.latitude,
        "longitude": vehicle.longitude,
        "bearing": vehicle.bearing,
        "speed": vehicle.speed,
        "observed_at": vehicle.observed_at.isoformat() if vehicle.observed_at else None,
        "stop_sequence": vehicle.current_stop_sequence,
        "status": vehicle.current_status,
        "relationship": vehicle.schedule_relationship,
        "delay_seconds": delay_seconds,
    }
    return hashlib.sha256(json.dumps(fact, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def trip_observation_key(
    static_feed_id: UUID,
    source_timestamp: datetime | None,
    vehicle: NormalizedVehicle,
    update: NormalizedTripUpdate,
) -> str:
    """Stable identity for one immutable active-vehicle Trip Update fact."""

    fact = {
        "static_feed_id": str(static_feed_id),
        "source_timestamp": source_timestamp.isoformat() if source_timestamp else None,
        "vehicle_id": vehicle.vehicle_id,
        "entity_id": update.entity_id,
        "trip_id": update.trip_id,
        "route_id": update.route_id or vehicle.route_id,
        "schedule_relationship": update.schedule_relationship,
        "delay_seconds": update.delay_seconds,
        "next_stop_id": update.next_stop_id,
        "next_stop_sequence": update.next_stop_sequence,
        "next_arrival_at": update.next_arrival_at.isoformat() if update.next_arrival_at else None,
        "next_departure_at": update.next_departure_at.isoformat() if update.next_departure_at else None,
        "next_arrival_delay_seconds": update.next_arrival_delay_seconds,
        "next_departure_delay_seconds": update.next_departure_delay_seconds,
        "observed_at": update.observed_at.isoformat() if update.observed_at else None,
    }
    return hashlib.sha256(json.dumps(fact, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _timestamp(value: int) -> datetime:
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _optional_message_string(message: Any | None, field: str) -> str | None:
    if message is None or not message.HasField(field):
        return None
    value = getattr(message, field)
    return value if value else None


def _enum_name(wrapper: Any, value: int) -> str:
    try:
        return wrapper.Name(value)
    except ValueError:
        return str(value)


def _trip_relationship(trip: Any | None) -> str | None:
    if trip is None or not trip.HasField("schedule_relationship"):
        return None
    return _enum_name(gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship, trip.schedule_relationship)


def _translated_text(value: Any) -> str | None:
    return next((translation.text for translation in value.translation if translation.text), None)
