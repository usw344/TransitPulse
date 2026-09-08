from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from geoalchemy2.elements import WKTElement
from google.transit import gtfs_realtime_pb2
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.fixtures.tiny_gtfs import make_gtfs_archive
from transitpulse_api.gtfs import import_gtfs_zip
from transitpulse_api.models import RealtimeVehicleState, VehicleObservation
from transitpulse_api.realtime import ingest_realtime


def realtime_fixture() -> bytes:
    message = gtfs_realtime_pb2.FeedMessage()
    message.header.gtfs_realtime_version = "2.0"
    message.header.timestamp = 1_788_793_200

    vehicle = message.entity.add()
    vehicle.id = "v-entity"
    vehicle.vehicle.vehicle.id = "vehicle-1"
    vehicle.vehicle.trip.trip_id = "TRIP_100"
    vehicle.vehicle.trip.route_id = "100"
    vehicle.vehicle.position.latitude = 53.5461
    vehicle.vehicle.position.longitude = -113.4938
    vehicle.vehicle.timestamp = 1_788_793_200

    update = message.entity.add()
    update.id = "tu-entity"
    update.trip_update.trip.trip_id = "TRIP_100"
    update.trip_update.trip.route_id = "100"
    update.trip_update.delay = 90
    stop = update.trip_update.stop_time_update.add()
    stop.stop_id = "STOP_A"
    stop.stop_sequence = 1
    stop.arrival.time = 1_788_793_400

    alert = message.entity.add()
    alert.id = "a-entity"
    alert.alert.header_text.translation.add().text = "Fixture alert"
    alert.alert.informed_entity.add().route_id = "100"
    return message.SerializeToString()


def test_ingestion_persists_current_state_history_and_deduplicates(
    client: TestClient, db_session: Session
) -> None:
    import_gtfs_zip(db_session, make_gtfs_archive(), provider="Test", source_url="https://example.test/feed.zip")
    payload = realtime_fixture()

    result = ingest_realtime(db_session, fetcher=lambda _: payload)
    assert result.failures == {}
    state = db_session.scalar(select(RealtimeVehicleState))
    assert state is not None
    assert state.route_gtfs_id == "100"
    assert state.delay_seconds == 90
    assert db_session.scalar(select(func.count()).select_from(VehicleObservation)) == 1

    ingest_realtime(db_session, fetcher=lambda _: payload)
    assert db_session.scalar(select(func.count()).select_from(VehicleObservation)) == 1

    operations = client.get("/api/operations/routes/100")
    assert operations.status_code == 200
    assert operations.json()["active_vehicles"] == 1
    assert operations.json()["average_delay_seconds"] == 90

    start = "2026-09-07T00:00:00Z"
    end = "2026-09-08T00:00:00Z"
    history = client.get(f"/api/history/vehicles/vehicle-1?start={start}&end={end}")
    assert history.status_code == 200
    assert history.json()["features"][0]["properties"]["static_feed_id"] == str(result.static_feed_id)
    assert client.get("/api/history/vehicles/vehicle-1?start=2026-09-07T00:00:00Z&end=2026-09-08T01:00:00Z").status_code == 422


def test_feed_failure_keeps_previous_current_state(db_session: Session) -> None:
    import_gtfs_zip(db_session, make_gtfs_archive(), provider="Test", source_url="https://example.test/feed.zip")
    ingest_realtime(db_session, fetcher=lambda _: realtime_fixture())

    result = ingest_realtime(db_session, fetcher=lambda _: (_ for _ in ()).throw(OSError("offline")))
    assert set(result.failures) == {"vehicle_positions", "trip_updates", "alerts"}
    assert db_session.scalar(select(func.count()).select_from(RealtimeVehicleState)) == 1
