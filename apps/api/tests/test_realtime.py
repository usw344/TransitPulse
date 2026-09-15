from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from google.transit import gtfs_realtime_pb2

from transitpulse_api.main import realtime_is_stale
from transitpulse_api.realtime import (
    RealtimeFeedError,
    active_trip_observation_candidates,
    normalize_alert_entities,
    normalize_trip_update_entities,
    normalize_vehicle_entities,
    parse_realtime_feed,
)


def _fixture_message() -> bytes:
    message = gtfs_realtime_pb2.FeedMessage()
    message.header.gtfs_realtime_version = "2.0"
    message.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    message.header.timestamp = 1_700_000_000

    vehicle = message.entity.add()
    vehicle.id = "vehicle-entity"
    vehicle.vehicle.vehicle.id = "bus-42"
    vehicle.vehicle.trip.trip_id = "trip-100"
    vehicle.vehicle.trip.route_id = "100"
    vehicle.vehicle.trip.schedule_relationship = gtfs_realtime_pb2.TripDescriptor.SCHEDULED
    vehicle.vehicle.position.latitude = 53.5461
    vehicle.vehicle.position.longitude = -113.4938
    vehicle.vehicle.position.bearing = 91.5
    vehicle.vehicle.position.speed = 8.25
    vehicle.vehicle.timestamp = 1_700_000_010
    vehicle.vehicle.current_stop_sequence = 4
    vehicle.vehicle.current_status = gtfs_realtime_pb2.VehiclePosition.IN_TRANSIT_TO

    update = message.entity.add()
    update.id = "trip-entity"
    update.trip_update.trip.trip_id = "trip-100"
    update.trip_update.trip.route_id = "100"
    update.trip_update.delay = 75
    update.trip_update.timestamp = 1_700_000_011
    stop = update.trip_update.stop_time_update.add()
    stop.stop_id = "stop-4"
    stop.stop_sequence = 4
    stop.arrival.time = 1_700_000_100
    stop.arrival.delay = 90
    stop.departure.time = 1_700_000_140
    stop.departure.delay = 105

    alert = message.entity.add()
    alert.id = "alert-entity"
    alert.alert.header_text.translation.add().text = "Detour"
    alert.alert.description_text.translation.add().text = "Use alternate stop"
    alert.alert.informed_entity.add().route_id = "100"
    alert.alert.informed_entity.add().stop_id = "stop-4"
    return message.SerializeToString()


def test_realtime_protobuf_normalization_preserves_optional_values() -> None:
    feed = parse_realtime_feed(_fixture_message())

    assert feed.full_dataset is True
    assert feed.source_timestamp == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
    vehicle = normalize_vehicle_entities(feed)[0]
    assert vehicle.vehicle_id == "bus-42"
    assert vehicle.route_id == "100"
    assert vehicle.latitude == pytest.approx(53.5461)
    assert vehicle.current_status == "IN_TRANSIT_TO"

    update = normalize_trip_update_entities(feed)[0]
    assert update.delay_seconds == 90
    assert update.next_stop_id == "stop-4"
    assert update.next_arrival_at == datetime.fromtimestamp(1_700_000_100, tz=timezone.utc)
    assert update.next_departure_at == datetime.fromtimestamp(1_700_000_140, tz=timezone.utc)
    assert update.next_arrival_delay_seconds == 90
    assert update.next_departure_delay_seconds == 105

    alert = normalize_alert_entities(feed)[0]
    assert alert.header == "Detour"
    assert alert.affected_routes == ("100",)
    assert alert.affected_stops == ("stop-4",)


def test_active_trip_observation_candidates_are_limited_to_current_vehicles() -> None:
    feed = parse_realtime_feed(_fixture_message())

    candidates = active_trip_observation_candidates(
        UUID("12345678-1234-5678-1234-567812345678"),
        {"vehicle_positions": feed, "trip_updates": feed},
    )

    assert len(candidates.items) == 1
    assert candidates.skipped_no_timing_vehicle_trips == 0
    vehicle, update = next(iter(candidates.items.values()))
    assert vehicle.vehicle_id == "bus-42"
    assert update.trip_id == "trip-100"
    assert update.next_departure_at == datetime.fromtimestamp(1_700_000_140, tz=timezone.utc)


def test_active_trip_observation_candidates_exclude_no_timing_updates() -> None:
    message = gtfs_realtime_pb2.FeedMessage()
    message.header.gtfs_realtime_version = "2.0"
    message.header.timestamp = 1_700_000_000
    vehicle = message.entity.add()
    vehicle.id = "vehicle-entity"
    vehicle.vehicle.vehicle.id = "bus-42"
    vehicle.vehicle.trip.trip_id = "trip-100"
    update = message.entity.add()
    update.id = "trip-entity"
    update.trip_update.trip.trip_id = "trip-100"
    update.trip_update.stop_time_update.add().stop_sequence = 4
    feed = parse_realtime_feed(message.SerializeToString())

    candidates = active_trip_observation_candidates(
        UUID("12345678-1234-5678-1234-567812345678"),
        {"vehicle_positions": feed, "trip_updates": feed},
    )

    assert candidates.items == {}
    assert candidates.skipped_no_timing_vehicle_trips == 1


def test_realtime_parser_rejects_malformed_payload() -> None:
    with pytest.raises(RealtimeFeedError, match="valid GTFS-Realtime"):
        parse_realtime_feed(b"\x80")


def test_stale_status_uses_source_timestamp_not_only_fetch_time() -> None:
    now = datetime.now(timezone.utc)
    assert realtime_is_stale(now, now - timedelta(seconds=121)) is True
    assert realtime_is_stale(now, now - timedelta(seconds=10)) is False
    assert realtime_is_stale(None, None) is True
