from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from geoalchemy2.elements import WKTElement
from sqlalchemy.orm import Session

from tests.fixtures.tiny_gtfs import make_gtfs_archive
from transitpulse_api.gtfs import import_gtfs_zip
from transitpulse_api.models import VehicleObservation


def test_route_reliability_keeps_recorded_and_scheduled_metrics_distinct(
    client: TestClient, db_session: Session
) -> None:
    feed = import_gtfs_zip(
        db_session,
        make_gtfs_archive(
            replacements={
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
        ),
        provider="Test",
        source_url="https://example.test/feed.zip",
    )
    start = datetime(2026, 9, 9, 7, tzinfo=timezone.utc)
    observations = (
        ("bus-1", "TRIP_A", 2, None, 0),
        ("bus-2", "TRIP_B", 2, None, 0),
        ("bus-3", "TRIP_C", 2, None, 0),
        ("bus-1", "TRIP_A", 1, 0, 1),
        ("bus-2", "TRIP_B", 1, 120, 4),
        ("bus-3", "TRIP_C", 1, 600, 23),
    )
    for offset, (vehicle_id, trip_id, stop_sequence, delay, minutes) in enumerate(observations):
        db_session.add(
            VehicleObservation(
                observation_key=f"reliability-{offset}",
                static_feed_id=feed.feed_id,
                observed_at=start + timedelta(minutes=minutes),
                vehicle_id=vehicle_id,
                trip_gtfs_id=trip_id,
                route_gtfs_id="100",
                current_stop_sequence=stop_sequence,
                position=WKTElement(f"POINT(-113.49 {53.54 + offset / 1000})", srid=4326),
                delay_seconds=delay,
            )
        )
    db_session.commit()

    response = client.get(
        "/api/analytics/routes/100?start=2026-09-09T07:00:00Z&end=2026-09-09T07:30:00Z"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["observation_count"] == 6
    assert payload["delay_distribution"] == {
        "source": "direct_recorded_observations",
        "sample_count": 3,
        "median_seconds": 120,
        "percentile_10_seconds": 24,
        "percentile_90_seconds": 504,
        "minimum_seconds": 0,
        "maximum_seconds": 600,
        "sufficient": False,
    }
    assert payload["sufficient_history"] is False
    assert payload["sufficiency_reason"].startswith("Insufficient recorded history")
    assert payload["observed_vehicle_count"] == 3
    assert payload["coverage_duration_seconds"] == 1380
    assert payload["delay_bands"] == {
        "source": "direct_recorded_observations",
        "sample_count": 3,
        "early_count": 0,
        "on_time_count": 1,
        "late_under_5_count": 1,
        "late_5_to_10_count": 1,
        "late_over_10_count": 0,
    }
    assert payload["observed_headways"]["source"] == "direct_recorded_stop_sequence_entries"
    assert payload["observed_headways"]["stop_id"] == "STOP_A"
    assert payload["observed_headways"]["median_seconds"] == 660
    assert payload["observed_headways"]["bunching_event_count"] == 1
    assert payload["observed_headways"]["service_gap_event_count"] == 1
    assert payload["observed_headways"]["variability_seconds"] == 480
    assert payload["observed_headways"]["sufficient"] is True
    assert payload["scheduled_headways"]["source"] == "static_gtfs_schedule"
    assert payload["scheduled_headways"]["median_seconds"] == 600
    assert payload["median_headway_deviation_seconds"] == 60
    assert payload["spatial_reliability"]["features"][0]["properties"]["stop_id"] == "STOP_A"
    assert client.get("/api/analytics/routes/100").status_code == 422
    assert client.get(
        "/api/analytics/routes/100?start=2026-09-08T00:00:00Z&end=2026-09-09T01:00:01Z"
    ).status_code == 422


def test_route_reliability_empty_window_is_explicitly_insufficient(
    client: TestClient, db_session: Session
) -> None:
    import_gtfs_zip(
        db_session,
        make_gtfs_archive(),
        provider="Test",
        source_url="https://example.test/feed.zip",
    )
    response = client.get(
        "/api/analytics/routes/100?start=2026-09-09T07:00:00Z&end=2026-09-09T08:00:00Z"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["observation_count"] == 0
    assert payload["observed_vehicle_count"] == 0
    assert payload["sufficient_history"] is False
    assert payload["sufficiency_reason"] == "Insufficient recorded history: no observations in this window."
    assert payload["delay_distribution"]["median_seconds"] is None
    assert payload["observed_headways"]["sufficient"] is False


def test_route_reliability_isolates_overlapping_identifiers_by_feed(
    client: TestClient, db_session: Session
) -> None:
    first_feed = import_gtfs_zip(
        db_session,
        make_gtfs_archive(
            replacements={
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
        ),
        provider="First overlapping feed",
        source_url="https://example.test/first.zip",
    )
    second_feed = import_gtfs_zip(
        db_session,
        make_gtfs_archive(
            replacements={
                "agency.txt": """agency_id,agency_name,agency_url,agency_timezone
ETS,Second Test Transit,https://example.test,America/Edmonton
""",
                "trips.txt": """route_id,service_id,trip_id,trip_headsign,direction_id,shape_id
100,WEEKDAY,TRIP_A,Downtown,0,SHAPE_100
100,WEEKDAY,TRIP_B,Downtown,0,SHAPE_100
100,WEEKDAY,TRIP_C,Downtown,0,SHAPE_100
""",
                "stop_times.txt": """trip_id,arrival_time,departure_time,stop_id,stop_sequence
TRIP_A,25:00:00,25:00:30,STOP_A,1
TRIP_A,25:05:00,25:05:30,STOP_B,2
TRIP_B,25:20:00,25:20:30,STOP_A,1
TRIP_B,25:25:00,25:25:30,STOP_B,2
TRIP_C,25:40:00,25:40:30,STOP_A,1
TRIP_C,25:45:00,25:45:30,STOP_B,2
""",
            }
        ),
        provider="Second overlapping feed",
        source_url="https://example.test/second.zip",
    )
    start = datetime(2026, 9, 9, 7, tzinfo=timezone.utc)

    for offset, minutes in enumerate((2, 12, 22)):
        db_session.add(
            VehicleObservation(
                observation_key=f"first-feed-{offset}",
                static_feed_id=first_feed.feed_id,
                observed_at=start + timedelta(minutes=minutes),
                vehicle_id=f"first-bus-{offset}",
                trip_gtfs_id=("TRIP_A", "TRIP_B", "TRIP_C")[offset],
                route_gtfs_id="100",
                current_stop_sequence=1,
                position=WKTElement(f"POINT(-113.49 {53.54 + offset / 1000})", srid=4326),
                delay_seconds=999,
            )
        )

    second_observations = (
        ("second-bus-1", "TRIP_A", 2, None, 0),
        ("second-bus-2", "TRIP_B", 2, None, 0),
        ("second-bus-3", "TRIP_C", 2, None, 0),
        ("second-bus-1", "TRIP_A", 1, 0, 1),
        ("second-bus-2", "TRIP_B", 1, 120, 21),
        ("second-bus-3", "TRIP_C", 1, 240, 41),
    )
    for offset, (vehicle_id, trip_id, stop_sequence, delay, minutes) in enumerate(
        second_observations
    ):
        db_session.add(
            VehicleObservation(
                observation_key=f"second-feed-{offset}",
                static_feed_id=second_feed.feed_id,
                observed_at=start + timedelta(minutes=minutes),
                vehicle_id=vehicle_id,
                trip_gtfs_id=trip_id,
                route_gtfs_id="100",
                current_stop_sequence=stop_sequence,
                position=WKTElement(f"POINT(-113.48 {53.55 + offset / 1000})", srid=4326),
                delay_seconds=delay,
            )
        )
    db_session.commit()

    response = client.get(
        "/api/analytics/routes/100",
        params={
            "feed_id": str(second_feed.feed_id),
            "start": "2026-09-09T07:00:00Z",
            "end": "2026-09-09T08:00:00Z",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["static_feed_id"] == str(second_feed.feed_id)
    assert payload["observation_count"] == 6
    assert payload["observed_vehicle_count"] == 3
    assert payload["delay_distribution"]["sample_count"] == 3
    assert payload["delay_distribution"]["median_seconds"] == 120
    assert payload["delay_distribution"]["maximum_seconds"] == 240
    assert payload["observed_headways"]["median_seconds"] == 1200
    assert payload["scheduled_headways"]["median_seconds"] == 1200
