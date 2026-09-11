from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from geoalchemy2.elements import WKTElement
from pytest import MonkeyPatch
from sqlalchemy.orm import Session

from tests.fixtures.tiny_gtfs import make_gtfs_archive
from transitpulse_api.config import settings
from transitpulse_api.gtfs import import_gtfs_zip
from transitpulse_api.models import (
    RealtimeAlert,
    RealtimeFeedStatus,
    RealtimeTripState,
    RealtimeVehicleState,
    VehicleObservation,
)


THREE_TRIP_REPLACEMENTS = {
    "trips.txt": """route_id,service_id,trip_id,trip_headsign,direction_id,shape_id
100,WEEKDAY,TRIP_A,Downtown,0,SHAPE_100
100,WEEKDAY,TRIP_B,Downtown,0,SHAPE_100
100,WEEKDAY,TRIP_C,Downtown,0,SHAPE_100
""",
    "stop_times.txt": """trip_id,arrival_time,departure_time,stop_id,stop_sequence
TRIP_A,25:00:00,25:00:30,STOP_A,1
TRIP_A,25:05:00,25:05:30,STOP_B,2
TRIP_B,25:10:00,25:10:30,STOP_A,1
TRIP_B,25:15:00,25:15:30,STOP_B,2
TRIP_C,25:20:00,25:20:30,STOP_A,1
TRIP_C,25:25:00,25:25:30,STOP_B,2
""",
}


def test_network_health_counts_and_ranks_supported_live_issues(
    client: TestClient, db_session: Session
) -> None:
    feed = import_gtfs_zip(
        db_session,
        make_gtfs_archive(replacements=THREE_TRIP_REPLACEMENTS),
        provider="Test",
        source_url="https://example.test/feed.zip",
    )
    now = datetime.now(timezone.utc)
    db_session.add(
        RealtimeFeedStatus(
            feed_kind="vehicle_positions",
            source_url="https://example.test/vehicles.pb",
            static_feed_id=feed.feed_id,
            last_attempt_at=now,
            last_success_at=now,
            source_timestamp=now,
            entity_count=1,
        )
    )
    db_session.add(
        RealtimeVehicleState(
            static_feed_id=feed.feed_id,
            entity_id="vehicle-1",
            vehicle_id="bus-1",
            trip_gtfs_id="TRIP_A",
            route_gtfs_id="100",
            position=WKTElement("POINT(-113.49 53.54)", srid=4326),
            observed_at=now,
            source_timestamp=now,
            delay_seconds=600,
        )
    )
    for index, (trip_id, minutes) in enumerate((("TRIP_A", 0), ("TRIP_B", 2), ("TRIP_C", 20))):
        db_session.add(
            RealtimeTripState(
                static_feed_id=feed.feed_id,
                entity_id=f"trip-{index}",
                trip_gtfs_id=trip_id,
                route_gtfs_id="100",
                delay_seconds=600 if index == 0 else 0,
                next_stop_id="STOP_A",
                next_stop_sequence=1,
                next_arrival_at=now + timedelta(minutes=minutes),
                observed_at=now,
                source_timestamp=now,
            )
        )
    db_session.add(
        RealtimeAlert(
            static_feed_id=feed.feed_id,
            entity_id="alert-1",
            header="Downtown detour",
            effect="DETOUR",
            active_periods=[{"start": now.isoformat(), "end": (now + timedelta(hours=2)).isoformat()}],
            affected_routes=["100"],
            affected_stops=[],
            source_timestamp=now,
        )
    )
    db_session.commit()

    response = client.get("/api/operations/network", params={"feed_id": str(feed.feed_id)})
    assert response.status_code == 200
    payload = response.json()
    assert payload["stale"] is False
    assert payload["active_vehicles"] == 1
    assert payload["routes_with_live_service"] == 1
    assert payload["routes_delayed"] == 1
    assert payload["routes_with_bunching"] == 1
    assert payload["routes_with_service_gaps"] == 1
    assert payload["active_alerts"] == 1
    assert payload["issues"][0]["route_id"] == "100"
    assert payload["issues"][0]["service_status"] == "BUNCHING"
    assert payload["issues"][0]["alert_count"] == 1


def _add_period(
    db_session: Session,
    *,
    feed_id,
    prefix: str,
    start: datetime,
    event_minutes: tuple[int, int, int],
    delays: tuple[int, int, int],
) -> None:
    for vehicle_index, (event_minute, delay) in enumerate(zip(event_minutes, delays)):
        vehicle_id = f"{prefix}-bus-{vehicle_index}"
        trip_id = ("TRIP_A", "TRIP_B", "TRIP_C")[vehicle_index]
        for pair_index, (sequence, minute, recorded_delay) in enumerate(
            ((2, max(0, event_minute - 1), None), (1, event_minute, delay))
        ):
            db_session.add(
                VehicleObservation(
                    observation_key=f"{prefix}-{vehicle_index}-{pair_index}",
                    static_feed_id=feed_id,
                    observed_at=start + timedelta(minutes=minute),
                    vehicle_id=vehicle_id,
                    trip_gtfs_id=trip_id,
                    route_gtfs_id="100",
                    current_stop_sequence=sequence,
                    position=WKTElement(
                        f"POINT(-113.49 {53.54 + vehicle_index / 1000})", srid=4326
                    ),
                    delay_seconds=recorded_delay,
                )
            )


def test_historical_comparison_identifies_better_period_and_guards_coverage(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    feed = import_gtfs_zip(
        db_session,
        make_gtfs_archive(replacements=THREE_TRIP_REPLACEMENTS),
        provider="Test",
        source_url="https://example.test/feed.zip",
    )
    first = datetime(2026, 9, 9, 7, tzinfo=timezone.utc)
    second = datetime(2026, 9, 9, 8, tzinfo=timezone.utc)
    _add_period(
        db_session,
        feed_id=feed.feed_id,
        prefix="period-a",
        start=first,
        event_minutes=(1, 11, 21),
        delays=(0, 30, 60),
    )
    _add_period(
        db_session,
        feed_id=feed.feed_id,
        prefix="period-b",
        start=second,
        event_minutes=(1, 13, 28),
        delays=(300, 600, 900),
    )
    db_session.commit()
    monkeypatch.setattr(settings, "analytics_min_observations", 6)
    monkeypatch.setattr(settings, "analytics_min_delay_samples", 3)
    monkeypatch.setattr(settings, "analytics_min_coverage_seconds", 1200)
    monkeypatch.setattr(settings, "analytics_min_headway_samples", 2)

    params = {
        "feed_id": str(feed.feed_id),
        "period_a_start": first.isoformat(),
        "period_a_end": (first + timedelta(minutes=30)).isoformat(),
        "period_b_start": second.isoformat(),
        "period_b_end": (second + timedelta(minutes=30)).isoformat(),
    }
    response = client.get("/api/analytics/routes/100/compare", params=params)
    assert response.status_code == 200
    payload = response.json()
    assert payload["comparable"] is True
    assert payload["better_period"] == "A"
    assert payload["period_a"]["median_delay_seconds"] == 30
    assert payload["period_b"]["median_delay_seconds"] == 600
    assert payload["deltas_b_minus_a"]["median_delay_seconds"] == 570
    assert payload["period_a"]["observed_headway_seconds"] == 600
    assert payload["period_b"]["observed_headway_seconds"] == 810
    assert payload["deciding_signals"]

    monkeypatch.setattr(settings, "analytics_min_observations", 1)
    monkeypatch.setattr(settings, "analytics_min_delay_samples", 1)
    monkeypatch.setattr(settings, "analytics_min_coverage_seconds", 0)
    params["period_b_end"] = (second + timedelta(minutes=2)).isoformat()
    response = client.get("/api/analytics/routes/100/compare", params=params)
    assert response.status_code == 200
    payload = response.json()
    assert payload["period_b"]["sufficient_history"] is True
    assert payload["comparable"] is False
    assert payload["better_period"] is None
    assert "coverage differs too much" in payload["verdict"]
