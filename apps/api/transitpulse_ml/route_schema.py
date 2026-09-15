"""City-neutral normalized route/service record for cross-city approximation.

One row describes one *directed* route variant in one city under one service
pattern.  The schema exists so that a model can learn broad relationships
between route geometry, stop spacing, service intensity and scheduled outcomes
across agencies whose raw identifiers are not comparable.

Two rules shape every field here:

1. **Measured and derived values stay distinguishable.**  A quantity read from
   an official feed is not the same evidence as one computed under operating
   assumptions, so derived quantities carry explicit ``estimated_`` names and
   the assumptions that produced them are recorded in :class:`RouteProvenance`.
2. **Nothing is included merely because it is available.**  Fields absent from
   an agency's feed stay ``None`` and are counted as missing rather than
   imputed; a model consumer decides how to handle missingness, not the
   extractor.

Ridership is deliberately optional and absent by default: GTFS carries no
ridership, vehicle positions are not passenger demand, and neither adopted city
publishes comparable route-level boardings.  See ``docs/data_sources.md``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal


#: Schema version. Bump on any field change so persisted datasets stay readable
#: and a model artifact can record exactly which shape it was trained on.
ROUTE_SCHEMA_VERSION = "1.2.0"


RouteKind = Literal["bus", "light_rail", "subway", "tram", "rail", "ferry", "other"]
DayType = Literal["weekday", "saturday", "sunday"]


#: GTFS route_type -> city-neutral route kind.  Cross-city comparison needs a
#: common vocabulary; raw route_type integers are stable but unreadable, and
#: agencies use the extended ranges inconsistently.
_GTFS_ROUTE_TYPE_TO_KIND: dict[int, RouteKind] = {
    0: "tram",
    1: "subway",
    2: "rail",
    3: "bus",
    4: "ferry",
    5: "tram",      # cable tram
    6: "other",     # aerial lift
    7: "other",     # funicular
    11: "bus",      # trolleybus
    12: "rail",     # monorail
}


def route_kind_from_gtfs(route_type: int) -> RouteKind:
    """Map a GTFS ``route_type`` onto the city-neutral vocabulary.

    Calgary's CTrain is published as light rail while Edmonton's network is
    predominantly bus, so the raw integer must be normalized before any
    cross-city grouping.  Extended (1xx) route types collapse to their base
    class; anything unrecognized becomes ``"other"`` rather than silently
    defaulting to bus.
    """

    if route_type in _GTFS_ROUTE_TYPE_TO_KIND:
        return _GTFS_ROUTE_TYPE_TO_KIND[route_type]
    # GTFS extended route types are 1xx (rail), 2xx (coach), 7xx (bus), etc.
    if 100 <= route_type <= 199:
        return "rail"
    if 400 <= route_type <= 499:
        return "subway"
    if 700 <= route_type <= 799 or 200 <= route_type <= 299:
        return "bus"
    if 900 <= route_type <= 999:
        return "tram"
    if 1200 <= route_type <= 1299:
        return "ferry"
    return "other"


@dataclass(frozen=True)
class RouteProvenance:
    """Where one normalized row came from and what was assumed to build it.

    Every normalized row keeps this so a dataset can be audited without
    re-deriving it, and so a reviewer can tell a measured field from a modelled
    one after the fact.
    """

    #: Registry key in ``docs/data_sources.md`` (e.g. ``"edmonton-ets"``).
    source_id: str
    #: Official dataset URL the row was derived from.
    source_url: str
    #: Licence name recorded in the source register.
    licence: str
    #: When the source artifact was retrieved.
    retrieved_at: datetime
    #: Feed-internal identity, so a row can be traced back to raw GTFS.
    static_feed_id: str | None = None
    original_route_id: str | None = None
    original_direction_id: int | None = None
    #: Named transformations applied, in order.
    transformations: tuple[str, ...] = ()
    #: Operating assumptions behind any ``estimated_`` field.
    assumptions: tuple[str, ...] = ()
    #: Field names that were unavailable in this source.
    missing_fields: tuple[str, ...] = ()
    #: Schema version this row was written against.
    schema_version: str = ROUTE_SCHEMA_VERSION


@dataclass(frozen=True)
class NormalizedRouteRecord:
    """One directed route variant in one city under one service pattern.

    Units are stated in every field name.  Distances are kilometres, durations
    are minutes (service-planning convention), speeds are km/h.
    """

    # --- identity -------------------------------------------------------
    city: str
    agency: str
    route_kind: RouteKind
    #: Agency-facing route label, for human inspection only.  Never a feature:
    #: route numbers carry no comparable meaning between agencies.
    route_label: str
    #: Direction of travel this row describes; rows are per direction because
    #: pooling opposing directions corrupts both runtime and headway.
    direction_id: int | None
    day_type: DayType

    # --- geometry (measured from the feed) ------------------------------
    #: One-way directed length along the route's own shape.
    one_way_length_km: float
    stop_count: int
    stops_per_km: float
    mean_stop_spacing_m: float
    #: Median spacing resists the long tail that one express segment creates,
    #: so a model sees typical spacing as well as the average.
    median_stop_spacing_m: float | None = None
    #: Straight-line terminal separation / path length; 1.0 is a straight line.
    #: Values near 0 indicate a loop, where the measure is not meaningful.
    directness_ratio: float | None = None
    #: Distinct stop patterns operated on this route/direction/day.  The
    #: geometry above describes only the dominant one, so a high branch count
    #: means the row represents less of the route's real service.
    branch_count: int | None = None
    #: Share of the direction's trips that run the dominant pattern, 0-1.  This
    #: is the single most important QA field on the row: at 0.95 the row speaks
    #: for the route, at 0.30 it speaks for one branch of it.
    dominant_pattern_trip_share: float | None = None
    #: True when the pattern returns to where it started.  A loop's one-way
    #: runtime already *is* its cycle, so conflating it with a linear route
    #: doubles its fleet estimate; it is also a genuinely different service
    #: geometry that a model should be able to see.
    loop_route: bool | None = None

    # --- service (measured from the schedule) ---------------------------
    scheduled_runtime_minutes: float | None = None
    #: One-way length divided by scheduled runtime.  Commercial speed includes
    #: dwell time and is not a vehicle speed.
    scheduled_commercial_speed_kmh: float | None = None
    peak_headway_minutes: float | None = None
    offpeak_headway_minutes: float | None = None
    median_headway_minutes: float | None = None
    #: Number of successive-departure gaps the headways were measured from.  A
    #: headway backed by two gaps is not evidence of a service pattern; a
    #: consumer filters on this rather than trusting the value blindly.
    headway_sample_count: int | None = None
    trips_per_day: int | None = None
    service_span_hours: float | None = None

    # --- derived under assumptions --------------------------------------
    #: Round-trip runtime plus terminal recovery, where recovery is derivable.
    estimated_cycle_time_minutes: float | None = None
    estimated_recovery_minutes: float | None = None
    #: cycle time / headway.  This is a REQUIRED vehicle estimate, never an
    #: actual fleet assignment; block data supersedes it when available.
    estimated_required_vehicles: float | None = None
    #: True only when the vehicle count came from real block/run data.
    vehicles_from_block_data: bool = False

    # --- ridership (optional, usually absent) ---------------------------
    #: Populated only when an agency publishes comparable route-level demand.
    #: ``ridership_measure`` names exactly what was counted; rows with
    #: different measures must not be pooled.
    ridership_measure: str | None = None
    route_boardings_per_day: float | None = None
    boardings_per_trip: float | None = None
    boardings_per_km: float | None = None
    boardings_per_service_hour: float | None = None

    # --- context --------------------------------------------------------
    #: Included only where comparably derivable across every adopted city.
    corridor_population_density_per_km2: float | None = None

    provenance: RouteProvenance | None = None

    def __post_init__(self) -> None:
        if self.stop_count < 0:
            raise ValueError("stop_count cannot be negative")
        if self.one_way_length_km <= 0:
            raise ValueError("one_way_length_km must be positive")
        if self.direction_id is not None and self.direction_id not in (0, 1):
            raise ValueError("direction_id must be 0, 1 or None")
        if self.dominant_pattern_trip_share is not None and not (
            0.0 < self.dominant_pattern_trip_share <= 1.0
        ):
            raise ValueError("dominant_pattern_trip_share must lie in (0, 1]")
        if self.route_boardings_per_day is not None and not self.ridership_measure:
            raise ValueError(
                "ridership requires ridership_measure naming what was counted; "
                "boardings, passenger trips, unlinked/linked trips, APC counts "
                "and fare validations are not interchangeable"
            )
        if self.vehicles_from_block_data and self.estimated_required_vehicles is None:
            raise ValueError(
                "vehicles_from_block_data claims block evidence but no vehicle count is set"
            )

    def as_row(self) -> dict[str, Any]:
        """Flatten to a JSON-serializable row, provenance nested."""

        row = asdict(self)
        if self.provenance is not None:
            row["provenance"] = {
                **asdict(self.provenance),
                "retrieved_at": self.provenance.retrieved_at.isoformat(),
            }
        return row


def estimate_required_vehicles(
    *, cycle_time_minutes: float, headway_minutes: float
) -> float:
    """Approximate concurrent vehicles as cycle time / headway.

    This is the standard planning identity and it is only meaningful when the
    cycle time already includes terminal recovery.  The result is an
    **estimated requirement**, never an actual assignment: real blocks
    interline across routes, and a fractional result means the true integer
    requirement depends on scheduling decisions this model does not see.
    """

    if headway_minutes <= 0:
        raise ValueError("headway_minutes must be positive")
    if cycle_time_minutes <= 0:
        raise ValueError("cycle_time_minutes must be positive")
    return cycle_time_minutes / headway_minutes
