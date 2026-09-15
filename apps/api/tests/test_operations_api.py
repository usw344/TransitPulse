"""Database-backed coverage for the live route panel and directional analytics.

These exercise, through HTTP, three paths that previously had only unit
coverage: the route operations panel's EARLY state (which once raised a
KeyError and returned HTTP 500), the comparable-prediction horizon, and
per-direction headway grouping at a stop served in both directions.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from geoalchemy2.elements import WKTElement
from sqlalchemy.orm import Session

from tests.fixtures.tiny_gtfs import make_gtfs_archive
from transitpulse_api.gtfs import import_gtfs_zip
from transitpulse_api.models import (
    RealtimeFeedStatus,
    RealtimeTripState,
    RealtimeVehicleState,
    VehicleObservation,
)


ONE_DIRECTION = {
    "trips.txt": """route_id,service_id,trip_id,trip_headsign,direction_id,shape_id
100,WEEKDAY,TRIP_A,Downtown,0,SHAPE_100
100,WEEKDAY,TRIP_B,Downtown,0,SHAPE_100
100,WEEKDAY,TRIP_C,Downtown,0,SHAPE_100
100,WEEKDAY,TRIP_D,Downtown,0,SHAPE_100
""",
    "stop_times.txt": """trip_id,arrival_time,departure_time,stop_id,stop_sequence
TRIP_A,25:00:00,25:00:30,STOP_A,1
TRIP_A,25:05:00,25:05:30,STOP_B,2
TRIP_B,25:10:00,25:10:30,STOP_A,1
TRIP_B,25:15:00,25:15:30,STOP_B,2
TRIP_C,25:20:00,25:20:30,STOP_A,1
TRIP_C,25:25:00,25:25:30,STOP_B,2
TRIP_D,25:30:00,25:30:30,STOP_A,1
TRIP_D,25:35:00,25:35:30,STOP_B,2
""",
}


def _live_route(
    db_session: Session,
    *,
    arrivals_minutes: tuple[float, ...],
    delay_seconds: int,
    publish_trip_feed: bool,
):
    feed = import_gtfs_zip(
        db_session,
        make_gtfs_archive(replacements=ONE_DIRECTION),
        provider="Test",
        source_url="https://example.test/feed.zip",
    )
    now = datetime.now(timezone.utc)
    kinds = ["vehicle_positions", "trip_updates"] if publish_trip_feed else ["vehicle_positions"]
    for kind in kinds:
        db_session.add(
            RealtimeFeedStatus(
                feed_kind=kind,
                source_url=f"https://example.test/{kind}.pb",
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
            delay_seconds=delay_seconds,
        )
    )
    for index, minutes in enumerate(arrivals_minutes):
        db_session.add(
            RealtimeTripState(
                static_feed_id=feed.feed_id,
                entity_id=f"trip-{index}",
                trip_gtfs_id=("TRIP_A", "TRIP_B", "TRIP_C", "TRIP_D")[index],
                route_gtfs_id="100",
                delay_seconds=delay_seconds,
                next_stop_id="STOP_A",
                next_stop_sequence=1,
                next_arrival_at=now + timedelta(minutes=minutes),
                observed_at=now,
                source_timestamp=now,
            )
        )
    db_session.commit()
    return feed


def test_route_operations_reports_early_running_instead_of_failing(
    client: TestClient, db_session: Session
) -> None:
    feed = _live_route(
        db_session, arrivals_minutes=(0, 10, 20), delay_seconds=-300, publish_trip_feed=False
    )

    response = client.get("/api/operations/routes/100", params={"feed_id": str(feed.feed_id)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["service_status"] == "EARLY"
    assert payload["status_reason"]
    assert payload["bunching"] is False
    assert payload["service_gap"] is False


def test_route_operations_excludes_predictions_beyond_the_comparable_horizon(
    client: TestClient, db_session: Session
) -> None:
    # Three trips ten minutes apart, plus a next-service-period prediction eight
    # hours out.  Included, that prediction would read as a rider-visible gap.
    feed = _live_route(
        db_session, arrivals_minutes=(0, 10, 20, 480), delay_seconds=0, publish_trip_feed=True
    )

    response = client.get("/api/operations/routes/100", params={"feed_id": str(feed.feed_id)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["prediction_stop_id"] == "STOP_A"
    assert payload["predicted_headways_seconds"] == [600, 600]
    assert payload["excluded_arrivals_beyond_horizon"] == 1
    assert payload["prediction_horizon_seconds"] == 5400
    assert payload["service_gap"] is False
    assert payload["service_status"] == "ON_TIME"

    network = client.get("/api/operations/network", params={"feed_id": str(feed.feed_id)}).json()
    route = next(item for item in network["routes"] if item["route_id"] == "100")
    assert route["excluded_arrivals_beyond_horizon"] == 1
    assert route["service_gap"] is False


def test_route_reliability_does_not_pool_directions_at_a_shared_stop(
    client: TestClient, db_session: Session
) -> None:
    # STOP_A is served every 10 minutes in each direction, offset by 5 minutes.
    # Pooled, that reads as a 5-minute headway; the real directional headway
    # (and the directional schedule it is compared with) is 10 minutes.
    feed = import_gtfs_zip(
        db_session,
        make_gtfs_archive(
            replacements={
                "trips.txt": """route_id,service_id,trip_id,trip_headsign,direction_id,shape_id
100,WEEKDAY,OUT_A,Outbound,0,SHAPE_100
100,WEEKDAY,OUT_B,Outbound,0,SHAPE_100
100,WEEKDAY,OUT_C,Outbound,0,SHAPE_100
100,WEEKDAY,IN_A,Inbound,1,SHAPE_100
100,WEEKDAY,IN_B,Inbound,1,SHAPE_100
""",
                "stop_times.txt": """trip_id,arrival_time,departure_time,stop_id,stop_sequence
OUT_A,25:00:00,25:00:30,STOP_A,1
OUT_A,25:04:00,25:04:30,STOP_B,2
OUT_B,25:10:00,25:10:30,STOP_A,1
OUT_B,25:14:00,25:14:30,STOP_B,2
OUT_C,25:20:00,25:20:30,STOP_A,1
OUT_C,25:24:00,25:24:30,STOP_B,2
IN_A,25:01:00,25:01:30,STOP_B,1
IN_A,25:05:00,25:05:30,STOP_A,2
IN_B,25:11:00,25:11:30,STOP_B,1
IN_B,25:15:00,25:15:30,STOP_A,2
""",
            }
        ),
        provider="Test",
        source_url="https://example.test/feed.zip",
    )
    start = datetime(2026, 9, 9, 7, tzinfo=timezone.utc)
    # (vehicle, trip, first sequence, first minute, arrival sequence at STOP_A, arrival minute)
    movements = (
        ("out-1", "OUT_A", 2, 0, 1, 1),
        ("out-2", "OUT_B", 2, 10, 1, 11),
        ("out-3", "OUT_C", 2, 20, 1, 21),
        ("in-1", "IN_A", 1, 2, 2, 6),
        ("in-2", "IN_B", 1, 12, 2, 16),
    )
    for vehicle_id, trip_id, first_sequence, first_minute, arrival_sequence, arrival_minute in movements:
        for suffix, sequence, minute in (
            ("before", first_sequence, first_minute),
            ("arrive", arrival_sequence, arrival_minute),
        ):
            db_session.add(
                VehicleObservation(
                    observation_key=f"direction-{vehicle_id}-{suffix}",
                    static_feed_id=feed.feed_id,
                    observed_at=start + timedelta(minutes=minute),
                    vehicle_id=vehicle_id,
                    trip_gtfs_id=trip_id,
                    route_gtfs_id="100",
                    current_stop_sequence=sequence,
                    position=WKTElement("POINT(-113.4938 53.5461)", srid=4326),
                    delay_seconds=60,
                )
            )
    db_session.commit()

    response = client.get(
        "/api/analytics/routes/100?start=2026-09-09T07:00:00Z&end=2026-09-09T07:30:00Z"
    )

    assert response.status_code == 200
    payload = response.json()
    observed = payload["observed_headways"]
    assert observed["stop_id"] == "STOP_A"
    assert observed["median_seconds"] == 600
    assert observed["bunching_event_count"] == 0
    assert payload["scheduled_headways"]["median_seconds"] == 600
    assert payload["median_headway_deviation_seconds"] == 0


def test_network_issue_queue_skips_routes_missing_from_the_static_feed(
    client: TestClient, db_session: Session
) -> None:
    # Route 999 reports vehicles and badly late trips but is not in the static
    # feed, so it cannot be opened from the queue; it is counted, not queued.
    feed = _live_route(
        db_session, arrivals_minutes=(0, 10, 20), delay_seconds=900, publish_trip_feed=True
    )
    now = datetime.now(timezone.utc)
    db_session.add(
        RealtimeVehicleState(
            static_feed_id=feed.feed_id,
            entity_id="vehicle-unmatched",
            vehicle_id="bus-9",
            trip_gtfs_id="GHOST_TRIP",
            route_gtfs_id="999",
            position=WKTElement("POINT(-113.50 53.55)", srid=4326),
            observed_at=now,
            source_timestamp=now,
            delay_seconds=900,
        )
    )
    db_session.add(
        RealtimeTripState(
            static_feed_id=feed.feed_id,
            entity_id="trip-unmatched",
            trip_gtfs_id="GHOST_TRIP",
            route_gtfs_id="999",
            delay_seconds=900,
            observed_at=now,
            source_timestamp=now,
        )
    )
    db_session.commit()

    payload = client.get("/api/operations/network", params={"feed_id": str(feed.feed_id)}).json()

    assert {route["route_id"] for route in payload["routes"]} == {"100", "999"}
    assert [issue["route_id"] for issue in payload["issues"]] == ["100"]
    assert payload["routes_delayed"] == 2
