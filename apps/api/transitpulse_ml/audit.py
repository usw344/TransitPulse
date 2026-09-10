"""Read-only audit of the observations available for modeling.

The command deliberately performs aggregate queries only and begins a read-only
transaction. It never creates, updates, or deletes database state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from transitpulse_api.database import get_engine


SUMMARY_SQL = """
SELECT
    min(observed_at) AS earliest,
    max(observed_at) AS latest,
    count(*) AS observations,
    count(DISTINCT observed_at::date) AS calendar_days,
    count(DISTINCT route_gtfs_id) FILTER (WHERE route_gtfs_id IS NOT NULL) AS routes,
    count(DISTINCT trip_gtfs_id) FILTER (WHERE trip_gtfs_id IS NOT NULL) AS trips,
    count(DISTINCT vehicle_id) AS vehicles,
    count(DISTINCT static_feed_id) AS feed_versions,
    count(*) FILTER (WHERE observed_at IS NULL) AS null_observed_at,
    count(*) FILTER (WHERE position IS NULL) AS null_position,
    count(*) FILTER (WHERE trip_gtfs_id IS NULL) AS null_trip,
    count(*) FILTER (WHERE route_gtfs_id IS NULL) AS null_route,
    count(*) FILTER (WHERE current_stop_sequence IS NULL) AS null_stop_sequence,
    count(*) FILTER (WHERE current_status IS NULL) AS omitted_current_status,
    count(*) FILTER (WHERE EXISTS (
        SELECT 1 FROM routes AS r
        WHERE r.feed_id = o.static_feed_id
          AND r.gtfs_route_id = o.route_gtfs_id
    )) AS matched_static_route,
    count(*) FILTER (WHERE EXISTS (
        SELECT 1 FROM trips AS t
        WHERE t.feed_id = o.static_feed_id
          AND t.gtfs_trip_id = o.trip_gtfs_id
    )) AS matched_static_trip,
    count(*) FILTER (WHERE EXISTS (
        SELECT 1
        FROM trips AS t
        JOIN stop_times AS st ON st.trip_id = t.id
        WHERE t.feed_id = o.static_feed_id
          AND t.gtfs_trip_id = o.trip_gtfs_id
          AND st.stop_sequence = o.current_stop_sequence
    )) AS matched_static_trip_stop_sequence,
    min(recorded_at) AS earliest_recorded_at,
    max(recorded_at) AS latest_recorded_at,
    current_setting('TimeZone') AS database_timezone
FROM vehicle_observations AS o
"""

HOURS_SQL = """
SELECT
    date_trunc('hour', observed_at) AS bucket_hour,
    count(*) AS observations,
    count(DISTINCT vehicle_id) AS vehicles,
    min(observed_at) AS first_at,
    max(observed_at) AS last_at
FROM vehicle_observations
WHERE observed_at IS NOT NULL
GROUP BY 1
ORDER BY 1
"""

INTERVALS_SQL = """
WITH intervals AS (
    SELECT observed_at - lag(observed_at) OVER (
        PARTITION BY static_feed_id, vehicle_id ORDER BY observed_at
    ) AS duration
    FROM vehicle_observations
    WHERE observed_at IS NOT NULL
)
SELECT
    count(*) FILTER (WHERE duration IS NOT NULL) AS intervals,
    percentile_cont(ARRAY[0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99])
        WITHIN GROUP (ORDER BY extract(epoch FROM duration))
        FILTER (WHERE duration > interval '0') AS positive_percentiles_seconds,
    count(*) FILTER (WHERE duration <= interval '0') AS nonpositive,
    count(*) FILTER (WHERE duration > interval '2 minutes') AS over_2_minutes,
    count(*) FILTER (WHERE duration > interval '10 minutes') AS over_10_minutes
FROM intervals
"""

GAPS_SQL = """
WITH timestamps AS (
    SELECT DISTINCT observed_at
    FROM vehicle_observations
    WHERE observed_at IS NOT NULL
), gaps AS (
    SELECT
        observed_at AS gap_end,
        observed_at - lag(observed_at) OVER (ORDER BY observed_at) AS duration
    FROM timestamps
)
SELECT gap_end, duration
FROM gaps
WHERE duration IS NOT NULL
ORDER BY duration DESC
LIMIT 50
"""

FEEDS_SQL = """
SELECT
    f.id,
    f.provider,
    f.source_url,
    f.imported_at,
    f.feed_start_date,
    f.feed_end_date,
    f.import_status,
    min(o.observed_at) AS observation_start,
    max(o.observed_at) AS observation_end,
    count(o.id) AS observations
FROM gtfs_feeds AS f
LEFT JOIN vehicle_observations AS o ON o.static_feed_id = f.id
GROUP BY f.id
ORDER BY f.imported_at
"""

STORAGE_SQL = """
SELECT
    pg_total_relation_size('vehicle_observations') AS observation_total_bytes,
    pg_relation_size('vehicle_observations') AS observation_table_bytes,
    pg_indexes_size('vehicle_observations') AS observation_index_bytes,
    pg_database_size(current_database()) AS database_bytes
"""


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _rows(connection: Connection, sql: str) -> list[dict[str, Any]]:
    return [
        {key: _json_value(value) for key, value in row._mapping.items()}
        for row in connection.execute(text(sql))
    ]


def build_audit(connection: Connection) -> dict[str, Any]:
    """Build the complete audit inside an already-open connection."""

    connection.execute(text("SET TRANSACTION READ ONLY"))
    summary = _rows(connection, SUMMARY_SQL)[0]
    earliest = datetime.fromisoformat(summary["earliest"]) if summary["earliest"] else None
    latest = datetime.fromisoformat(summary["latest"]) if summary["latest"] else None
    elapsed_seconds = (latest - earliest).total_seconds() if earliest and latest else 0
    summary["elapsed_seconds"] = elapsed_seconds
    summary["elapsed_hours"] = round(elapsed_seconds / 3600, 3)
    observations = summary["observations"]
    for count_name in (
        "matched_static_route",
        "matched_static_trip",
        "matched_static_trip_stop_sequence",
    ):
        summary[f"{count_name}_rate"] = round(
            summary[count_name] / observations, 6
        ) if observations else 0.0

    report = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(),
        "transaction_mode": "read-only",
        "summary": summary,
        "coverage_by_hour": _rows(connection, HOURS_SQL),
        "per_vehicle_observation_intervals": _rows(connection, INTERVALS_SQL)[0],
        "largest_global_timestamp_gaps": _rows(connection, GAPS_SQL),
        "static_feed_versions": _rows(connection, FEEDS_SQL),
        "storage": _rows(connection, STORAGE_SQL)[0],
        "fitness": {
            "rolling_30_day_ready": elapsed_seconds >= 30 * 86400,
            "credible_deep_model_ready": False,
            "judgment": (
                "No observations are available."
                if not earliest
                else "Available history is partial and discontinuous; retain it, keep recording, "
                "and use it only for pipeline smoke tests until broader chronological coverage exists."
            ),
        },
        "label_eligibility_rule": (
            "Only observations matching the same static feed's route, trip, and exact "
            "GTFS stop_sequence may enter segment-label extraction. Nonmatches are "
            "excluded and counted; non-null identifiers alone do not establish provenance."
        ),
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"))
    report["report_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return report


def write_report(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/data-audits/latest.json"),
        help="ignored artifact path for the JSON report",
    )
    args = parser.parse_args()
    with get_engine().connect() as connection:
        report = build_audit(connection)
        connection.rollback()
    write_report(report, args.output)
    print(json.dumps({"output": str(args.output), **report["summary"]}, indent=2))


if __name__ == "__main__":
    main()
