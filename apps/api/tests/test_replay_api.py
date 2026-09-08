from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from geoalchemy2.elements import WKTElement
from sqlalchemy.orm import Session

from tests.fixtures.tiny_gtfs import make_gtfs_archive
from transitpulse_api.gtfs import import_gtfs_zip
from transitpulse_api.models import VehicleObservation


def test_replay_history_is_bounded_feed_scoped_and_reports_availability(
    client: TestClient, db_session: Session
) -> None:
    feed = import_gtfs_zip(
        db_session,
        make_gtfs_archive(),
        provider="Test",
        source_url="https://example.test/feed.zip",
    )
    start = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    for offset, vehicle_id in enumerate(("bus-1", "bus-1", "bus-2")):
        db_session.add(
            VehicleObservation(
                observation_key=f"replay-{offset}",
                static_feed_id=feed.feed_id,
                observed_at=start + timedelta(seconds=30 * offset),
                vehicle_id=vehicle_id,
                trip_gtfs_id="TRIP_100",
                route_gtfs_id="100",
                position=WKTElement(f"POINT(-113.49 {53.54 + offset / 1000})", srid=4326),
                delay_seconds=offset * 30,
            )
        )
    db_session.commit()

    availability = client.get("/api/history/availability?route_id=100")
    assert availability.status_code == 200
    availability_payload = availability.json()
    assert availability_payload["static_feed_id"] == str(feed.feed_id)
    assert availability_payload["observation_count"] == 3
    assert datetime.fromisoformat(availability_payload["first_observed_at"]).astimezone(timezone.utc) == start
    assert datetime.fromisoformat(availability_payload["last_observed_at"]).astimezone(timezone.utc) == start + timedelta(minutes=1)

    history = client.get(
        "/api/history/observations?start=2026-09-08T12:00:00Z&end=2026-09-08T12:02:00Z&route_id=100&limit=2"
    )
    assert history.status_code == 200
    payload = history.json()
    assert payload["static_feed_id"] == str(feed.feed_id)
    assert payload["limit"] == 2
    assert [feature["properties"]["vehicle_id"] for feature in payload["features"]] == ["bus-1", "bus-1"]
    assert client.get(
        "/api/history/observations?start=2026-09-08T12:00:00Z&end=2026-09-09T13:00:00Z"
    ).status_code == 422
