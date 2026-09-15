"""Deterministic, source-only GTFS trip-to-block attribution reporting.

This module deliberately reports static trip provenance, not vehicle block
assignment.  A matching static block ID can help a later, separately validated
Gate 5 evaluation link source observations to published GTFS context; it says
nothing by itself about vehicle continuity, fleet assignment, recovery, or
service regularity.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


ATTRIBUTED = "static_trip_block_attributed"
MISSING_BLOCK = "static_trip_without_block"
CROSS_FEED = "cross_feed_trip_id_only"
UNMATCHED = "unmatched_static_trip"
AMBIGUOUS = "ambiguous_exact_static_trip"


def _text(value: object | None) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def classify_static_trip_block(row: Mapping[str, object]) -> str:
    """Classify one exact feed/trip lookup without inferring a missing match."""

    exact_match_count = int(row["exact_static_trip_match_count"])
    if exact_match_count > 1:
        return AMBIGUOUS
    if exact_match_count == 1:
        return ATTRIBUTED if _text(row.get("static_block_id")) else MISSING_BLOCK
    if int(row["other_feed_trip_match_count"]) > 0:
        return CROSS_FEED
    return UNMATCHED


def _record(row: Mapping[str, object]) -> dict[str, object | None]:
    status = classify_static_trip_block(row)
    observed_route = _text(row.get("observed_route_gtfs_id"))
    static_route = _text(row.get("static_route_gtfs_id"))
    return {
        "source_observation_id": int(row["source_observation_id"]),
        "observation_key": str(row["observation_key"]),
        "static_feed_id": str(row["static_feed_id"]),
        "observed_at": str(row["observed_at"]),
        "recorded_at": str(row["recorded_at"]),
        "source_timestamp": _text(row.get("source_timestamp")),
        "vehicle_id": str(row["vehicle_id"]),
        "trip_gtfs_id": str(row["trip_gtfs_id"]),
        "observed_route_gtfs_id": observed_route,
        "static_route_gtfs_id": static_route,
        "static_block_id": _text(row.get("static_block_id")) if status == ATTRIBUTED else None,
        "attribution_status": status,
        "route_id_relation": (
            "unknown_realtime_route"
            if observed_route is None
            else "matches_static_trip"
            if observed_route == static_route
            else "mismatches_static_trip"
        ),
        "exact_static_trip_match_count": int(row["exact_static_trip_match_count"]),
        "other_feed_trip_match_count": int(row["other_feed_trip_match_count"]),
    }


def attribution_records(rows: Iterable[Mapping[str, object]]) -> list[dict[str, object | None]]:
    """Normalize and deterministically sort a bounded SQL result set."""

    records = [_record(row) for row in rows]
    return sorted(
        records,
        key=lambda record: (
            str(record["observed_at"]),
            str(record["recorded_at"]),
            int(record["source_observation_id"]),
            str(record["observation_key"]),
        ),
    )


def records_sha256(records: list[dict[str, object | None]]) -> str:
    """Return a stable checksum over canonical row-level attribution evidence."""

    canonical = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def summarize_attributions(records: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Count all outcomes rather than silently dropping non-attributable rows."""

    rows = list(records)
    statuses = Counter(str(row["attribution_status"]) for row in rows)
    relations = Counter(str(row["route_id_relation"]) for row in rows)
    by_route_day: dict[tuple[str | None, str], Counter[str]] = defaultdict(Counter)
    for row in rows:
        observed_calendar_date = str(row["observed_at"])[:10]
        by_route_day[(row.get("static_route_gtfs_id"), observed_calendar_date)][str(row["attribution_status"])] += 1
    return {
        "source_row_count": len(rows),
        "attribution_status_counts": dict(sorted(statuses.items())),
        "route_id_relation_counts": dict(sorted(relations.items())),
        "attributed_row_count": statuses[ATTRIBUTED],
        "unattributed_row_count": len(rows) - statuses[ATTRIBUTED],
        "distinct_attributed_static_blocks": len(
            {str(row["static_block_id"]) for row in rows if row.get("static_block_id") is not None}
        ),
        "distinct_attributed_static_trips": len(
            {str(row["trip_gtfs_id"]) for row in rows if row.get("attribution_status") == ATTRIBUTED}
        ),
        "distinct_attributed_vehicles": len(
            {str(row["vehicle_id"]) for row in rows if row.get("attribution_status") == ATTRIBUTED}
        ),
        "per_static_route_observed_calendar_date": [
            {
                "static_route_gtfs_id": route_id,
                "observed_calendar_date": observed_calendar_date,
                "attribution_status_counts": dict(sorted(counts.items())),
            }
            for (route_id, observed_calendar_date), counts in sorted(
                by_route_day.items(), key=lambda item: (str(item[0][0]), item[0][1])
            )
        ],
    }


def write_new_report(path: Path, report: Mapping[str, object]) -> None:
    """Persist one versioned report and refuse to replace an existing artifact."""

    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing block attribution audit: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
