from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from tests.fixtures.tiny_gtfs import make_gtfs_archive
from transitpulse_api.gtfs import GtfsImportError, import_gtfs_zip, parse_gtfs_zip
from transitpulse_api.models import GtfsFeed, Route, Shape, Stop, StopTime, Trip


def test_parser_reads_small_complete_fixture() -> None:
    parsed = parse_gtfs_zip(make_gtfs_archive())

    assert parsed.start_date.isoformat() == "2026-09-01"
    assert parsed.end_date.isoformat() == "2026-11-30"
    assert len(parsed.tables["stop_times.txt"]) == 2


def test_parser_rejects_missing_required_file() -> None:
    with pytest.raises(GtfsImportError, match="routes.txt"):
        parse_gtfs_zip(make_gtfs_archive(omit=["routes.txt"]))


def test_successful_import_keeps_relations_and_postgis_geometry(db_session: Session) -> None:
    result = import_gtfs_zip(
        db_session,
        make_gtfs_archive(),
        provider="Test provider",
        source_url="https://example.test/feed.zip",
        downloaded_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )

    assert result.duplicate is False
    assert result.counts == {
        "agencies": 1,
        "routes": 1,
        "stops": 2,
        "trips": 1,
        "stop_times": 2,
        "calendar": 1,
        "calendar_dates": 1,
        "shapes": 1,
    }
    assert db_session.scalar(select(func.count(Route.id))) == 1
    assert db_session.scalar(select(func.count(Trip.id))) == 1
    assert db_session.scalar(select(func.count(StopTime.id))) == 2
    assert db_session.scalar(select(func.ST_SRID(Stop.location))) == 4326
    assert db_session.scalar(select(func.ST_GeometryType(Shape.geometry))) == "ST_LineString"


def test_same_checksum_does_not_make_another_feed_version(db_session: Session) -> None:
    first = import_gtfs_zip(
        db_session, make_gtfs_archive(), provider="Test", source_url="https://example.test/feed.zip"
    )
    second = import_gtfs_zip(
        db_session, make_gtfs_archive(), provider="Test", source_url="https://example.test/feed.zip"
    )

    assert second.duplicate is True
    assert second.feed_id == first.feed_id
    assert db_session.scalar(select(func.count(GtfsFeed.id))) == 1


def test_invalid_relationship_rolls_back_entire_import(db_session: Session) -> None:
    invalid = make_gtfs_archive(
        replacements={
            "trips.txt": "route_id,service_id,trip_id,shape_id\nMISSING,WEEKDAY,TRIP_100,SHAPE_100\n"
        }
    )

    with pytest.raises(GtfsImportError, match="unknown route_id"):
        import_gtfs_zip(db_session, invalid, provider="Test", source_url="https://example.test/feed.zip")
    assert db_session.scalar(select(func.count(GtfsFeed.id))) == 0
    assert db_session.scalar(select(func.count(Stop.id))) == 0
