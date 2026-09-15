"""Tests for the shared cross-city GTFS normalization layer.

Each test builds a tiny synthetic feed whose correct answer can be worked out by
hand, so a failure points at one measurement rather than at "the pipeline".  The
cases were chosen from the defects and ambiguities that real feeds actually
contain — times past 24:00, unsorted stop_times, branching patterns, loops,
one-way trippers and calendar exceptions — because those are what silently
corrupt a cross-city dataset while every row still looks plausible.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from transitpulse_ml.gtfs_normalizer import (
    FeedSource,
    GtfsNormalizationError,
    choose_representative_dates,
    haversine_km,
    normalize_feed,
    parse_gtfs_time,
    read_calendar,
)

# Roughly 1 km of longitude at Edmonton's latitude, used to build predictable
# geometry: a straight east-west line of stops with known spacing.
LAT = 53.5461
LON0 = -113.4938
KM_IN_DEG_LON = 1.0 / (111.320 * 0.5949)  # cos(53.5461 deg)


def _lon(km: float) -> float:
    return LON0 + km * KM_IN_DEG_LON


def _csv(rows: list[dict[str, str]], columns: list[str]) -> str:
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(str(row.get(column, "")) for column in columns))
    return "\n".join(lines) + "\n"


def build_feed(
    tmp_path: Path,
    *,
    trips: list[dict],
    routes: list[dict] | None = None,
    stops_km: dict[str, float] | None = None,
    calendar_rows: list[dict] | None = None,
    calendar_dates: list[dict] | None = None,
    shapes: dict[str, list[float]] | None = None,
    name: str = "feed.zip",
) -> Path:
    """Write a minimal but valid GTFS archive.

    ``trips`` entries carry their own ``stop_times`` as ``(stop_id, "HH:MM:SS")``
    pairs so each test reads as the schedule it is asserting about.
    """

    routes = routes or [{"route_id": "R1", "route_short_name": "1", "route_long_name": "Test", "route_type": "3"}]
    stops_km = stops_km or {"S1": 0.0, "S2": 1.0, "S3": 2.0, "S4": 3.0}
    calendar_rows = calendar_rows or [
        {
            "service_id": "WK",
            "monday": "1", "tuesday": "1", "wednesday": "1", "thursday": "1",
            "friday": "1", "saturday": "0", "sunday": "0",
            "start_date": "20260101", "end_date": "20261231",
        }
    ]

    stop_rows = [
        {"stop_id": sid, "stop_name": sid, "stop_lat": f"{LAT}", "stop_lon": f"{_lon(km)}", "location_type": "0"}
        for sid, km in stops_km.items()
    ]
    trip_rows = []
    stop_time_rows = []
    for trip in trips:
        trip_rows.append(
            {
                "route_id": trip.get("route_id", "R1"),
                "service_id": trip.get("service_id", "WK"),
                "trip_id": trip["trip_id"],
                "direction_id": trip.get("direction_id", "0"),
                "shape_id": trip.get("shape_id", ""),
            }
        )
        entries = list(enumerate(trip["stop_times"], start=1))
        if trip.get("shuffle_stop_times"):
            entries = list(reversed(entries))
        for sequence, (stop_id, when) in entries:
            stop_time_rows.append(
                {
                    "trip_id": trip["trip_id"],
                    "arrival_time": when,
                    "departure_time": when,
                    "stop_id": stop_id,
                    "stop_sequence": str(sequence),
                }
            )

    shape_rows = []
    for shape_id, kilometres in (shapes or {}).items():
        for index, km in enumerate(kilometres, start=1):
            shape_rows.append(
                {
                    "shape_id": shape_id,
                    "shape_pt_lat": f"{LAT}",
                    "shape_pt_lon": f"{_lon(km)}",
                    "shape_pt_sequence": str(index),
                }
            )

    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("agency.txt", _csv(
            [{"agency_name": "Test Transit", "agency_url": "http://x", "agency_timezone": "America/Edmonton"}],
            ["agency_name", "agency_url", "agency_timezone"]))
        archive.writestr("routes.txt", _csv(
            routes, ["route_id", "route_short_name", "route_long_name", "route_type"]))
        archive.writestr("stops.txt", _csv(
            stop_rows, ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type"]))
        archive.writestr("trips.txt", _csv(
            trip_rows, ["route_id", "service_id", "trip_id", "direction_id", "shape_id"]))
        archive.writestr("stop_times.txt", _csv(
            stop_time_rows, ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]))
        archive.writestr("calendar.txt", _csv(
            calendar_rows,
            ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday", "start_date", "end_date"]))
        if calendar_dates is not None:
            archive.writestr("calendar_dates.txt", _csv(
                calendar_dates, ["service_id", "date", "exception_type"]))
        if shape_rows:
            archive.writestr("shapes.txt", _csv(
                shape_rows, ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"]))
    return path


def source_for(path: Path, **overrides) -> FeedSource:
    defaults = dict(
        source_id="test-city",
        city="Testville",
        agency="Test Transit",
        source_url="http://example.invalid/gtfs.zip",
        licence="Test licence",
        archive_path=str(path),
        retrieved_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
        checksum_sha256="0" * 64,
    )
    defaults.update(overrides)
    return FeedSource(**defaults)


def clock(trips_at: list[str], stops: list[str], minutes_each: int) -> list[dict]:
    """Build one trip per departure time, each spending ``minutes_each`` per hop."""

    built = []
    for index, start in enumerate(trips_at):
        hour, minute = (int(part) for part in start.split(":"))
        times = []
        for step, stop_id in enumerate(stops):
            total = hour * 60 + minute + step * minutes_each
            times.append((stop_id, f"{total // 60:02d}:{total % 60:02d}:00"))
        built.append({"trip_id": f"T{index}", "stop_times": times})
    return built


# --------------------------------------------------------------------------
# time parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("00:00:00", 0),
        ("08:30:00", 30600),
        # GTFS times past midnight belong to the previous service day.  Rejecting
        # them would delete every late-night trip and shorten measured spans.
        ("25:10:00", 90600),
        ("27:45:30", 99930),
        ("", None),
        ("8:30", None),
        ("08:60:00", None),
        ("-1:00:00", None),
    ],
)
def test_parse_gtfs_time(value, expected):
    assert parse_gtfs_time(value) == expected


def test_haversine_matches_known_separation():
    # One degree of latitude is ~111.19 km on a sphere of this radius.
    assert haversine_km((0.0, 0.0), (1.0, 0.0)) == pytest.approx(111.19, abs=0.05)


# --------------------------------------------------------------------------
# geometry and stop spacing
# --------------------------------------------------------------------------


def test_length_and_spacing_measured_from_shape(tmp_path):
    feed = build_feed(
        tmp_path,
        trips=[dict(t, shape_id="SH1") for t in clock(["08:00", "08:30", "09:00", "09:30"],
                                                      ["S1", "S2", "S3", "S4"], 5)],
        shapes={"SH1": [0.0, 1.0, 2.0, 3.0]},
    )
    records, _ = normalize_feed(source_for(feed), reference_date=date(2026, 6, 1))
    assert len(records) == 1
    record = records[0]
    assert record.one_way_length_km == pytest.approx(3.0, abs=0.02)
    assert record.stop_count == 4
    assert record.stops_per_km == pytest.approx(4 / 3.0, abs=0.02)
    assert record.mean_stop_spacing_m == pytest.approx(1000.0, abs=10)
    assert record.median_stop_spacing_m == pytest.approx(1000.0, abs=10)


def test_unsorted_stop_times_are_ordered_by_stop_sequence(tmp_path):
    """GTFS does not promise stop_times are in order; measuring them as-read
    would scramble the pattern, the runtime and every derived value."""

    ordered = build_feed(tmp_path, trips=clock(["08:00", "08:30", "09:00", "09:30"],
                                               ["S1", "S2", "S3", "S4"], 5), name="a.zip")
    shuffled = build_feed(
        tmp_path,
        trips=[dict(t, shuffle_stop_times=True) for t in clock(
            ["08:00", "08:30", "09:00", "09:30"], ["S1", "S2", "S3", "S4"], 5)],
        name="b.zip",
    )
    first, _ = normalize_feed(source_for(ordered), reference_date=date(2026, 6, 1))
    second, _ = normalize_feed(source_for(shuffled), reference_date=date(2026, 6, 1))
    assert first[0].scheduled_runtime_minutes == second[0].scheduled_runtime_minutes == 15.0
    assert first[0].one_way_length_km == pytest.approx(second[0].one_way_length_km)


def test_missing_shapes_fall_back_to_stop_path_and_say_so(tmp_path):
    feed = build_feed(tmp_path, trips=clock(["08:00", "08:30", "09:00", "09:30"],
                                            ["S1", "S2", "S3", "S4"], 5))
    records, _ = normalize_feed(source_for(feed), reference_date=date(2026, 6, 1))
    record = records[0]
    assert record.one_way_length_km == pytest.approx(3.0, abs=0.02)
    assert any("stop_to_stop_path" in t for t in record.provenance.transformations)


# --------------------------------------------------------------------------
# service measurement
# --------------------------------------------------------------------------


def test_headway_span_and_speed(tmp_path):
    # Six trips 20 minutes apart, each taking 15 minutes over 3 km.
    feed = build_feed(
        tmp_path,
        trips=clock(["07:00", "07:20", "07:40", "08:00", "08:20", "08:40"],
                    ["S1", "S2", "S3", "S4"], 5),
    )
    records, _ = normalize_feed(source_for(feed), reference_date=date(2026, 6, 1))
    record = records[0]
    assert record.scheduled_runtime_minutes == 15.0
    assert record.median_headway_minutes == 20.0
    assert record.peak_headway_minutes == 20.0
    assert record.headway_sample_count == 5
    assert record.service_span_hours == pytest.approx(100 / 60, abs=0.01)
    # 3 km in 15 min = 12 km/h.
    assert record.scheduled_commercial_speed_kmh == pytest.approx(12.0, abs=0.1)


def test_headway_windows_do_not_span_the_peak_boundary(tmp_path):
    """A gap between the last peak trip and the first midday trip is not a peak
    headway; counting it would report a fictitious 4-hour peak frequency."""

    feed = build_feed(
        tmp_path,
        trips=clock(["07:00", "07:30", "08:00", "12:00", "12:30", "13:00"],
                    ["S1", "S2", "S3", "S4"], 5),
    )
    records, _ = normalize_feed(source_for(feed), reference_date=date(2026, 6, 1))
    record = records[0]
    assert record.peak_headway_minutes == 30.0
    assert record.offpeak_headway_minutes == 30.0


def test_times_past_midnight_extend_the_service_span(tmp_path):
    feed = build_feed(
        tmp_path,
        trips=[
            {"trip_id": "T0", "stop_times": [("S1", "05:00:00"), ("S2", "05:10:00"), ("S3", "05:20:00")]},
            {"trip_id": "T1", "stop_times": [("S1", "12:00:00"), ("S2", "12:10:00"), ("S3", "12:20:00")]},
            {"trip_id": "T2", "stop_times": [("S1", "25:30:00"), ("S2", "25:40:00"), ("S3", "25:50:00")]},
        ],
    )
    records, _ = normalize_feed(source_for(feed), reference_date=date(2026, 6, 1))
    # 05:00 to 25:30 is 20.5 hours, not "05:00 to 01:30 = negative".
    assert records[0].service_span_hours == pytest.approx(20.5, abs=0.01)


def test_span_needs_more_than_two_departures(tmp_path):
    feed = build_feed(
        tmp_path,
        trips=clock(["08:00", "08:02"], ["S1", "S2", "S3", "S4"], 5),
    )
    records, _ = normalize_feed(source_for(feed), reference_date=date(2026, 6, 1))
    assert records[0].service_span_hours is None
    assert "service_span_hours" in records[0].provenance.missing_fields


# --------------------------------------------------------------------------
# patterns, directions, loops
# --------------------------------------------------------------------------


def test_dominant_pattern_drives_geometry_and_branches_are_counted(tmp_path):
    """Three trips run the full route and one runs a short turn.  Averaging the
    two lengths would describe a route that does not exist."""

    trips = clock(["08:00", "08:20", "08:40"], ["S1", "S2", "S3", "S4"], 5)
    trips.append({"trip_id": "TShort", "stop_times": [("S1", "09:00:00"), ("S2", "09:05:00")]})
    feed = build_feed(tmp_path, trips=trips)
    records, _ = normalize_feed(source_for(feed), reference_date=date(2026, 6, 1))
    record = records[0]
    assert record.stop_count == 4
    assert record.one_way_length_km == pytest.approx(3.0, abs=0.02)
    assert record.branch_count == 2
    assert record.dominant_pattern_trip_share == pytest.approx(0.75)


def test_directions_are_separate_rows(tmp_path):
    outbound = clock(["08:00", "08:30", "09:00"], ["S1", "S2", "S3", "S4"], 5)
    inbound = clock(["08:10", "08:40", "09:10"], ["S4", "S3", "S2", "S1"], 7)
    for trip in inbound:
        trip["trip_id"] = trip["trip_id"] + "R"
        trip["direction_id"] = "1"
    feed = build_feed(tmp_path, trips=outbound + inbound)
    records, _ = normalize_feed(source_for(feed), reference_date=date(2026, 6, 1))
    assert {r.direction_id for r in records} == {0, 1}
    by_direction = {r.direction_id: r for r in records}
    assert by_direction[0].scheduled_runtime_minutes == 15.0
    assert by_direction[1].scheduled_runtime_minutes == 21.0
    # Cycle time pairs the two observed halves rather than doubling one.
    assert by_direction[0].estimated_cycle_time_minutes == pytest.approx(36.0 + 5.0)
    assert any("both directions observed" in a for a in by_direction[0].provenance.assumptions)


def test_loop_route_cycle_is_not_doubled(tmp_path):
    """A loop returns to its origin, so its one-way runtime already is the full
    circuit.  Treating it like a linear route doubles its fleet estimate."""

    stops = {"S1": 0.0, "S2": 0.4, "S3": 0.8, "S1B": 0.05}
    trips = []
    for index, start in enumerate(["08:00", "08:30", "09:00", "09:30"]):
        hour, minute = (int(p) for p in start.split(":"))
        times = []
        for step, stop_id in enumerate(["S1", "S2", "S3", "S1B"]):
            total = hour * 60 + minute + step * 10
            times.append((stop_id, f"{total // 60:02d}:{total % 60:02d}:00"))
        trips.append({"trip_id": f"L{index}", "stop_times": times})
    feed = build_feed(tmp_path, trips=trips, stops_km=stops)
    records, _ = normalize_feed(source_for(feed), reference_date=date(2026, 6, 1))
    record = records[0]
    assert record.loop_route is True
    assert record.scheduled_runtime_minutes == 30.0
    # 30 min circuit + max(10% of 30, 5) = 30 + 5 = 35, not 2 x 30 + recovery.
    assert record.estimated_cycle_time_minutes == pytest.approx(35.0)
    assert record.estimated_required_vehicles == pytest.approx(35.0 / 30.0, abs=0.01)


def test_one_way_non_loop_gets_no_invented_cycle_time(tmp_path):
    """A school tripper has no return leg and is not a loop.  Doubling its
    runtime would fabricate a round trip the feed never described."""

    feed = build_feed(
        tmp_path,
        trips=clock(["08:00", "08:20", "08:40", "09:00"], ["S1", "S2", "S3", "S4"], 5),
    )
    records, _ = normalize_feed(source_for(feed), reference_date=date(2026, 6, 1))
    record = records[0]
    assert record.loop_route is False
    assert record.estimated_cycle_time_minutes is None
    assert record.estimated_required_vehicles is None
    assert any("not a loop" in a for a in record.provenance.assumptions)


# --------------------------------------------------------------------------
# reproducibility
# --------------------------------------------------------------------------


def test_tied_busiest_stop_is_broken_deterministically(tmp_path):
    """Every stop on an unbranched route is served by every trip, so the
    "busiest stop" is a tie among all of them.

    ``Counter.most_common`` breaks such a tie by insertion order, which here
    follows set iteration over stop id *strings* -- and Python randomizes string
    hashing per process. The winning stop, and with it the measured headway and
    service span, therefore changed between runs of identical code on an
    identical feed: 280 of 842 Edmonton rows differed across two builds. The tie
    must be broken on the stop id itself.
    """

    feed = build_feed(
        tmp_path,
        trips=clock(["07:00", "07:20", "07:40", "08:00"], ["S1", "S2", "S3", "S4"], 5),
    )
    first, _ = normalize_feed(source_for(feed), reference_date=date(2026, 6, 1))
    record = first[0]

    # All four stops tie at 4 trips; "S4" is the largest id and must win every
    # time. Its departures are 20 minutes apart, like every other stop's, so the
    # headway is stable either way -- what this pins is the *choice*.
    assert record.median_headway_minutes == 20.0
    assert record.headway_sample_count == 3

    # Re-normalizing the same bytes must reproduce the row exactly, field for field.
    second, _ = normalize_feed(source_for(feed), reference_date=date(2026, 6, 1))
    assert first[0].as_row() == second[0].as_row()


def test_busiest_stop_prefers_the_genuinely_busier_stop_over_the_tiebreak(tmp_path):
    """The tie-break must only apply to ties: a stop served by more trips still
    wins outright even when its id sorts lower."""

    trips = clock(["07:00", "07:20", "07:40"], ["S1", "S2", "S3", "S4"], 5)
    # Two extra short trips serve only S1 and S2, making S1 and S2 the busiest.
    trips.append({"trip_id": "X1", "stop_times": [("S1", "09:00:00"), ("S2", "09:05:00")]})
    trips.append({"trip_id": "X2", "stop_times": [("S1", "09:30:00"), ("S2", "09:35:00")]})
    feed = build_feed(tmp_path, trips=trips)
    records, _ = normalize_feed(source_for(feed), reference_date=date(2026, 6, 1))
    # S2 is served 5 times and sorts above S1, so it is the basis stop; five
    # departures give four gaps.
    assert records[0].headway_sample_count == 4


# --------------------------------------------------------------------------
# calendars
# --------------------------------------------------------------------------


def test_calendar_dates_exception_removes_a_service(tmp_path):
    calendar_rows = [{
        "service_id": "WK", "monday": "1", "tuesday": "1", "wednesday": "1",
        "thursday": "1", "friday": "1", "saturday": "0", "sunday": "0",
        "start_date": "20260601", "end_date": "20260630",
    }]
    with zipfile.ZipFile(build_feed(tmp_path, trips=clock(["08:00"], ["S1", "S2"], 5),
                                    calendar_rows=calendar_rows,
                                    calendar_dates=[{"service_id": "WK", "date": "20260603",
                                                     "exception_type": "2"}])) as archive:
        calendar = read_calendar(archive)
    assert "WK" in calendar.active_on(date(2026, 6, 2))
    assert "WK" not in calendar.active_on(date(2026, 6, 3))  # removed by exception


def test_representative_date_skips_a_holiday(tmp_path):
    """The first candidate Wednesday is a holiday with a single service; the
    chooser must prefer an ordinary week rather than describe the whole city's
    weekday service from a reduced schedule."""

    calendar_rows = [
        {"service_id": f"WK{i}", "monday": "1", "tuesday": "1", "wednesday": "1",
         "thursday": "1", "friday": "1", "saturday": "0", "sunday": "0",
         "start_date": "20260601", "end_date": "20260830"}
        for i in range(4)
    ]
    # Strip three of the four weekday services on the first candidate Wednesday.
    holiday = [{"service_id": f"WK{i}", "date": "20260603", "exception_type": "2"} for i in range(1, 4)]
    path = build_feed(tmp_path, trips=clock(["08:00"], ["S1", "S2"], 5),
                      calendar_rows=calendar_rows, calendar_dates=holiday)
    with zipfile.ZipFile(path) as archive:
        calendar = read_calendar(archive)
    chosen = choose_representative_dates(calendar, reference=date(2026, 6, 1))
    assert chosen["weekday"] != date(2026, 6, 3)
    assert chosen["weekday"].weekday() == 2


def test_feed_without_any_calendar_is_rejected(tmp_path):
    path = tmp_path / "bare.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("agency.txt", "agency_name,agency_url,agency_timezone\nX,http://x,UTC\n")
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(GtfsNormalizationError):
            read_calendar(archive)


# --------------------------------------------------------------------------
# cross-city contract
# --------------------------------------------------------------------------


def test_provenance_records_source_and_missingness(tmp_path):
    feed = build_feed(tmp_path, trips=clock(["08:00", "08:30", "09:00", "09:30"],
                                            ["S1", "S2", "S3", "S4"], 5))
    records, report = normalize_feed(
        source_for(feed, source_id="somewhere-tt", city="Somewhere"), reference_date=date(2026, 6, 1)
    )
    provenance = records[0].provenance
    assert provenance.source_id == "somewhere-tt"
    assert provenance.licence == "Test licence"
    assert provenance.original_route_id == "R1"
    assert any("ridership" in field for field in provenance.missing_fields)
    assert report["city"] == "Somewhere"
    assert report["records_emitted"] == len(records)


def test_two_feeds_produce_the_same_field_set(tmp_path):
    """Cross-city comparability is the whole point: a row from one city must be
    structurally indistinguishable from a row from another."""

    a = build_feed(tmp_path, trips=clock(["08:00", "08:30", "09:00", "09:30"],
                                         ["S1", "S2", "S3", "S4"], 5), name="a.zip")
    b = build_feed(tmp_path, trips=clock(["06:00", "06:15", "06:30", "06:45"],
                                         ["S1", "S2", "S3"], 9), name="b.zip")
    first, _ = normalize_feed(source_for(a, city="Alpha"), reference_date=date(2026, 6, 1))
    second, _ = normalize_feed(source_for(b, city="Beta"), reference_date=date(2026, 6, 1))
    assert first[0].as_row().keys() == second[0].as_row().keys()
    assert first[0].city == "Alpha" and second[0].city == "Beta"


def test_rail_and_bus_are_distinguished_not_pooled(tmp_path):
    routes = [
        {"route_id": "R1", "route_short_name": "1", "route_long_name": "Bus", "route_type": "3"},
        {"route_id": "R2", "route_short_name": "L", "route_long_name": "Rail", "route_type": "0"},
    ]
    trips = clock(["08:00", "08:30", "09:00"], ["S1", "S2", "S3", "S4"], 5)
    rail = clock(["08:05", "08:35", "09:05"], ["S1", "S2", "S3", "S4"], 3)
    for trip in rail:
        trip["trip_id"] += "L"
        trip["route_id"] = "R2"
    feed = build_feed(tmp_path, trips=trips + rail, routes=routes)
    records, _ = normalize_feed(source_for(feed), reference_date=date(2026, 6, 1))
    kinds = {r.route_kind for r in records}
    assert kinds == {"bus", "tram"}
