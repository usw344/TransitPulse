"""Persist a read-only audit of strict complete-route evidence."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

from sqlalchemy import text


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
from transitpulse_api.database import get_engine  # noqa: E402
from transitpulse_ml.full_route_audit import audit_complete_route_coverage  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"refusing to overwrite existing audit: {args.output}")
    label_bytes = args.labels.read_bytes()
    labels = [json.loads(line) for line in label_bytes.decode("utf-8").splitlines() if line]
    pairs = {(str(label["static_feed_id"]), str(label["trip_gtfs_id"])) for label in labels}
    with get_engine().connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        result = connection.execute(
            text(
                """
                SELECT t.feed_id::text AS static_feed_id, t.gtfs_trip_id,
                       MIN(st.stop_sequence) AS first_sequence,
                       MAX(st.stop_sequence) AS last_sequence
                FROM trips AS t
                JOIN stop_times AS st ON st.trip_id = t.id
                GROUP BY t.feed_id, t.gtfs_trip_id
                """
            )
        )
        ranges = {
            (str(row.static_feed_id), str(row.gtfs_trip_id)): (int(row.first_sequence), int(row.last_sequence))
            for row in result
            if (str(row.static_feed_id), str(row.gtfs_trip_id)) in pairs
        }
        connection.rollback()
    coverage = audit_complete_route_coverage(labels, ranges)
    report = {
        "schema_version": 1,
        "audit_contract": "m7-strict-terminal-coverage-v1",
        "source_labels": str(args.labels),
        "source_labels_sha256": sha256(label_bytes).hexdigest(),
        "static_range_count": len(ranges),
        "rules": [
            "Source observations and GTFS tables are read in a READ ONLY transaction.",
            "A complete run must contain every consecutive static edge from the first to the last stop exactly once and in order.",
            "No discontinuity, missing edge, or observation gap is stitched or imputed.",
        ],
        **coverage,
        "reproducible_command": f".\\.venv\\Scripts\\python.exe scripts\\audit_full_route_coverage.py --labels {args.labels} --output {args.output}",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(report, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({key: report[key] for key in ("label_count", "label_run_count", "complete_terminal_to_terminal_run_count", "full_route_terminal_validation")}, indent=2))


if __name__ == "__main__":
    main()
