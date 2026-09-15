from transitpulse_ml.full_route_audit import audit_complete_route_coverage


def label(from_sequence: int, to_sequence: int, observed_start: str) -> dict[str, object]:
    return {
        "service_day": "2026-09-10",
        "static_feed_id": "feed",
        "trip_gtfs_id": "trip",
        "vehicle_id": "vehicle",
        "from_stop_sequence": from_sequence,
        "to_stop_sequence": to_sequence,
        "observed_start": observed_start,
    }


def test_complete_route_requires_all_edges_in_one_ordered_run() -> None:
    complete = audit_complete_route_coverage(
        [label(1, 2, "2026-09-10T08:00:00-06:00"), label(2, 3, "2026-09-10T08:01:00-06:00")],
        {("feed", "trip"): (1, 3)},
    )
    assert complete["complete_terminal_to_terminal_run_count"] == 1
    assert complete["full_route_terminal_validation"]["status"] == "REACHED"


def test_missing_edge_is_not_stitched_into_a_complete_route() -> None:
    incomplete = audit_complete_route_coverage(
        [label(1, 2, "2026-09-10T08:00:00-06:00"), label(3, 4, "2026-09-10T08:02:00-06:00")],
        {("feed", "trip"): (1, 4)},
    )
    assert incomplete["complete_terminal_to_terminal_run_count"] == 0
    assert incomplete["full_route_terminal_validation"]["status"] == "NOT REACHED"
