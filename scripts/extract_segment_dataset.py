"""Create a reproducible, bounded segment-label dataset artifact."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from collections import Counter


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from transitpulse_api.database import get_engine  # noqa: E402
from transitpulse_ml.extractor import ExtractionConfig, SegmentLabel, extract_labels  # noqa: E402


EXTRACTION_VERSION = "segment-arrival-v3-causal-availability"


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamps must include an ISO-8601 offset")
    return parsed


def _label_record(label: SegmentLabel) -> dict[str, object]:
    traversal = label.traversal
    return {
        "static_feed_id": str(label.static_feed_id),
        "service_day": label.service_day.isoformat(),
        "trip_gtfs_id": label.trip_gtfs_id,
        "route_gtfs_id": label.route_gtfs_id,
        "direction_id": label.direction_id,
        "vehicle_id": label.vehicle_id,
        "from_stop_id": traversal.from_stop_id,
        "to_stop_id": traversal.to_stop_id,
        "from_stop_sequence": traversal.from_stop_sequence,
        "to_stop_sequence": traversal.to_stop_sequence,
        "observed_start": traversal.observed_start.isoformat(),
        "observed_end": traversal.observed_end.isoformat(),
        "outcome_available_at": traversal.outcome_available_at.isoformat(),
        "travel_seconds": traversal.travel_seconds,
        "scheduled_seconds": traversal.scheduled_seconds,
        "distance_m": traversal.distance_m,
        "max_lateral_error_m": traversal.max_lateral_error_m,
        "max_bracketing_gap_seconds": traversal.max_bracketing_gap_seconds,
    }


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "p50": None, "p90": None, "p99": None, "max": None}
    ordered = sorted(values)

    def percentile(value: float) -> float:
        index = round((len(ordered) - 1) * value)
        return ordered[index]

    return {
        "count": len(ordered),
        "min": ordered[0],
        "p50": percentile(0.50),
        "p90": percentile(0.90),
        "p99": percentile(0.99),
        "max": ordered[-1],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--start", required=True, type=_parse_datetime)
    parser.add_argument("--end", required=True, type=_parse_datetime)
    parser.add_argument("--route", action="append", default=[])
    parser.add_argument("--max-source-rows", type=int, default=100_000)
    parser.add_argument("--include-terminal-segments", action="store_true", help="retain only quality-qualified edges touching a scheduled terminal for a coverage audit")
    parser.add_argument("--output-root", type=Path, default=ROOT / "artifacts" / "datasets")
    args = parser.parse_args()

    output_dir = args.output_root / args.dataset_id
    if output_dir.exists():
        parser.error(f"dataset output already exists: {output_dir}")
    config = ExtractionConfig(
        start_at=args.start,
        end_at=args.end,
        route_ids=tuple(args.route),
        max_source_rows=args.max_source_rows,
        include_terminal_segments=args.include_terminal_segments,
    )
    labels, census = extract_labels(get_engine(), config)
    output_dir.mkdir(parents=True)
    labels_path = output_dir / "labels.jsonl"
    digest = sha256()
    with labels_path.open("wb") as handle:
        for label in labels:
            encoded = (json.dumps(_label_record(label), sort_keys=True, separators=(",", ":")) + "\n").encode()
            handle.write(encoded)
            digest.update(encoded)
    route_counts = Counter(label.route_gtfs_id for label in labels)
    segment_counts = Counter(
        f"{label.route_gtfs_id}:{label.traversal.from_stop_id}->{label.traversal.to_stop_id}"
        for label in labels
    )
    manifest = {
        "schema_version": 1,
        "dataset_id": args.dataset_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "extraction_version": EXTRACTION_VERSION,
        "label_definition": "directed consecutive scheduled-stop arrival-to-arrival travel time",
        "source_bounds": {"start": args.start.isoformat(), "end": args.end.isoformat()},
        "routes": sorted(args.route),
        "max_source_rows": args.max_source_rows,
        "field_schema": {
            "labels.jsonl": [
                "static_feed_id", "service_day", "trip_gtfs_id", "route_gtfs_id", "direction_id",
                "vehicle_id", "from_stop_id", "to_stop_id", "from_stop_sequence", "to_stop_sequence",
                "observed_start", "observed_end", "outcome_available_at", "travel_seconds", "scheduled_seconds", "distance_m",
                "max_lateral_error_m", "max_bracketing_gap_seconds",
            ]
        },
        "quality_rules": {
            "max_lateral_error_m": config.max_lateral_error_m,
            "max_observation_gap_seconds": config.max_observation_gap_seconds,
            "max_scheduled_segment_seconds": config.max_scheduled_segment_seconds,
            "max_group_observations": config.max_group_observations,
            "max_progress_regression_m": 35.0,
            "max_speed_mps": 40.0,
            "ambiguity_error_delta_m": 8.0,
            "ambiguity_progress_separation_m": 100.0,
            "timezone": config.timezone_name or "feed agency timezone",
            "route_match": "non-null realtime route must equal static trip route",
            "terminal_rule": "include quality-qualified edges touching stop_times pickup_type=1 or drop_off_type=1 for a terminal-coverage audit" if args.include_terminal_segments else "exclude edges touching stop_times pickup_type=1 or drop_off_type=1",
            "ambiguous_shape_projection": "rejected",
            "raw_observations": "read-only",
            "outcome_availability": "max(recorded_at/observed_at) of the later brackets required to interpolate both stop crossings",
        },
        "candidate_observations": census.candidate_observations,
        "candidate_runs": census.candidate_runs,
        "candidate_traversals": census.candidate_traversals,
        "accepted_labels": census.accepted_labels,
        "rejection_census": dict(sorted(census.rejected.items())),
        "feed_ids": sorted({str(label.static_feed_id) for label in labels}),
        "trip_count": len({label.trip_gtfs_id for label in labels}),
        "vehicle_count": len({label.vehicle_id for label in labels}),
        "segment_count": len({(label.traversal.from_stop_id, label.traversal.to_stop_id) for label in labels}),
        "per_route_accepted_labels": dict(sorted(route_counts.items())),
        "per_segment_accepted_labels": dict(sorted(segment_counts.items())),
        "travel_seconds_distribution": _distribution([label.traversal.travel_seconds for label in labels]),
        "bracketing_gap_seconds_distribution": _distribution(
            [label.traversal.max_bracketing_gap_seconds for label in labels]
        ),
        "labels_sha256": digest.hexdigest(),
        "chronological_split": None,
        "chronological_split_note": "Not created until Gate 2 extraction validation passes.",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
