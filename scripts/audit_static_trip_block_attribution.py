"""Persist a bounded, read-only static GTFS trip-to-block provenance audit.

This tool is intentionally source-only.  It joins immutable vehicle-linked
Trip Update history to static GTFS `trips` by the exact `(static_feed_id,
trip_gtfs_id)` identity.  It does not infer vehicle block assignment, block
continuity, fleet behavior, terminal recovery, headways, gaps, bunching, or a
Gate 5 result.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import Engine, text


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from transitpulse_api.database import get_engine  # noqa: E402
from transitpulse_ml.block_attribution import (  # noqa: E402
    attribution_records,
    records_sha256,
    summarize_attributions,
    write_new_report,
)


MAX_SOURCE_ROWS = 100_000


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamps must include an ISO-8601 offset")
    return parsed


def read_attribution_rows(
    engine: Engine,
    *,
    static_feed_id: str,
    start_at: datetime,
    end_at: datetime,
    route_ids: tuple[str, ...],
    max_source_rows: int,
) -> tuple[dict[str, str], list[dict[str, object]]]:
    """Read a strictly bounded source slice in a PostgreSQL READ ONLY transaction."""

    if end_at <= start_at:
        raise ValueError("end timestamp must be after start timestamp")
    if not 1 <= max_source_rows <= MAX_SOURCE_ROWS:
        raise ValueError(f"max_source_rows must be between 1 and {MAX_SOURCE_ROWS}")
    route_clause = ""
    params: dict[str, Any] = {
        "static_feed_id": static_feed_id,
        "start_at": start_at,
        "end_at": end_at,
        "limit": max_source_rows + 1,
    }
    if route_ids:
        route_clause = " AND o.route_gtfs_id = ANY(:route_ids)"
        params["route_ids"] = list(route_ids)
    statement = text(
        """
        WITH candidate AS (
            SELECT o.id AS source_observation_id, o.observation_key,
                   o.static_feed_id::text AS static_feed_id, o.observed_at,
                   o.recorded_at, o.source_timestamp, o.vehicle_id, o.trip_gtfs_id,
                   o.route_gtfs_id AS observed_route_gtfs_id
            FROM realtime_trip_observations AS o
            WHERE o.static_feed_id = CAST(:static_feed_id AS uuid)
              AND o.observed_at >= :start_at
              AND o.observed_at < :end_at
        """
        + route_clause
        + """
            ORDER BY o.observed_at, o.recorded_at, o.id
            LIMIT :limit
        ),
        selected_trip_ids AS (
            SELECT DISTINCT trip_gtfs_id FROM candidate
        ),
        other_feed_matches AS (
            SELECT t.gtfs_trip_id, COUNT(*) AS other_feed_trip_match_count
            FROM trips AS t
            JOIN selected_trip_ids AS selected ON selected.trip_gtfs_id = t.gtfs_trip_id
            WHERE t.feed_id <> CAST(:static_feed_id AS uuid)
            GROUP BY t.gtfs_trip_id
        )
        SELECT c.source_observation_id, c.observation_key, c.static_feed_id,
               c.observed_at, c.source_timestamp, c.vehicle_id, c.trip_gtfs_id,
               c.recorded_at,
               c.observed_route_gtfs_id, r.gtfs_route_id AS static_route_gtfs_id,
               t.block_id AS static_block_id,
               CASE WHEN t.id IS NULL THEN 0 ELSE 1 END AS exact_static_trip_match_count,
               COALESCE(other.other_feed_trip_match_count, 0) AS other_feed_trip_match_count
        FROM candidate AS c
        LEFT JOIN trips AS t
          ON t.feed_id = CAST(c.static_feed_id AS uuid)
         AND t.gtfs_trip_id = c.trip_gtfs_id
        LEFT JOIN routes AS r ON r.id = t.route_id
        LEFT JOIN other_feed_matches AS other ON other.gtfs_trip_id = c.trip_gtfs_id
        ORDER BY c.observed_at, c.recorded_at, c.source_observation_id
        """
    )
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        try:
            feed = connection.execute(
                text(
                    """
                    SELECT id::text AS static_feed_id, checksum_sha256, provider, source_url
                    FROM gtfs_feeds
                    WHERE id = CAST(:static_feed_id AS uuid)
                    """
                ),
                {"static_feed_id": static_feed_id},
            ).mappings().one_or_none()
            if feed is None:
                raise ValueError("static_feed_id is not an imported GTFS feed")
            rows = [dict(row) for row in connection.execute(statement, params).mappings()]
            if len(rows) > max_source_rows:
                raise ValueError("source_row_limit_reached: narrow the time or route scope")
        finally:
            connection.rollback()
    return dict(feed), rows


def build_report(
    *,
    static_feed: dict[str, str],
    rows: list[dict[str, object]],
    static_feed_id: str,
    start_at: datetime,
    end_at: datetime,
    route_ids: tuple[str, ...],
    max_source_rows: int,
    command: str,
) -> dict[str, object]:
    """Build a stable, source-only artifact from a bounded database result set."""

    records = attribution_records(rows)
    return {
        "schema_version": 1,
        "audit_kind": "static_trip_block_attribution",
        "scope": "SOURCE_ONLY_NOT_BLOCK_VALIDATION",
        "static_feed": static_feed,
        "source": {
            "table": "realtime_trip_observations",
            "static_feed_id": static_feed_id,
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "time_bound_field": "observed_at",
            "observed_at_semantics": "official Trip Update entity timestamp; rows without it fall outside this observed_at-bounded audit",
            "recorded_at_semantics": "database recorder insertion time retained per row; not used as this audit's query bound",
            "route_ids": list(route_ids),
            "max_source_rows": max_source_rows,
            "ordering": ["observed_at", "recorded_at", "source_observation_id"],
            "transaction": "SET TRANSACTION READ ONLY",
            "source_rows_returned": len(records),
        },
        "rules": [
            "Only exact (static_feed_id, trip_gtfs_id) joins may attribute a static block.",
            "Missing blocks, unmatched trips, cross-feed-only identifiers, and ambiguities are counted and never inferred.",
            "Per-route date summaries are observed calendar dates derived from observed_at; they are not GTFS service days.",
            "This artifact is static trip provenance only; it is not vehicle block assignment, continuity, fleet, recovery, headway, gap, bunching, or Gate 5 validation.",
        ],
        "summary": summarize_attributions(records),
        "attribution_records_sha256": records_sha256(records),
        "attributions": records,
        "source_code_sha256": {
            "block_attribution.py": sha256((REPO_ROOT / "apps" / "api" / "transitpulse_ml" / "block_attribution.py").read_bytes()).hexdigest(),
            "audit_static_trip_block_attribution.py": sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "reproducible_command": command,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-feed-id", required=True)
    parser.add_argument("--start", type=parse_datetime, required=True)
    parser.add_argument("--end", type=parse_datetime, required=True)
    parser.add_argument("--route", action="append", default=[])
    parser.add_argument("--max-source-rows", type=int, default=25_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"refusing to overwrite existing block attribution audit: {args.output}")
    route_ids = tuple(sorted(set(args.route)))
    command = (
        f".\\.venv\\Scripts\\python.exe scripts\\audit_static_trip_block_attribution.py "
        f"--static-feed-id {args.static_feed_id} --start {args.start.isoformat()} --end {args.end.isoformat()} "
        + " ".join(f"--route {route_id}" for route_id in route_ids)
        + f" --max-source-rows {args.max_source_rows} --output {args.output}"
    )
    static_feed, rows = read_attribution_rows(
        get_engine(),
        static_feed_id=args.static_feed_id,
        start_at=args.start,
        end_at=args.end,
        route_ids=route_ids,
        max_source_rows=args.max_source_rows,
    )
    report = build_report(
        static_feed=static_feed,
        rows=rows,
        static_feed_id=args.static_feed_id,
        start_at=args.start,
        end_at=args.end,
        route_ids=route_ids,
        max_source_rows=args.max_source_rows,
        command=command,
    )
    write_new_report(args.output, report)
    summary = report["summary"]
    print(
        json.dumps(
            {
                "scope": report["scope"],
                "source_rows": summary["source_row_count"],
                "attributed_rows": summary["attributed_row_count"],
                "unattributed_rows": summary["unattributed_row_count"],
                "static_blocks": summary["distinct_attributed_static_blocks"],
                "records_sha256": report["attribution_records_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
