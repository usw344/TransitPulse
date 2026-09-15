from datetime import datetime, timedelta, timezone

from geoalchemy2.elements import WKTElement
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.fixtures.tiny_gtfs import make_gtfs_archive
from transitpulse_api.gtfs import import_gtfs_zip
from transitpulse_api.models import GtfsFeed, VehicleObservation
from transitpulse_ml.extractor import ExtractionConfig, extract_labels


@pytest.mark.parametrize(
    ("realtime_route_id", "expected_labels", "expected_rejection"),
    [("100", 1, None), (None, 0, "realtime_static_route_mismatch")],
)
def test_streaming_extractor_reads_immutable_gtfs_and_after_midnight_observations(
    db_session: Session, realtime_route_id: str | None, expected_labels: int, expected_rejection: str | None
) -> None:
    imported = import_gtfs_zip(db_session, make_gtfs_archive(), provider="Test", source_url="https://example.test/feed.zip")
    feed = db_session.get(GtfsFeed, imported.feed_id)
    assert feed is not None
    base = datetime(2026, 9, 10, 7, 0, tzinfo=timezone.utc)  # 01:00 America/Regina, service day Sep 9
    positions = [
        (-113.4938, 53.5461, 1),
        (-113.4919, 53.5481, 1),
        (-113.4900, 53.5500, 2),
    ]
    for offset, (longitude, latitude, sequence) in enumerate(positions):
        observed_at = base + timedelta(seconds=offset * 30)
        db_session.add(
            VehicleObservation(
                observation_key=f"extract-{offset}",
                static_feed_id=feed.id,
                source_timestamp=observed_at,
                observed_at=observed_at,
                recorded_at=observed_at,
                vehicle_id="vehicle-1",
                trip_gtfs_id="TRIP_100",
                route_gtfs_id=realtime_route_id,
                position=WKTElement(f"POINT({longitude} {latitude})", srid=4326),
                current_stop_sequence=sequence,
            )
        )
    db_session.commit()
    source_count = db_session.scalar(select(func.count()).select_from(VehicleObservation))

    labels, census = extract_labels(
        db_session.get_bind(),
        ExtractionConfig(
            start_at=base,
            end_at=base + timedelta(minutes=5),
            max_source_rows=10,
        ),
    )

    assert db_session.scalar(select(func.count()).select_from(VehicleObservation)) == source_count
    assert census.candidate_observations == 3
    assert census.accepted_labels == expected_labels
    assert len(labels) == expected_labels
    if expected_rejection:
        assert census.rejected[expected_rejection] == 1
    else:
        assert labels[0].service_day.isoformat() == "2026-09-09"
        assert labels[0].traversal.travel_seconds == 60
