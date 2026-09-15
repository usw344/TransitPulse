from datetime import datetime, timezone

import pytest

from transitpulse_ml.route_schema import (
    ROUTE_SCHEMA_VERSION,
    NormalizedRouteRecord,
    RouteProvenance,
    estimate_required_vehicles,
    route_kind_from_gtfs,
)


PROVENANCE = RouteProvenance(
    source_id="edmonton-ets",
    source_url="https://gtfs.edmonton.ca/TMGTFSRealTimeWebService/GTFS/GTFS.zip",
    licence="Open Government Licence - Edmonton",
    retrieved_at=datetime(2026, 9, 11, tzinfo=timezone.utc),
    static_feed_id="feed-1",
    original_route_id="004",
    original_direction_id=1,
)


def record(**overrides) -> NormalizedRouteRecord:
    defaults = dict(
        city="Edmonton",
        agency="Edmonton Transit Service",
        route_kind="bus",
        route_label="004",
        direction_id=1,
        day_type="weekday",
        one_way_length_km=18.2,
        stop_count=51,
        stops_per_km=2.8,
        mean_stop_spacing_m=357.0,
        provenance=PROVENANCE,
    )
    defaults.update(overrides)
    return NormalizedRouteRecord(**defaults)


def test_gtfs_route_types_map_to_city_neutral_kinds() -> None:
    assert route_kind_from_gtfs(3) == "bus"
    assert route_kind_from_gtfs(0) == "tram"
    assert route_kind_from_gtfs(1) == "subway"
    assert route_kind_from_gtfs(2) == "rail"
    assert route_kind_from_gtfs(11) == "bus"
    # Extended route types collapse to their base class.
    assert route_kind_from_gtfs(700) == "bus"
    assert route_kind_from_gtfs(109) == "rail"
    # Unknown types must not silently become bus.
    assert route_kind_from_gtfs(-5) == "other"
    assert route_kind_from_gtfs(5000) == "other"


def test_ridership_requires_an_explicit_measure_name() -> None:
    """Boardings and passenger trips are not interchangeable."""

    with pytest.raises(ValueError, match="ridership_measure"):
        record(route_boardings_per_day=4200.0)

    accepted = record(
        route_boardings_per_day=4200.0,
        ridership_measure="unlinked_boardings_apc_annual_average_weekday",
    )
    assert accepted.ridership_measure


def test_block_backed_vehicle_claim_requires_a_vehicle_count() -> None:
    with pytest.raises(ValueError, match="block evidence"):
        record(vehicles_from_block_data=True)


def test_directional_and_geometry_invariants_are_enforced() -> None:
    with pytest.raises(ValueError, match="direction_id"):
        record(direction_id=2)
    with pytest.raises(ValueError, match="one_way_length_km"):
        record(one_way_length_km=0)
    with pytest.raises(ValueError, match="stop_count"):
        record(stop_count=-1)


def test_estimated_vehicles_is_cycle_time_over_headway() -> None:
    assert estimate_required_vehicles(cycle_time_minutes=120, headway_minutes=15) == 8.0
    with pytest.raises(ValueError):
        estimate_required_vehicles(cycle_time_minutes=120, headway_minutes=0)
    with pytest.raises(ValueError):
        estimate_required_vehicles(cycle_time_minutes=0, headway_minutes=15)


def test_row_keeps_provenance_and_schema_version() -> None:
    row = record().as_row()
    assert row["provenance"]["source_id"] == "edmonton-ets"
    assert row["provenance"]["schema_version"] == ROUTE_SCHEMA_VERSION
    assert row["provenance"]["retrieved_at"] == "2026-09-11T00:00:00+00:00"
    # Ridership and derived fields stay absent rather than imputed.
    assert row["route_boardings_per_day"] is None
    assert row["estimated_required_vehicles"] is None
    assert row["vehicles_from_block_data"] is False
