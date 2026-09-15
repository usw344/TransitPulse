"""Turn any conventional static GTFS feed into :class:`NormalizedRouteRecord` rows.

This is the shared path every adopted city goes through.  Adding an ordinary
GTFS agency should mean registering a :class:`FeedSource` — not writing another
parser — so all city-specific knowledge lives in that small declaration and the
measurement logic below stays identical across agencies.  Where an agency really
does differ (a missing ``direction_id``, a feed that ships no ``shapes.txt``),
the difference is handled here once and recorded on the row's provenance rather
than forked into a per-city module.

Why this does not reuse ``transitpulse_api.gtfs``: that importer materializes
every table as a list of dicts inside one database transaction, which is right
for a transactional import of one city but costs several gigabytes on a feed
whose ``stop_times.txt`` is 58 MB, and it is coupled to SQLAlchemy models this
package must not depend on.  The reader here streams the same files twice and
keeps only what a measurement needs.

Measurement decisions that a reviewer should check, all deliberate:

*Unit of analysis.*  One row is one ``(route, direction, day_type)``.  A route
direction usually operates several stop patterns — short turns, branches,
express variants — whose lengths differ by a factor of two.  Averaging them
produces a route that does not exist, so geometry and runtime describe the
**dominant pattern** (the one the most trips run) and
``dominant_pattern_trip_share`` states how much of the direction that pattern
actually is.  A consumer filtering to well-represented routes filters on it.

*Service intensity.*  Headway is measured at the **busiest stop** of the
direction — the trunk every branch shares — over *all* the direction's trips,
not just the dominant pattern's.  Measuring frequency on the dominant pattern
alone would report a 30-minute headway on a corridor where two branches
alternate every 15.

*Day type.*  Resolved by choosing a representative service date and asking the
calendar which services run that day, so ``calendar_dates.txt`` exceptions are
honoured instead of being approximated from weekday bitmaps.  Candidate dates
that look like holidays (unusually few active services) are skipped.

*Derived values are labelled.*  Cycle time, recovery and vehicle requirement are
planning estimates under stated assumptions, never measurements; each one
records its assumption string on the row's provenance.
"""

from __future__ import annotations

import csv
import io
import statistics
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import asin, cos, isfinite, radians, sin, sqrt
from typing import Iterable, Iterator, Sequence

from transitpulse_ml.route_schema import (
    DayType,
    NormalizedRouteRecord,
    RouteProvenance,
    estimate_required_vehicles,
    route_kind_from_gtfs,
)


EXTRACTOR_VERSION = "gtfs_normalizer/1.4.0"

#: Peak windows, in seconds from noon-minus-12h (GTFS time origin).  Chosen to
#: match conventional North-American service planning definitions; they are
#: recorded in provenance because a different agency convention would move the
#: peak/off-peak split and therefore the headway numbers.
AM_PEAK = (6 * 3600, 9 * 3600)
PM_PEAK = (15 * 3600, 18 * 3600)
MIDDAY = (9 * 3600, 15 * 3600)

#: Terminal recovery assumption used when no block data is available.  Real
#: schedules size recovery per route from observed running-time variability; a
#: fixed fraction with a floor is the standard planning approximation, and it is
#: stated as an assumption rather than implied to come from the feed.
RECOVERY_FRACTION_OF_ROUND_TRIP = 0.10
MIN_RECOVERY_MINUTES = 5.0

#: A direction needs at least this many trips before its headway means anything.
MIN_HEADWAY_GAPS = 3

#: A pattern is a loop when its terminals are closer than BOTH an absolute
#: distance and a fraction of its own length.  Either test alone misfires: a
#: pure ratio calls a 40 km route a loop when its ends are 3 km apart, while a
#: pure absolute distance calls every route shorter than ~2 km a loop
#: regardless of shape.  Requiring both leaves a genuinely circular shuttle
#: classified and a short straight route not.
LOOP_TERMINAL_SEPARATION_KM = 1.0
LOOP_TERMINAL_SEPARATION_FRACTION = 0.15

#: Departures needed before a service span is reported.  Two trips three hours
#: apart do not describe a span, and one pair 70 seconds apart describes
#: nothing at all.
MIN_SPAN_DEPARTURES = 3

_DAY_TYPE_WEEKDAY: dict[DayType, int] = {"weekday": 2, "saturday": 5, "sunday": 6}


class GtfsNormalizationError(ValueError):
    """A feed could not be normalized into comparable route records."""


@dataclass(frozen=True)
class FeedSource:
    """Everything city-specific about one adopted GTFS feed.

    Adding a city means adding one of these.  ``source_id`` keys the entry in
    ``docs/data_sources.md`` so a row can always be traced to the register.
    """

    source_id: str
    city: str
    agency: str
    source_url: str
    licence: str
    archive_path: str
    retrieved_at: datetime
    checksum_sha256: str
    #: Set when an agency publishes no usable ``direction_id``; rows then carry
    #: ``direction_id=None`` and cycle time cannot be derived from two halves.
    has_direction_id: bool = True


@dataclass(frozen=True)
class _Trip:
    trip_id: str
    route_id: str
    service_id: str
    direction_id: int | None
    shape_id: str | None


@dataclass(frozen=True)
class _TripTiming:
    """What one trip's stop_times reduce to, once read."""

    pattern: tuple[str, ...]
    first_departure_s: int
    last_arrival_s: int
    #: Departure second at each stop, positionally aligned with ``pattern``.
    departures_s: tuple[int, ...]


# --------------------------------------------------------------------------
# primitive readers
# --------------------------------------------------------------------------


def _rows(archive: zipfile.ZipFile, member: str) -> Iterator[dict[str, str]]:
    """Stream one GTFS table without holding the file in memory."""

    names = {name.rsplit("/", 1)[-1].lower(): name for name in archive.namelist()}
    if member not in names:
        return
    with archive.open(names[member]) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        for row in csv.DictReader(text):
            yield row


def _has(archive: zipfile.ZipFile, member: str) -> bool:
    return member in {name.rsplit("/", 1)[-1].lower() for name in archive.namelist()}


def parse_gtfs_time(value: str) -> int | None:
    """Seconds since the service day's midnight, or ``None`` if unusable.

    GTFS times legitimately exceed 24:00:00 for trips that run past midnight;
    treating ``25:10:00`` as invalid would silently drop every late-night trip
    and shorten measured service spans across the whole feed.
    """

    value = (value or "").strip()
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError:
        return None
    if minutes < 0 or minutes > 59 or seconds < 0 or seconds > 59 or hours < 0:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if len(value) != 8 or not value.isdigit():
        return None
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:]))
    except ValueError:
        return None


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in km between two ``(lat, lon)`` points."""

    lat1, lon1 = radians(a[0]), radians(a[1])
    lat2, lon2 = radians(b[0]), radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371.0088 * asin(sqrt(min(1.0, h)))


def polyline_length_km(points: Sequence[tuple[float, float]]) -> float:
    return sum(haversine_km(points[i], points[i + 1]) for i in range(len(points) - 1))


# --------------------------------------------------------------------------
# calendar resolution
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Calendar:
    """Service activity, resolved against real dates rather than weekday flags."""

    #: service_id -> set of weekday numbers it nominally runs.
    weekdays: dict[str, frozenset[int]]
    windows: dict[str, tuple[date | None, date | None]]
    #: (service_id, date) -> exception type (1 added, 2 removed).
    exceptions: dict[tuple[str, date], int]

    def active_on(self, service_day: date) -> set[str]:
        active: set[str] = set()
        for service_id, days in self.weekdays.items():
            start, end = self.windows.get(service_id, (None, None))
            if start is not None and service_day < start:
                continue
            if end is not None and service_day > end:
                continue
            if service_day.weekday() in days:
                active.add(service_id)
        for (service_id, day), exception in self.exceptions.items():
            if day != service_day:
                continue
            if exception == 1:
                active.add(service_id)
            elif exception == 2:
                active.discard(service_id)
        return active

    @property
    def span(self) -> tuple[date | None, date | None]:
        starts = [s for s, _ in self.windows.values() if s is not None]
        ends = [e for _, e in self.windows.values() if e is not None]
        exception_days = [day for _, day in self.exceptions]
        starts += exception_days
        ends += exception_days
        return (min(starts) if starts else None, max(ends) if ends else None)


def read_calendar(archive: zipfile.ZipFile) -> _Calendar:
    weekdays: dict[str, frozenset[int]] = {}
    windows: dict[str, tuple[date | None, date | None]] = {}
    day_columns = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )
    for row in _rows(archive, "calendar.txt"):
        service_id = (row.get("service_id") or "").strip()
        if not service_id:
            continue
        weekdays[service_id] = frozenset(
            index for index, column in enumerate(day_columns) if (row.get(column) or "").strip() == "1"
        )
        windows[service_id] = (
            _parse_date(row.get("start_date", "")),
            _parse_date(row.get("end_date", "")),
        )

    exceptions: dict[tuple[str, date], int] = {}
    for row in _rows(archive, "calendar_dates.txt"):
        service_id = (row.get("service_id") or "").strip()
        day = _parse_date(row.get("date", ""))
        if not service_id or day is None:
            continue
        try:
            exception = int((row.get("exception_type") or "").strip())
        except ValueError:
            continue
        exceptions[(service_id, day)] = exception
        # Feeds that ship only calendar_dates.txt have no weekday bitmap; the
        # exception rows are the whole calendar, so register the service.
        weekdays.setdefault(service_id, frozenset())
        windows.setdefault(service_id, (None, None))

    if not weekdays:
        raise GtfsNormalizationError("feed has neither calendar.txt nor calendar_dates.txt")
    return _Calendar(weekdays=weekdays, windows=windows, exceptions=exceptions)


def choose_representative_dates(
    calendar: _Calendar,
    *,
    reference: date,
    candidates_per_day_type: int = 5,
) -> dict[DayType, date]:
    """Pick one ordinary service date per day type.

    A holiday falling on the first matching weekday would silently make the
    whole city look like it runs a Sunday schedule, so several candidates are
    scored by how many services are active and the busiest is taken — a holiday
    reduction is always a *reduction*.
    """

    start, end = calendar.span
    window_start = max(reference, start) if start is not None else reference
    window_end = end

    chosen: dict[DayType, date] = {}
    for day_type, weekday in _DAY_TYPE_WEEKDAY.items():
        offset = (weekday - window_start.weekday()) % 7
        best: tuple[int, date] | None = None
        for index in range(candidates_per_day_type):
            candidate = window_start + timedelta(days=offset + 7 * index)
            if window_end is not None and candidate > window_end:
                break
            count = len(calendar.active_on(candidate))
            if count == 0:
                continue
            if best is None or count > best[0]:
                best = (count, candidate)
        if best is not None:
            chosen[day_type] = best[1]
    if not chosen:
        raise GtfsNormalizationError("no representative service date found in the feed window")
    return chosen


# --------------------------------------------------------------------------
# feed reading
# --------------------------------------------------------------------------


@dataclass
class _FeedTables:
    agency_name: str | None
    agency_timezone: str | None
    #: agency_id -> agency_name, for the feeds that carry several operators.
    agency_names: dict[str, str]
    routes: dict[str, dict[str, str]]
    stops: dict[str, tuple[float, float]]
    trips: dict[str, _Trip]
    calendar: _Calendar
    feed_version: str | None


def _read_static_tables(archive: zipfile.ZipFile, source: FeedSource) -> _FeedTables:
    # A regional feed carries several operators, and the first row is not
    # necessarily the principal one -- TriMet's archive declares Portland
    # Streetcar first.  Every operator is kept so a row can be stamped with its
    # own route's agency rather than with whichever happened to be listed first.
    agency_name = None
    agency_timezone = None
    agency_names: dict[str, str] = {}
    for row in _rows(archive, "agency.txt"):
        name = (row.get("agency_name") or "").strip() or None
        agency_id = (row.get("agency_id") or "").strip()
        if name:
            agency_names[agency_id] = name
        if agency_name is None:
            agency_name = name
            agency_timezone = (row.get("agency_timezone") or "").strip() or None

    feed_version = None
    for row in _rows(archive, "feed_info.txt"):
        feed_version = (row.get("feed_version") or "").strip() or None
        break

    routes = {}
    for row in _rows(archive, "routes.txt"):
        route_id = (row.get("route_id") or "").strip()
        if route_id:
            routes[route_id] = row

    stops: dict[str, tuple[float, float]] = {}
    for row in _rows(archive, "stops.txt"):
        stop_id = (row.get("stop_id") or "").strip()
        location_type = (row.get("location_type") or "0").strip() or "0"
        if not stop_id or location_type not in ("0", ""):
            continue
        try:
            lat = float((row.get("stop_lat") or "").strip())
            lon = float((row.get("stop_lon") or "").strip())
        except ValueError:
            continue
        if not (isfinite(lat) and isfinite(lon)):
            continue
        stops[stop_id] = (lat, lon)

    trips: dict[str, _Trip] = {}
    for row in _rows(archive, "trips.txt"):
        trip_id = (row.get("trip_id") or "").strip()
        route_id = (row.get("route_id") or "").strip()
        if not trip_id or not route_id:
            continue
        raw_direction = (row.get("direction_id") or "").strip()
        direction: int | None = None
        if source.has_direction_id and raw_direction in ("0", "1"):
            direction = int(raw_direction)
        trips[trip_id] = _Trip(
            trip_id=trip_id,
            route_id=route_id,
            service_id=(row.get("service_id") or "").strip(),
            direction_id=direction,
            shape_id=(row.get("shape_id") or "").strip() or None,
        )

    if not routes or not trips:
        raise GtfsNormalizationError("feed has no routes or no trips")

    return _FeedTables(
        agency_name=agency_name,
        agency_timezone=agency_timezone,
        agency_names=agency_names,
        routes=routes,
        stops=stops,
        trips=trips,
        calendar=read_calendar(archive),
        feed_version=feed_version,
    )


def _read_trip_timings(
    archive: zipfile.ZipFile, wanted: set[str]
) -> dict[str, _TripTiming]:
    """Reduce ``stop_times.txt`` to one small record per wanted trip.

    ``stop_times.txt`` is by far the largest table in a GTFS feed -- 202 MB
    uncompressed for Montreal -- so the file itself is never held in memory: it
    is streamed row by row and every row belonging to an unwanted trip is
    discarded immediately.

    Rows for *wanted* trips are staged until the pass completes, then collapsed
    to the stop pattern, the endpoint times and the per-stop departure seconds a
    headway needs.  They cannot be collapsed on arrival because GTFS does not
    guarantee a trip's rows are contiguous or sorted, and assuming they are is a
    silent-corruption bug rather than a loud one.  Peak cost is therefore
    O(rows of wanted trips), measured at about 365 MB on Montreal -- comfortable
    on a workstation, and bounded by the feed rather than unbounded.
    """

    staged: dict[str, list[tuple[int, str, int | None, int | None]]] = defaultdict(list)
    for row in _rows(archive, "stop_times.txt"):
        trip_id = (row.get("trip_id") or "").strip()
        if trip_id not in wanted:
            continue
        stop_id = (row.get("stop_id") or "").strip()
        if not stop_id:
            continue
        try:
            sequence = int((row.get("stop_sequence") or "").strip())
        except ValueError:
            continue
        staged[trip_id].append(
            (
                sequence,
                stop_id,
                parse_gtfs_time(row.get("arrival_time", "")),
                parse_gtfs_time(row.get("departure_time", "")),
            )
        )

    timings: dict[str, _TripTiming] = {}
    for trip_id, entries in staged.items():
        if len(entries) < 2:
            continue
        entries.sort(key=lambda entry: entry[0])
        pattern = tuple(entry[1] for entry in entries)
        # Interpolating between timepoints would invent evidence; a trip whose
        # endpoints are untimed simply cannot contribute a runtime.
        first_departure = entries[0][3] if entries[0][3] is not None else entries[0][2]
        last_arrival = entries[-1][2] if entries[-1][2] is not None else entries[-1][3]
        if first_departure is None or last_arrival is None:
            continue
        if last_arrival <= first_departure:
            continue
        # `0 or -1` is -1 in Python, so a departure at exactly 00:00:00 -- a real
        # time for an overnight route -- was silently converted into the missing
        # sentinel and dropped from every headway measured at that stop.
        departures = tuple(
            value if (value := (entry[3] if entry[3] is not None else entry[2])) is not None else -1
            for entry in entries
        )
        timings[trip_id] = _TripTiming(
            pattern=pattern,
            first_departure_s=first_departure,
            last_arrival_s=last_arrival,
            departures_s=departures,
        )
    return timings


def _read_shapes(archive: zipfile.ZipFile, wanted: set[str]) -> dict[str, float]:
    """Measured length in km for each wanted shape."""

    staged: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for row in _rows(archive, "shapes.txt"):
        shape_id = (row.get("shape_id") or "").strip()
        if shape_id not in wanted:
            continue
        try:
            sequence = int((row.get("shape_pt_sequence") or "").strip())
            lat = float((row.get("shape_pt_lat") or "").strip())
            lon = float((row.get("shape_pt_lon") or "").strip())
        except ValueError:
            continue
        staged[shape_id].append((sequence, lat, lon))

    lengths: dict[str, float] = {}
    for shape_id, points in staged.items():
        if len(points) < 2:
            continue
        points.sort(key=lambda point: point[0])
        length = polyline_length_km([(lat, lon) for _, lat, lon in points])
        if length > 0:
            lengths[shape_id] = length
    return lengths


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------


def _headways(departures: Sequence[int], window: tuple[int, int] | None) -> list[float]:
    """Successive-departure gaps in minutes, optionally inside one time window.

    Gaps are taken between consecutive departures that both fall in the window,
    so the boundary does not manufacture one huge gap spanning the whole
    off-peak; an interval that straddles the edge is simply not counted.
    """

    if window is None:
        selected = sorted(departures)
    else:
        low, high = window
        selected = sorted(value for value in departures if low <= value < high)
    return [
        (selected[i + 1] - selected[i]) / 60.0
        for i in range(len(selected) - 1)
        if selected[i + 1] > selected[i]
    ]


def _median_or_none(values: Sequence[float], *, minimum: int = 1) -> float | None:
    usable = [value for value in values if value > 0]
    if len(usable) < minimum:
        return None
    return float(statistics.median(usable))


@dataclass
class _DirectionMeasurement:
    """Intermediate result for one ``(route, direction, day_type)``."""

    trip_ids: list[str]
    dominant_pattern: tuple[str, ...]
    dominant_trip_ids: list[str]
    branch_count: int
    shape_id: str | None


def _measure_direction(
    trip_ids: Sequence[str],
    timings: dict[str, _TripTiming],
    trips: dict[str, _Trip],
) -> _DirectionMeasurement | None:
    timed = [trip_id for trip_id in trip_ids if trip_id in timings]
    if not timed:
        return None
    # Same reproducibility rule as the busiest stop: break every tie on the
    # value itself, never on dictionary insertion order.
    patterns = Counter(timings[trip_id].pattern for trip_id in timed)
    dominant_pattern = max(patterns.items(), key=lambda item: (item[1], item[0]))[0]
    dominant = [trip_id for trip_id in timed if timings[trip_id].pattern == dominant_pattern]
    shapes = Counter(
        trips[trip_id].shape_id for trip_id in dominant if trips[trip_id].shape_id
    )
    shape_id = max(shapes.items(), key=lambda item: (item[1], item[0]))[0] if shapes else None
    return _DirectionMeasurement(
        trip_ids=timed,
        dominant_pattern=dominant_pattern,
        dominant_trip_ids=dominant,
        branch_count=len(patterns),
        shape_id=shape_id,
    )


def _busiest_stop_departures(
    trip_ids: Sequence[str], timings: dict[str, _TripTiming]
) -> tuple[str | None, list[int]]:
    """Departure times at the stop the most of these trips serve.

    That stop is the direction's trunk: every branch passes through it, so it
    measures the frequency a rider on the common section actually sees.
    """

    counts: Counter[str] = Counter()
    for trip_id in trip_ids:
        counts.update(set(timings[trip_id].pattern))
    if not counts:
        return None, []
    # Ties are common -- every stop on an unbranched route is served by every
    # trip -- and `Counter.most_common` breaks them by insertion order, which
    # here follows set iteration over stop id STRINGS.  Python randomizes string
    # hashing per process, so the winning stop, and with it the measured
    # headway and service span, changed between runs of the same code on the
    # same feed.  Breaking the tie on the stop id makes extraction reproducible.
    stop_id = max(counts.items(), key=lambda item: (item[1], item[0]))[0]
    departures: list[int] = []
    for trip_id in trip_ids:
        timing = timings[trip_id]
        try:
            index = timing.pattern.index(stop_id)
        except ValueError:
            continue
        value = timing.departures_s[index]
        if value >= 0:
            departures.append(value)
    return stop_id, departures


def _route_agency(
    route_row: dict[str, str], tables: "_FeedTables", source: "FeedSource"
) -> str:
    """The operator of *this route*, not of the feed.

    TriMet's and King County Metro's archives each carry several operators, so
    labelling every row with the registry's agency name silently attributes
    Sound Transit and Portland Streetcar routes to the wrong agency -- and those
    rows then train the model under a name that is not theirs.
    """

    agency_id = (route_row.get("agency_id") or "").strip()
    if agency_id and agency_id in tables.agency_names:
        return tables.agency_names[agency_id]
    if len(tables.agency_names) == 1:
        return next(iter(tables.agency_names.values()))
    return source.agency


def _stop_spacings_m(
    pattern: Sequence[str], stops: dict[str, tuple[float, float]]
) -> list[float]:
    coords = [stops[stop_id] for stop_id in pattern if stop_id in stops]
    return [haversine_km(coords[i], coords[i + 1]) * 1000.0 for i in range(len(coords) - 1)]


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------


def normalize_feed(
    source: FeedSource,
    *,
    reference_date: date | None = None,
    min_stops: int = 3,
) -> tuple[list[NormalizedRouteRecord], dict[str, object]]:
    """Normalize one GTFS archive into route records plus an extraction report.

    The report is returned rather than logged because it is the evidence a data
    QA step needs: how many routes were read, how many rows were rejected and
    for exactly which reason.  Silent drops are how a dataset ends up biased
    toward whatever the extractor happened to find easy.
    """

    reference_date = reference_date or date.today()
    rejected: Counter[str] = Counter()

    with zipfile.ZipFile(source.archive_path) as archive:
        tables = _read_static_tables(archive, source)
        representative = choose_representative_dates(tables.calendar, reference=reference_date)

        active_by_day: dict[DayType, set[str]] = {
            day_type: tables.calendar.active_on(day)
            for day_type, day in representative.items()
        }

        # trip ids needed = any trip on a service active on any representative day
        wanted_services = set().union(*active_by_day.values()) if active_by_day else set()
        wanted_trips = {
            trip_id
            for trip_id, trip in tables.trips.items()
            if trip.service_id in wanted_services
        }
        if not wanted_trips:
            raise GtfsNormalizationError(
                "no trips run on any representative service date; check the feed window"
            )
        timings = _read_trip_timings(archive, wanted_trips)

        groups: dict[tuple[str, int | None, DayType], list[str]] = defaultdict(list)
        for day_type, services in active_by_day.items():
            for trip_id in wanted_trips:
                trip = tables.trips[trip_id]
                if trip.service_id in services:
                    groups[(trip.route_id, trip.direction_id, day_type)].append(trip_id)

        measurements: dict[tuple[str, int | None, DayType], _DirectionMeasurement] = {}
        for key, trip_ids in groups.items():
            measurement = _measure_direction(trip_ids, timings, tables.trips)
            if measurement is None:
                rejected["no_timed_trips"] += 1
                continue
            measurements[key] = measurement

        shape_ids = {m.shape_id for m in measurements.values() if m.shape_id}
        shape_lengths = _read_shapes(archive, shape_ids)

    # ---- runtime per group, needed before cycle time can pair directions ----
    runtimes: dict[tuple[str, int | None, DayType], float] = {}
    for key, measurement in measurements.items():
        values = [
            (timings[trip_id].last_arrival_s - timings[trip_id].first_departure_s) / 60.0
            for trip_id in measurement.dominant_trip_ids
        ]
        median = _median_or_none(values)
        if median is not None:
            runtimes[key] = median

    records: list[NormalizedRouteRecord] = []
    for key, measurement in sorted(measurements.items(), key=lambda item: str(item[0])):
        route_id, direction_id, day_type = key
        route_row = tables.routes.get(route_id)
        if route_row is None:
            rejected["unknown_route"] += 1
            continue

        pattern = measurement.dominant_pattern
        if len(pattern) < min_stops:
            rejected["too_few_stops"] += 1
            continue

        length_km = shape_lengths.get(measurement.shape_id or "")
        length_source = "shapes.txt"
        if length_km is None:
            # A feed without usable shape geometry still supports a defensible
            # lower bound: the stop-to-stop path. It is shorter than the real
            # alignment, so it is recorded as a distinct, weaker measurement.
            spacings = _stop_spacings_m(pattern, tables.stops)
            length_km = sum(spacings) / 1000.0 if spacings else None
            length_source = "stop_to_stop_path"
        if not length_km or length_km <= 0:
            rejected["no_usable_length"] += 1
            continue

        spacings = _stop_spacings_m(pattern, tables.stops)
        located = [stop_id for stop_id in pattern if stop_id in tables.stops]
        if len(located) < 2:
            rejected["stops_missing_coordinates"] += 1
            continue

        stop_count = len(pattern)
        runtime = runtimes.get(key)
        speed = (length_km / (runtime / 60.0)) if runtime else None

        _, departures = _busiest_stop_departures(measurement.trip_ids, timings)
        all_gaps = _headways(departures, None)
        peak_gaps = _headways(departures, AM_PEAK) + _headways(departures, PM_PEAK)
        offpeak_gaps = _headways(departures, MIDDAY)

        span_hours: float | None = None
        if len(departures) >= MIN_SPAN_DEPARTURES:
            span_hours = (max(departures) - min(departures)) / 3600.0
            if span_hours <= 0:
                span_hours = None

        peak_headway = _median_or_none(peak_gaps, minimum=2)
        offpeak_headway = _median_or_none(offpeak_gaps, minimum=2)
        median_headway = _median_or_none(all_gaps, minimum=MIN_HEADWAY_GAPS)

        terminal_separation = haversine_km(
            tables.stops[located[0]], tables.stops[located[-1]]
        )
        directness = terminal_separation / length_km if length_km > 0 else None
        is_loop = terminal_separation <= min(
            LOOP_TERMINAL_SEPARATION_KM, LOOP_TERMINAL_SEPARATION_FRACTION * length_km
        )

        # ---- derived-under-assumption block ----
        # Three genuinely different cases, and only two of them support a cycle
        # time.  Collapsing them (the previous "just double this direction")
        # invented a return leg for one-way school trippers and double-counted
        # every loop route, inflating both their cycle time and their fleet.
        opposite_key = (
            (route_id, 1 - direction_id, day_type) if direction_id is not None else None
        )
        opposite_runtime = runtimes.get(opposite_key) if opposite_key else None
        cycle_time = recovery = vehicles = None
        cycle_basis: str | None = None
        if runtime is not None:
            round_trip: float | None = None
            if is_loop:
                # A loop is tested FIRST, before any opposite direction.  Where an
                # agency publishes a circular route as two directions those are two
                # separate circuits (clockwise and counter-clockwise), not two halves
                # of one; adding them together doubles the cycle and the fleet.
                round_trip = runtime
                cycle_basis = "loop route; one-way runtime is the full circuit"
            elif opposite_runtime is not None:
                round_trip = runtime + opposite_runtime
                cycle_basis = "both directions observed"
            if round_trip is not None:
                recovery = max(
                    RECOVERY_FRACTION_OF_ROUND_TRIP * round_trip, MIN_RECOVERY_MINUTES
                )
                cycle_time = round_trip + recovery
                sizing_headway = peak_headway or median_headway or offpeak_headway
                if sizing_headway and sizing_headway > 0:
                    vehicles = estimate_required_vehicles(
                        cycle_time_minutes=cycle_time, headway_minutes=sizing_headway
                    )

        try:
            route_type = int((route_row.get("route_type") or "").strip())
        except ValueError:
            rejected["bad_route_type"] += 1
            continue

        assumptions = [
            f"recovery = max({RECOVERY_FRACTION_OF_ROUND_TRIP:.0%} of round-trip running time, "
            f"{MIN_RECOVERY_MINUTES:g} min); no block or run data was available",
            "estimated_required_vehicles = cycle time / "
            + ("peak" if peak_headway else "median" if median_headway else "off-peak")
            + " headway; a REQUIRED estimate, not an assignment",
            f"peak windows {AM_PEAK[0] // 3600:02d}-{AM_PEAK[1] // 3600:02d} and "
            f"{PM_PEAK[0] // 3600:02d}-{PM_PEAK[1] // 3600:02d}; off-peak "
            f"{MIDDAY[0] // 3600:02d}-{MIDDAY[1] // 3600:02d} local",
            f"representative service date {representative[day_type].isoformat()} "
            "chosen as the busiest of several candidates of this day type",
        ]
        if cycle_basis is not None:
            assumptions.append(f"cycle time basis: {cycle_basis}")
        elif runtime is not None:
            assumptions.append(
                "no return leg observed and the pattern is not a loop, so no cycle "
                "time or vehicle requirement is derivable; both are left missing "
                "rather than approximated"
            )

        missing = [
            name
            for name, value in (
                ("scheduled_runtime_minutes", runtime),
                ("peak_headway_minutes", peak_headway),
                ("offpeak_headway_minutes", offpeak_headway),
                ("median_headway_minutes", median_headway),
                ("service_span_hours", span_hours),
                ("estimated_required_vehicles", vehicles),
            )
            if value is None
        ]
        missing.append("ridership: not published at route level by this agency")

        records.append(
            NormalizedRouteRecord(
                city=source.city,
                agency=_route_agency(route_row, tables, source),
                route_kind=route_kind_from_gtfs(route_type),
                route_label=(route_row.get("route_short_name") or "").strip()
                or (route_row.get("route_long_name") or "").strip()
                or route_id,
                direction_id=direction_id,
                day_type=day_type,
                one_way_length_km=round(length_km, 4),
                stop_count=stop_count,
                stops_per_km=round(stop_count / length_km, 4),
                mean_stop_spacing_m=round(sum(spacings) / len(spacings), 2) if spacings else 0.0,
                median_stop_spacing_m=round(statistics.median(spacings), 2) if spacings else None,
                directness_ratio=round(directness, 4) if directness is not None else None,
                branch_count=measurement.branch_count,
                dominant_pattern_trip_share=round(
                    len(measurement.dominant_trip_ids) / len(measurement.trip_ids), 4
                ),
                loop_route=is_loop,
                scheduled_runtime_minutes=round(runtime, 2) if runtime else None,
                scheduled_commercial_speed_kmh=round(speed, 3) if speed else None,
                peak_headway_minutes=round(peak_headway, 2) if peak_headway else None,
                offpeak_headway_minutes=round(offpeak_headway, 2) if offpeak_headway else None,
                median_headway_minutes=round(median_headway, 2) if median_headway else None,
                headway_sample_count=len(all_gaps),
                trips_per_day=len(measurement.trip_ids),
                service_span_hours=round(span_hours, 3) if span_hours else None,
                estimated_cycle_time_minutes=round(cycle_time, 2) if cycle_time else None,
                estimated_recovery_minutes=round(recovery, 2) if recovery else None,
                estimated_required_vehicles=round(vehicles, 3) if vehicles else None,
                vehicles_from_block_data=False,
                provenance=RouteProvenance(
                    source_id=source.source_id,
                    source_url=source.source_url,
                    licence=source.licence,
                    retrieved_at=source.retrieved_at,
                    static_feed_id=tables.feed_version or source.checksum_sha256[:16],
                    original_route_id=route_id,
                    original_direction_id=direction_id,
                    transformations=(
                        EXTRACTOR_VERSION,
                        f"length from {length_source}",
                        "geometry and runtime from the dominant stop pattern",
                        "headway measured at the direction's busiest stop over all its trips",
                    ),
                    assumptions=tuple(assumptions),
                    missing_fields=tuple(missing),
                ),
            )
        )

    report: dict[str, object] = {
        "source_id": source.source_id,
        "city": source.city,
        "agency_in_feed": tables.agency_name,
        "agencies_in_feed": tables.agency_names,
        "feed_timezone": tables.agency_timezone,
        "feed_version": tables.feed_version,
        "checksum_sha256": source.checksum_sha256,
        "retrieved_at": source.retrieved_at.isoformat(),
        "extractor_version": EXTRACTOR_VERSION,
        "representative_dates": {k: v.isoformat() for k, v in representative.items()},
        "routes_in_feed": len(tables.routes),
        "trips_in_feed": len(tables.trips),
        "trips_on_representative_dates": len(wanted_trips),
        "groups_considered": len(groups),
        "records_emitted": len(records),
        "rejected": dict(rejected),
    }
    return records, report
