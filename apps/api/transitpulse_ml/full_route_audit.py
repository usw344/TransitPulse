"""Pure coverage audit for strict terminal-to-terminal traversal evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def audit_complete_route_coverage(
    labels: list[dict[str, Any]],
    static_ranges: dict[tuple[str, str], tuple[int, int]],
) -> dict[str, Any]:
    """Require every scheduled edge once, in order; never stitch gaps."""

    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for label in labels:
        groups[
            (
                str(label["service_day"]),
                str(label["static_feed_id"]),
                str(label["trip_gtfs_id"]),
                str(label["vehicle_id"]),
            )
        ].append(label)

    by_service_day: dict[str, Counter[str]] = defaultdict(Counter)
    complete_runs: list[dict[str, Any]] = []
    missing_static_ranges = 0
    for (service_day, feed_id, trip_id, vehicle_id), rows in groups.items():
        static_range = static_ranges.get((feed_id, trip_id))
        if static_range is None:
            missing_static_ranges += 1
            continue
        first_sequence, last_sequence = static_range
        expected_edges = list(zip(range(first_sequence, last_sequence), range(first_sequence + 1, last_sequence + 1)))
        actual_edges = [
            (int(row["from_stop_sequence"]), int(row["to_stop_sequence"]))
            for row in sorted(rows, key=lambda row: (int(row["from_stop_sequence"]), str(row["observed_start"])))
        ]
        complete = actual_edges == expected_edges
        counters = by_service_day[service_day]
        counters["label_runs"] += 1
        counters["complete_terminal_to_terminal_runs"] += int(complete)
        counters["max_derived_edges"] = max(counters["max_derived_edges"], len(actual_edges))
        if complete:
            complete_runs.append(
                {
                    "service_day": service_day,
                    "static_feed_id": feed_id,
                    "trip_gtfs_id": trip_id,
                    "vehicle_id": vehicle_id,
                    "edges": len(actual_edges),
                }
            )

    return {
        "label_count": len(labels),
        "label_run_count": sum(counters["label_runs"] for counters in by_service_day.values()),
        "missing_static_ranges": missing_static_ranges,
        "by_service_day": {day: dict(counters) for day, counters in sorted(by_service_day.items())},
        "complete_terminal_to_terminal_runs": complete_runs,
        "complete_terminal_to_terminal_run_count": len(complete_runs),
        "full_route_terminal_validation": {
            "status": "REACHED" if complete_runs else "NOT REACHED",
            "reason": (
                "Every static edge is present in at least one strict geometry-derived run."
                if complete_runs
                else "No one run contains every consecutive static edge under the existing geometry, ambiguity, progression, and 120-second bracketing rules. Gaps were not stitched."
            ),
        },
    }
