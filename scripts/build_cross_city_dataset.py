"""Build a versioned cross-city normalized route dataset.

Run:

    .venv\\Scripts\\python.exe scripts\\build_cross_city_dataset.py --version v1

Reads every archive named in ``artifacts/gtfs_sources/manifest.json``, pushes
each through the shared normalizer, and writes one immutable dataset directory
under ``artifacts/datasets/``.  The script refuses to overwrite an existing
version: a dataset is evidence a model was trained on, so a correction becomes
``v2`` rather than silently replacing the bytes ``v1``'s metrics refer to.

Every dataset directory contains:

``rows.jsonl``       one normalized record per line, provenance nested
``rows.csv``         the same rows flattened, for inspection and pandas
``manifest.json``    sources, checksums, schema version, command, row counts
``qa_report.json``   distributions, missingness, outlier flags, duplicate check

The QA report is produced by the build, not afterwards by hand, so a dataset
cannot exist without one.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from transitpulse_ml.gtfs_normalizer import (  # noqa: E402
    EXTRACTOR_VERSION,
    FeedSource,
    normalize_feed,
)
from transitpulse_ml.route_schema import (  # noqa: E402
    ROUTE_SCHEMA_VERSION,
    NormalizedRouteRecord,
)

SOURCE_DIR = REPO_ROOT / "artifacts" / "gtfs_sources"
MANIFEST_PATH = SOURCE_DIR / "manifest.json"
DATASET_ROOT = REPO_ROOT / "artifacts" / "datasets"

#: Numeric columns the QA report profiles.
_PROFILED = (
    "one_way_length_km",
    "stop_count",
    "stops_per_km",
    "mean_stop_spacing_m",
    "median_stop_spacing_m",
    "directness_ratio",
    "branch_count",
    "dominant_pattern_trip_share",
    "scheduled_runtime_minutes",
    "scheduled_commercial_speed_kmh",
    "peak_headway_minutes",
    "offpeak_headway_minutes",
    "median_headway_minutes",
    "headway_sample_count",
    "trips_per_day",
    "service_span_hours",
    "estimated_cycle_time_minutes",
    "estimated_recovery_minutes",
    "estimated_required_vehicles",
)

#: Plausibility envelope for conventional urban surface transit.  A value
#: outside these bounds is *flagged for review*, never silently dropped —
#: quietly deleting inconvenient rows is how a dataset comes to look better
#: than the data underneath it.
OUTLIER_RULES: dict[str, tuple[str, Any]] = {
    "speed_below_6_kmh": ("scheduled_commercial_speed_kmh", lambda v: v is not None and v < 6),
    "speed_above_60_kmh": ("scheduled_commercial_speed_kmh", lambda v: v is not None and v > 60),
    "length_above_60_km": ("one_way_length_km", lambda v: v is not None and v > 60),
    "length_below_1_km": ("one_way_length_km", lambda v: v is not None and v < 1),
    "stops_per_km_above_8": ("stops_per_km", lambda v: v is not None and v > 8),
    "stops_per_km_below_0_3": ("stops_per_km", lambda v: v is not None and v < 0.3),
    "runtime_below_3_min": ("scheduled_runtime_minutes", lambda v: v is not None and v < 3),
    "headway_below_1_min": ("median_headway_minutes", lambda v: v is not None and v < 1),
    "span_above_24_h": ("service_span_hours", lambda v: v is not None and v > 24),
}


def _profile(values: Sequence[float]) -> dict[str, float] | None:
    usable = sorted(v for v in values if v is not None)
    if not usable:
        return None

    def q(p: float) -> float:
        return float(usable[min(len(usable) - 1, int(p * len(usable)))])

    return {
        "n": len(usable),
        "min": float(usable[0]),
        "p05": q(0.05),
        "p25": q(0.25),
        "median": float(statistics.median(usable)),
        "p75": q(0.75),
        "p95": q(0.95),
        "max": float(usable[-1]),
        "mean": round(float(statistics.fmean(usable)), 4),
    }


def _flatten(record: NormalizedRouteRecord) -> dict[str, Any]:
    row = asdict(record)
    provenance = row.pop("provenance") or {}
    row["source_id"] = provenance.get("source_id")
    row["static_feed_id"] = provenance.get("static_feed_id")
    row["original_route_id"] = provenance.get("original_route_id")
    row["retrieved_at"] = (
        provenance["retrieved_at"].isoformat()
        if isinstance(provenance.get("retrieved_at"), datetime)
        else provenance.get("retrieved_at")
    )
    return row


def build_qa_report(records: list[NormalizedRouteRecord]) -> dict[str, Any]:
    """Everything a reviewer needs to decide whether to trust these rows."""

    by_city: dict[str, list[NormalizedRouteRecord]] = defaultdict(list)
    for record in records:
        by_city[record.city].append(record)

    bus = [r for r in records if r.route_kind == "bus"]

    # Duplicate logical routes: the same city/route/direction should appear at
    # most once per day type.  A repeat means the grouping key is not unique.
    identity = Counter(
        (r.city, r.provenance.original_route_id if r.provenance else r.route_label, r.direction_id, r.day_type)
        for r in records
    )
    duplicate_identities = [key for key, count in identity.items() if count > 1]

    # Near-identical rows across day types share geometry and often runtime.
    # They are legitimate observations of the same route on different days, but
    # they inflate the effective sample size, so a model consumer must know how
    # many there are before quoting an n.
    geometry_target = Counter(
        (r.city, round(r.one_way_length_km, 3), r.stop_count, r.scheduled_commercial_speed_kmh)
        for r in bus
    )
    repeated_geometry_target = sum(count - 1 for count in geometry_target.values() if count > 1)

    missingness = {
        field: round(
            sum(1 for r in records if getattr(r, field) is None) / max(1, len(records)), 4
        )
        for field in _PROFILED
    }

    outliers: dict[str, dict[str, Any]] = {}
    for name, (field, predicate) in OUTLIER_RULES.items():
        hits = [r for r in records if predicate(getattr(r, field))]
        outliers[name] = {
            "count": len(hits),
            "by_city": dict(Counter(r.city for r in hits)),
            "examples": [
                {
                    "city": r.city,
                    "route_label": r.route_label,
                    "direction_id": r.direction_id,
                    "day_type": r.day_type,
                    field: getattr(r, field),
                    "one_way_length_km": r.one_way_length_km,
                    "stop_count": r.stop_count,
                    "trips_per_day": r.trips_per_day,
                }
                for r in hits[:4]
            ],
        }

    return {
        "row_count": len(records),
        "cities": sorted(by_city),
        "rows_by_city": {city: len(rows) for city, rows in sorted(by_city.items())},
        "rows_by_route_kind": dict(Counter(r.route_kind for r in records)),
        "rows_by_day_type": dict(Counter(r.day_type for r in records)),
        "rows_by_direction": {str(k): v for k, v in Counter(r.direction_id for r in records).items()},
        "bus_rows_by_city": {
            city: sum(1 for r in rows if r.route_kind == "bus")
            for city, rows in sorted(by_city.items())
        },
        "distinct_routes_by_city": {
            city: len({r.provenance.original_route_id for r in rows if r.provenance})
            for city, rows in sorted(by_city.items())
        },
        "loop_rows": sum(1 for r in records if r.loop_route),
        "duplicate_identity_keys": len(duplicate_identities),
        "repeated_geometry_and_target_rows": repeated_geometry_target,
        "missingness_fraction": missingness,
        "distributions_all_kinds": {f: _profile([getattr(r, f) for r in records]) for f in _PROFILED},
        "distributions_bus_only": {f: _profile([getattr(r, f) for r in bus]) for f in _PROFILED},
        "distributions_bus_by_city": {
            city: {
                f: _profile([getattr(r, f) for r in rows if r.route_kind == "bus"])
                for f in (
                    "one_way_length_km",
                    "stops_per_km",
                    "scheduled_runtime_minutes",
                    "scheduled_commercial_speed_kmh",
                    "median_headway_minutes",
                    "service_span_hours",
                    "estimated_required_vehicles",
                )
            }
            for city, rows in sorted(by_city.items())
        },
        "outlier_flags": outliers,
        "ridership": {
            "rows_with_ridership": sum(1 for r in records if r.route_boardings_per_day is not None),
            "note": "No adopted agency publishes comparable route-level ridership; "
            "the dataset is deliberately built without it.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="dataset version directory, e.g. v1")
    parser.add_argument(
        "--reference-date",
        default=date.today().isoformat(),
        help="date the representative service days are chosen from",
    )
    parser.add_argument("--only", nargs="*", default=None, help="restrict to these source_ids")
    args = parser.parse_args()

    if not MANIFEST_PATH.exists():
        print("no source manifest; run scripts/fetch_gtfs_sources.py first", file=sys.stderr)
        return 2
    manifest = json.loads(MANIFEST_PATH.read_text())

    out_dir = DATASET_ROOT / f"cross_city_routes_{args.version}"
    if out_dir.exists():
        print(
            f"{out_dir} already exists — datasets are immutable, build the next version instead",
            file=sys.stderr,
        )
        return 2

    reference = date.fromisoformat(args.reference_date)
    records: list[NormalizedRouteRecord] = []
    extraction_reports: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for entry in manifest["feeds"]:
        if args.only and entry["source_id"] not in args.only:
            continue
        archive = SOURCE_DIR / entry["archive_name"]
        if not archive.exists():
            failures.append({"source_id": entry["source_id"], "error": "archive missing on disk"})
            continue
        source = FeedSource(
            source_id=entry["source_id"],
            city=entry["city"],
            agency=entry["agency"],
            source_url=entry["download_url"],
            licence=entry["licence"],
            archive_path=str(archive),
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            checksum_sha256=entry["sha256"],
        )
        print(f"normalizing {entry['source_id']} ...", flush=True)
        try:
            city_records, report = normalize_feed(source, reference_date=reference)
        except Exception as error:  # noqa: BLE001 - one bad feed must not lose the rest
            print(f"  !! failed: {error}", file=sys.stderr)
            failures.append({"source_id": entry["source_id"], "error": str(error)})
            continue
        records.extend(city_records)
        extraction_reports.append(report)
        print(f"  {report['records_emitted']:>5} rows  rejected={report['rejected']}")

    if not records:
        print("no records produced", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True)
    rows = [_flatten(record) for record in records]

    jsonl_path = out_dir / "rows.jsonl"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record.as_row(), ensure_ascii=False) + "\n")

    csv_path = out_dir / "rows.csv"
    fieldnames = list(rows[0].keys())
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    qa = build_qa_report(records)
    (out_dir / "qa_report.json").write_text(json.dumps(qa, indent=2), encoding="utf-8")

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest().upper()

    dataset_manifest = {
        "dataset": out_dir.name,
        "schema_version": ROUTE_SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "scripts/build_cross_city_dataset.py",
        "command": f"python scripts/build_cross_city_dataset.py --version {args.version} "
        f"--reference-date {args.reference_date}",
        "reference_date": args.reference_date,
        "row_count": len(records),
        "cities": qa["cities"],
        "rows_by_city": qa["rows_by_city"],
        "sources": [
            {
                key: entry[key]
                for key in (
                    "source_id",
                    "city",
                    "agency",
                    "country",
                    "download_url",
                    "licence",
                    "licence_url",
                    "sha256",
                    "bytes",
                    "retrieved_at",
                    "timestamp_basis",
                    "declared_agency",
                    "calendar_start",
                    "calendar_end",
                )
            }
            for entry in manifest["feeds"]
            if not args.only or entry["source_id"] in args.only
        ],
        "extraction_reports": extraction_reports,
        "failures": failures,
        "file_checksums": {
            "rows.jsonl": digest(jsonl_path),
            "rows.csv": digest(csv_path),
            "qa_report.json": digest(out_dir / "qa_report.json"),
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(dataset_manifest, indent=2), encoding="utf-8")

    print(f"\n{len(records)} rows across {len(qa['cities'])} cities -> {out_dir.relative_to(REPO_ROOT)}")
    for city, count in qa["rows_by_city"].items():
        print(f"  {city:14s} {count:5d} rows ({qa['bus_rows_by_city'][city]} bus)")
    if failures:
        print(f"  FAILURES: {failures}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
