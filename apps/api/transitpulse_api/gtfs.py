"""Small, transactional importer for the static GTFS subset TransitPulse uses."""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable
from uuid import UUID

from geoalchemy2.elements import WKTElement
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from transitpulse_api.models import (
    Agency,
    CalendarDate,
    GtfsFeed,
    Route,
    ServiceCalendar,
    Shape,
    Stop,
    StopTime,
    Trip,
)


REQUIRED_FILES = {"agency.txt", "routes.txt", "stops.txt", "trips.txt", "stop_times.txt", "shapes.txt"}
REQUIRED_COLUMNS = {
    "agency.txt": {"agency_name", "agency_url", "agency_timezone"},
    "routes.txt": {"route_id", "route_type"},
    "stops.txt": {"stop_id", "stop_name", "stop_lat", "stop_lon"},
    "trips.txt": {"route_id", "service_id", "trip_id"},
    "stop_times.txt": {"trip_id", "stop_id", "stop_sequence"},
    "shapes.txt": {"shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"},
    "calendar.txt": {
        "service_id",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "start_date",
        "end_date",
    },
    "calendar_dates.txt": {"service_id", "date", "exception_type"},
}


class GtfsImportError(ValueError):
    """A source feed could not be validated or represented safely."""


@dataclass(frozen=True)
class ParsedGtfsFeed:
    tables: dict[str, list[dict[str, str]]]
    start_date: date | None
    end_date: date | None


@dataclass(frozen=True)
class ImportResult:
    feed_id: UUID
    duplicate: bool
    counts: dict[str, int]
    checksum_sha256: str


def parse_gtfs_zip(archive: bytes) -> ParsedGtfsFeed:
    """Read all required GTFS tables before any database write begins."""

    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            members = {name.rsplit("/", 1)[-1].lower(): name for name in zipped.namelist() if not name.endswith("/")}
            missing = REQUIRED_FILES - members.keys()
            if missing:
                raise GtfsImportError(f"GTFS archive is missing required files: {', '.join(sorted(missing))}")
            if "calendar.txt" not in members and "calendar_dates.txt" not in members:
                raise GtfsImportError("GTFS archive requires calendar.txt or calendar_dates.txt")

            tables: dict[str, list[dict[str, str]]] = {}
            for table_name, required_columns in REQUIRED_COLUMNS.items():
                if table_name not in members:
                    continue
                payload = zipped.read(members[table_name]).decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(payload))
                if reader.fieldnames is None:
                    raise GtfsImportError(f"{table_name} has no header row")
                missing_columns = required_columns - set(reader.fieldnames)
                if missing_columns:
                    raise GtfsImportError(
                        f"{table_name} is missing columns: {', '.join(sorted(missing_columns))}"
                    )
                tables[table_name] = [
                    {key: (value or "").strip() for key, value in row.items()}
                    for row in reader
                ]
    except zipfile.BadZipFile as error:
        raise GtfsImportError("Source is not a valid GTFS ZIP archive") from error
    except UnicodeDecodeError as error:
        raise GtfsImportError("GTFS files must be UTF-8 encoded") from error

    _validate_references(tables)
    dates = [
        _parse_date(row["start_date"], "calendar start_date")
        for row in tables.get("calendar.txt", [])
    ] + [
        _parse_date(row["end_date"], "calendar end_date")
        for row in tables.get("calendar.txt", [])
    ] + [
        _parse_date(row["date"], "calendar_dates date")
        for row in tables.get("calendar_dates.txt", [])
    ]
    return ParsedGtfsFeed(tables=tables, start_date=min(dates, default=None), end_date=max(dates, default=None))


def import_gtfs_zip(
    session: Session,
    archive: bytes,
    *,
    provider: str,
    source_url: str,
    downloaded_at: datetime | None = None,
) -> ImportResult:
    """Store one complete GTFS version, or no domain rows if any step fails."""

    parsed = parse_gtfs_zip(archive)
    checksum = hashlib.sha256(archive).hexdigest()
    existing = session.scalar(
        select(GtfsFeed).where(GtfsFeed.checksum_sha256 == checksum)
    )
    if existing is not None:
        return ImportResult(
            feed_id=existing.id,
            duplicate=True,
            counts=dict(existing.import_counts),
            checksum_sha256=checksum,
        )

    imported_at = downloaded_at or datetime.now(timezone.utc)
    # The identity lookup above opens SQLAlchemy's implicit read transaction.
    # Import ownership is deliberately self-contained, so start its write transaction cleanly.
    session.rollback()
    try:
        with session.begin():
            feed = GtfsFeed(
                provider=provider,
                source_url=source_url,
                checksum_sha256=checksum,
                downloaded_at=imported_at,
                feed_start_date=parsed.start_date,
                feed_end_date=parsed.end_date,
                import_status="importing",
                import_counts={},
            )
            session.add(feed)
            session.flush()
            counts = _persist_feed(session, feed, parsed)
            feed.import_status = "succeeded"
            feed.import_counts = counts
            session.flush()
            feed_id = feed.id
    except Exception:
        session.rollback()
        raise

    return ImportResult(feed_id=feed_id, duplicate=False, counts=counts, checksum_sha256=checksum)


def _persist_feed(session: Session, feed: GtfsFeed, parsed: ParsedGtfsFeed) -> dict[str, int]:
    tables = parsed.tables
    agencies = _add_agencies(session, feed, tables["agency.txt"])
    calendars = _add_calendars(session, feed, tables.get("calendar.txt", []), tables.get("calendar_dates.txt", []))
    stops = _add_stops(session, feed, tables["stops.txt"])
    shapes = _add_shapes(session, feed, tables["shapes.txt"])
    routes = _add_routes(session, feed, tables["routes.txt"], agencies)
    trips = _add_trips(session, feed, tables["trips.txt"], routes, calendars, shapes)
    stop_time_count = _add_stop_times(session, tables["stop_times.txt"], trips, stops)
    return {
        "agencies": len(agencies),
        "routes": len(routes),
        "stops": len(stops),
        "trips": len(trips),
        "stop_times": stop_time_count,
        "calendar": len(calendars),
        "calendar_dates": len(tables.get("calendar_dates.txt", [])),
        "shapes": len(shapes),
    }


def _add_agencies(session: Session, feed: GtfsFeed, rows: list[dict[str, str]]) -> dict[str, Agency]:
    agencies: dict[str, Agency] = {}
    for index, row in enumerate(rows):
        agency_id = _optional(row.get("agency_id")) or f"__single_agency_{index}"
        if agency_id in agencies:
            raise GtfsImportError(f"Duplicate agency_id {agency_id!r}")
        agency = Agency(
            feed_id=feed.id,
            gtfs_agency_id=agency_id,
            name=_required(row, "agency_name"),
            url=_required(row, "agency_url"),
            timezone=_required(row, "agency_timezone"),
            lang=_optional(row.get("agency_lang")),
            phone=_optional(row.get("agency_phone")),
            fare_url=_optional(row.get("agency_fare_url")),
            email=_optional(row.get("agency_email")),
        )
        agencies[agency_id] = agency
        session.add(agency)
    if not agencies:
        raise GtfsImportError("agency.txt contains no agencies")
    session.flush()
    return agencies


def _add_calendars(
    session: Session,
    feed: GtfsFeed,
    calendar_rows: list[dict[str, str]],
    date_rows: list[dict[str, str]],
) -> dict[str, ServiceCalendar]:
    calendars: dict[str, ServiceCalendar] = {}
    for row in calendar_rows:
        service_id = _required(row, "service_id")
        if service_id in calendars:
            raise GtfsImportError(f"Duplicate calendar service_id {service_id!r}")
        calendar = ServiceCalendar(
            feed_id=feed.id,
            gtfs_service_id=service_id,
            monday=_parse_bool(row["monday"], "calendar monday"),
            tuesday=_parse_bool(row["tuesday"], "calendar tuesday"),
            wednesday=_parse_bool(row["wednesday"], "calendar wednesday"),
            thursday=_parse_bool(row["thursday"], "calendar thursday"),
            friday=_parse_bool(row["friday"], "calendar friday"),
            saturday=_parse_bool(row["saturday"], "calendar saturday"),
            sunday=_parse_bool(row["sunday"], "calendar sunday"),
            start_date=_parse_date(row["start_date"], "calendar start_date"),
            end_date=_parse_date(row["end_date"], "calendar end_date"),
        )
        calendars[service_id] = calendar
        session.add(calendar)
    for row in date_rows:
        service_id = _required(row, "service_id")
        if service_id not in calendars:
            calendar = ServiceCalendar(feed_id=feed.id, gtfs_service_id=service_id)
            calendars[service_id] = calendar
            session.add(calendar)
    session.flush()
    for row in date_rows:
        session.add(
            CalendarDate(
                service_id=calendars[_required(row, "service_id")].id,
                date=_parse_date(row["date"], "calendar_dates date"),
                exception_type=_parse_exception_type(row["exception_type"]),
            )
        )
    return calendars


def _add_stops(session: Session, feed: GtfsFeed, rows: list[dict[str, str]]) -> dict[str, Stop]:
    stops: dict[str, Stop] = {}
    for row in rows:
        stop_id = _required(row, "stop_id")
        if stop_id in stops:
            raise GtfsImportError(f"Duplicate stop_id {stop_id!r}")
        longitude = _parse_coordinate(row["stop_lon"], "stop_lon", -180, 180)
        latitude = _parse_coordinate(row["stop_lat"], "stop_lat", -90, 90)
        stop = Stop(
            feed_id=feed.id,
            gtfs_stop_id=stop_id,
            code=_optional(row.get("stop_code")),
            name=_required(row, "stop_name"),
            description=_optional(row.get("stop_desc")),
            location_type=_parse_int(row.get("location_type"), "location_type", default=0),
            parent_station_gtfs_id=_optional(row.get("parent_station")),
            timezone=_optional(row.get("stop_timezone")),
            wheelchair_boarding=_parse_optional_int(row.get("wheelchair_boarding"), "wheelchair_boarding"),
            location=WKTElement(f"POINT({longitude} {latitude})", srid=4326),
        )
        stops[stop_id] = stop
        session.add(stop)
    session.flush()
    return stops


def _add_shapes(session: Session, feed: GtfsFeed, rows: list[dict[str, str]]) -> dict[str, Shape]:
    points: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for row in rows:
        shape_id = _required(row, "shape_id")
        points[shape_id].append(
            (
                _parse_int(row["shape_pt_sequence"], "shape_pt_sequence"),
                _parse_coordinate(row["shape_pt_lon"], "shape_pt_lon", -180, 180),
                _parse_coordinate(row["shape_pt_lat"], "shape_pt_lat", -90, 90),
            )
        )
    shapes: dict[str, Shape] = {}
    for shape_id, shape_points in points.items():
        ordered = sorted(shape_points)
        if len(ordered) < 2:
            raise GtfsImportError(f"Shape {shape_id!r} must contain at least two points")
        coordinates = ", ".join(f"{longitude} {latitude}" for _, longitude, latitude in ordered)
        shape = Shape(
            feed_id=feed.id,
            gtfs_shape_id=shape_id,
            geometry=WKTElement(f"LINESTRING({coordinates})", srid=4326),
        )
        shapes[shape_id] = shape
        session.add(shape)
    session.flush()
    return shapes


def _add_routes(
    session: Session, feed: GtfsFeed, rows: list[dict[str, str]], agencies: dict[str, Agency]
) -> dict[str, Route]:
    routes: dict[str, Route] = {}
    for row in rows:
        route_id = _required(row, "route_id")
        if route_id in routes:
            raise GtfsImportError(f"Duplicate route_id {route_id!r}")
        agency_id = _optional(row.get("agency_id"))
        if agency_id is None:
            if len(agencies) != 1:
                raise GtfsImportError(f"Route {route_id!r} lacks agency_id in a multi-agency feed")
            agency = next(iter(agencies.values()))
        else:
            agency = agencies.get(agency_id)
            if agency is None:
                raise GtfsImportError(f"Route {route_id!r} references unknown agency_id {agency_id!r}")
        route = Route(
            feed_id=feed.id,
            agency_id=agency.id,
            gtfs_route_id=route_id,
            short_name=_optional(row.get("route_short_name")),
            long_name=_optional(row.get("route_long_name")),
            description=_optional(row.get("route_desc")),
            route_type=_parse_int(row["route_type"], "route_type"),
            url=_optional(row.get("route_url")),
            color=_normalise_color(_optional(row.get("route_color"))),
            text_color=_normalise_color(_optional(row.get("route_text_color"))),
            sort_order=_parse_optional_int(row.get("route_sort_order"), "route_sort_order"),
        )
        routes[route_id] = route
        session.add(route)
    session.flush()
    return routes


def _add_trips(
    session: Session,
    feed: GtfsFeed,
    rows: list[dict[str, str]],
    routes: dict[str, Route],
    calendars: dict[str, ServiceCalendar],
    shapes: dict[str, Shape],
) -> dict[str, int]:
    """Insert trips in chunks and retain only the source-to-internal ID lookup."""

    trip_ids: dict[str, int] = {}
    batch: list[dict[str, object]] = []
    pending_trip_ids: set[str] = set()
    for row in rows:
        trip_id = _required(row, "trip_id")
        route_id = _required(row, "route_id")
        service_id = _required(row, "service_id")
        if trip_id in trip_ids or trip_id in pending_trip_ids:
            raise GtfsImportError(f"Duplicate trip_id {trip_id!r}")
        if route_id not in routes:
            raise GtfsImportError(f"Trip {trip_id!r} references unknown route_id {route_id!r}")
        if service_id not in calendars:
            raise GtfsImportError(f"Trip {trip_id!r} references unknown service_id {service_id!r}")
        shape_key = _optional(row.get("shape_id"))
        if shape_key is not None and shape_key not in shapes:
            raise GtfsImportError(f"Trip {trip_id!r} references unknown shape_id {shape_key!r}")
        batch.append(
            {
                "feed_id": feed.id,
                "route_id": routes[route_id].id,
                "service_id": calendars[service_id].id,
                "shape_id": shapes[shape_key].id if shape_key is not None else None,
                "gtfs_trip_id": trip_id,
                "headsign": _optional(row.get("trip_headsign")),
                "short_name": _optional(row.get("trip_short_name")),
                "direction_id": _parse_optional_int(row.get("direction_id"), "direction_id"),
                "block_id": _optional(row.get("block_id")),
                "wheelchair_accessible": _parse_optional_int(
                    row.get("wheelchair_accessible"), "wheelchair_accessible"
                ),
                "bikes_allowed": _parse_optional_int(row.get("bikes_allowed"), "bikes_allowed"),
            }
        )
        pending_trip_ids.add(trip_id)
        if len(batch) == 5_000:
            _insert_trips(session, batch, trip_ids)
            batch.clear()
            pending_trip_ids.clear()
    if batch:
        _insert_trips(session, batch, trip_ids)
    return trip_ids


def _add_stop_times(
    session: Session,
    rows: list[dict[str, str]],
    trips: dict[str, int],
    stops: dict[str, Stop],
) -> int:
    count = 0
    sequences: set[tuple[str, int]] = set()
    batch: list[dict[str, object]] = []
    for row in rows:
        trip_id = _required(row, "trip_id")
        stop_id = _required(row, "stop_id")
        if trip_id not in trips:
            raise GtfsImportError(f"stop_times references unknown trip_id {trip_id!r}")
        if stop_id not in stops:
            raise GtfsImportError(f"stop_times references unknown stop_id {stop_id!r}")
        sequence = _parse_int(row["stop_sequence"], "stop_sequence")
        if (trip_id, sequence) in sequences:
            raise GtfsImportError(f"Trip {trip_id!r} repeats stop_sequence {sequence}")
        sequences.add((trip_id, sequence))
        arrival = _optional(row.get("arrival_time"))
        departure = _optional(row.get("departure_time"))
        batch.append(
            {
                "trip_id": trips[trip_id],
                "stop_id": stops[stop_id].id,
                "arrival_time": arrival,
                "departure_time": departure,
                "arrival_seconds": _parse_gtfs_time(arrival, "arrival_time"),
                "departure_seconds": _parse_gtfs_time(departure, "departure_time"),
                "stop_sequence": sequence,
                "headsign": _optional(row.get("stop_headsign")),
                "pickup_type": _parse_optional_int(row.get("pickup_type"), "pickup_type"),
                "drop_off_type": _parse_optional_int(row.get("drop_off_type"), "drop_off_type"),
                "timepoint": _parse_optional_int(row.get("timepoint"), "timepoint"),
            }
        )
        count += 1
        if len(batch) == 10_000:
            session.execute(insert(StopTime), batch)
            batch.clear()
    if batch:
        session.execute(insert(StopTime), batch)
    return count


def _insert_trips(session: Session, batch: list[dict[str, object]], trip_ids: dict[str, int]) -> None:
    inserted = session.execute(
        insert(Trip).returning(Trip.id, Trip.gtfs_trip_id), batch
    )
    trip_ids.update({gtfs_trip_id: trip_id for trip_id, gtfs_trip_id in inserted})


def _validate_references(tables: dict[str, list[dict[str, str]]]) -> None:
    if not tables["agency.txt"]:
        raise GtfsImportError("agency.txt contains no rows")
    for name in REQUIRED_FILES:
        if not tables[name]:
            raise GtfsImportError(f"{name} contains no rows")


def _required(row: dict[str, str], key: str) -> str:
    value = _optional(row.get(key))
    if value is None:
        raise GtfsImportError(f"Missing required value {key}")
    return value


def _optional(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


def _parse_int(value: str | None, name: str, *, default: int | None = None) -> int:
    if _optional(value) is None:
        if default is not None:
            return default
        raise GtfsImportError(f"Missing integer {name}")
    try:
        return int(value)  # type: ignore[arg-type]
    except ValueError as error:
        raise GtfsImportError(f"Invalid integer {name}: {value!r}") from error


def _parse_optional_int(value: str | None, name: str) -> int | None:
    return None if _optional(value) is None else _parse_int(value, name)


def _parse_bool(value: str, name: str) -> bool:
    parsed = _parse_int(value, name)
    if parsed not in {0, 1}:
        raise GtfsImportError(f"{name} must be 0 or 1")
    return bool(parsed)


def _parse_exception_type(value: str) -> int:
    parsed = _parse_int(value, "exception_type")
    if parsed not in {1, 2}:
        raise GtfsImportError("exception_type must be 1 or 2")
    return parsed


def _parse_coordinate(value: str, name: str, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise GtfsImportError(f"Invalid {name}: {value!r}") from error
    if not minimum <= parsed <= maximum:
        raise GtfsImportError(f"{name} outside valid range: {value!r}")
    return parsed


def _parse_date(value: str, name: str) -> date:
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as error:
        raise GtfsImportError(f"Invalid {name}: {value!r}") from error


def _parse_gtfs_time(value: str | None, name: str) -> int | None:
    if value is None:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in value.split(":"))
    except ValueError as error:
        raise GtfsImportError(f"Invalid {name}: {value!r}") from error
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise GtfsImportError(f"Invalid {name}: {value!r}")
    return hours * 3600 + minutes * 60 + seconds


def _normalise_color(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.removeprefix("#").upper()
    if len(candidate) != 6 or any(character not in "0123456789ABCDEF" for character in candidate):
        raise GtfsImportError(f"Invalid GTFS route colour: {value!r}")
    return candidate
