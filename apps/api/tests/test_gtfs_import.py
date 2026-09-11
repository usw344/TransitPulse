from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from tests.fixtures.tiny_gtfs import make_gtfs_archive
from transitpulse_api.gtfs import GtfsImportError, import_gtfs_zip, parse_gtfs_zip
from transitpulse_api.models import GtfsFeed, Route, Shape, Stop, StopTime, Trip


def _stops_with_internal_locations() -> str:
    return """stop_id,stop_code,stop_name,stop_lat,stop_lon,location_type,parent_station
STOP_A,1001,Alpha Stop,53.5461,-113.4938,0,
STOP_B,1002,Beta Stop,53.5500,-113.4900,0,
STATION,,Example Station,53.5465,-113.4935,1,
NODE,,,,,3,STATION
BOARDING,,,,,4,STOP_A
"""


def test_parser_reads_small_complete_fixture() -> None:
    parsed = parse_gtfs_zip(make_gtfs_archive())

    assert parsed.start_date.isoformat() == "2026-09-01"
    assert parsed.end_date.isoformat() == "2026-11-30"
    assert len(parsed.tables["stop_times.txt"]) == 2


def test_parser_rejects_missing_required_file() -> None:
    with pytest.raises(GtfsImportError, match="routes.txt"):
        parse_gtfs_zip(make_gtfs_archive(omit=["routes.txt"]))


def test_parser_accepts_blank_stop_name_and_coordinates_for_permitted_locations() -> None:
    parsed = parse_gtfs_zip(
        make_gtfs_archive(replacements={"stops.txt": _stops_with_internal_locations()})
    )

    rows = {row["stop_id"]: row for row in parsed.tables["stops.txt"]}
    assert rows["NODE"]["stop_name"] == ""
    assert rows["BOARDING"]["stop_name"] == ""


@pytest.mark.parametrize(
    ("location_type", "parent_station"),
    [("0", ""), ("1", ""), ("2", "STATION")],
)
def test_parser_rejects_blank_stop_name_where_gtfs_requires_it(
    location_type: str, parent_station: str
) -> None:
    stops = f"""stop_id,stop_code,stop_name,stop_lat,stop_lon,location_type,parent_station
STOP_A,1001,,53.5461,-113.4938,{location_type},{parent_station}
STOP_B,1002,Beta Stop,53.5500,-113.4900,0,
STATION,,Example Station,53.5465,-113.4935,1,
"""

    with pytest.raises(GtfsImportError, match=r"STOP_A.*stop_name"):
        parse_gtfs_zip(make_gtfs_archive(replacements={"stops.txt": stops}))


def test_parser_accepts_the_edmonton_generic_node_that_triggered_the_failure() -> None:
    # Exact relevant values from Edmonton stops.txt row 6258: E10 is an
    # unnamed generic node under station Q7004, not a passenger stop.
    stops = """stop_id,stop_code,stop_name,stop_lat,stop_lon,location_type,parent_station
STOP_A,1001,Alpha Stop,53.5461,-113.4938,0,
STOP_B,1002,Beta Stop,53.5500,-113.4900,0,
Q7004,,Corona Station,53.544250,-113.489100,1,
E10,,,53.544248,-113.489115,3,Q7004
"""

    parsed = parse_gtfs_zip(make_gtfs_archive(replacements={"stops.txt": stops}))
    assert next(row for row in parsed.tables["stops.txt"] if row["stop_id"] == "E10")["stop_name"] == ""


def test_parser_rejects_invalid_conditional_stop_fields() -> None:
    missing_parent = _stops_with_internal_locations().replace("NODE,,,,,3,STATION", "NODE,,,,,3,")
    with pytest.raises(GtfsImportError, match=r"NODE.*requires parent_station"):
        parse_gtfs_zip(make_gtfs_archive(replacements={"stops.txt": missing_parent}))

    missing_coordinates = _stops_with_internal_locations().replace(
        "STOP_A,1001,Alpha Stop,53.5461,-113.4938,0,", "STOP_A,1001,Alpha Stop,, ,0,"
    )
    with pytest.raises(GtfsImportError, match="stop_lat"):
        parse_gtfs_zip(make_gtfs_archive(replacements={"stops.txt": missing_coordinates}))


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


def test_import_preserves_optional_internal_stop_fields(db_session: Session) -> None:
    import_gtfs_zip(
        db_session,
        make_gtfs_archive(replacements={"stops.txt": _stops_with_internal_locations()}),
        provider="Test provider",
        source_url="https://example.test/feed.zip",
    )

    node = db_session.scalar(select(Stop).where(Stop.gtfs_stop_id == "NODE"))
    boarding = db_session.scalar(select(Stop).where(Stop.gtfs_stop_id == "BOARDING"))
    assert node is not None
    assert node.name == ""
    assert node.location is None
    assert boarding is not None
    assert boarding.name == ""
    assert boarding.location is None


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
