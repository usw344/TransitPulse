"""Create prediction-time-safe chronological rows from a verified label artifact."""

from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output exists: {args.output}")
    label_bytes = args.labels.read_bytes()
    raw_rows = [json.loads(line) for line in label_bytes.decode("utf-8").splitlines() if line]
    rows = []
    for row in raw_rows:
        prediction_at = datetime.fromisoformat(row["observed_start"])
        rows.append({
            "prediction_at": prediction_at.isoformat(), "target_travel_seconds": row["travel_seconds"],
            # A label may be interpolated from a later source observation.  It
            # must not enter any causal historical feature/history calculation
            # until M2 says that its outcome was actually available.
            "outcome_available_at": row["outcome_available_at"],
            "scheduled_seconds": row["scheduled_seconds"], "distance_m": row["distance_m"],
            "route_gtfs_id": row["route_gtfs_id"], "direction_id": row["direction_id"],
            "from_stop_id": row["from_stop_id"], "to_stop_id": row["to_stop_id"],
            "hour": prediction_at.hour, "weekday": prediction_at.weekday(), "service_day": row["service_day"],
        })
    rows.sort(key=lambda row: datetime.fromisoformat(row["prediction_at"]))
    days = sorted({row["service_day"] for row in rows})
    # Three non-overlapping service days are the minimum defensible simple split.
    split_note = None
    if len(days) >= 3:
        train_end, validation_end = days[-3], days[-2]
        for row in rows:
            row["split"] = "train" if row["service_day"] <= train_end else "validation" if row["service_day"] <= validation_end else "test"
    else:
        split_note = "Insufficient distinct service days for a chronological train/validation/test split."
        for row in rows:
            row["split"] = "unsplittable"
    args.output.mkdir(parents=True)
    data = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows).encode("utf-8")
    (args.output / "rows.jsonl").write_bytes(data)
    split_counts = {split: sum(row["split"] == split for row in rows) for split in ("train", "validation", "test", "unsplittable")}
    split_boundaries = {
        split: {"first_prediction_at": min((row["prediction_at"] for row in rows if row["split"] == split), default=None), "last_prediction_at": max((row["prediction_at"] for row in rows if row["split"] == split), default=None), "service_days": sorted({row["service_day"] for row in rows if row["split"] == split})}
        for split in split_counts if split_counts[split]
    }
    manifest = {
        "schema_version": 3,
        "dataset_contract": "m3-prediction-safe-chronological-v3-causal-availability",
        "source_labels": str(args.labels),
        "source_labels_sha256": sha256(label_bytes).hexdigest(),
        "row_count": len(rows),
        "service_days": days,
        "prediction_timestamp": "observed_start",
        "outcome_available_at": "causal interpolated-label availability bound from M2 (evaluation metadata only; never a model feature)",
        "target": "travel_seconds",
        "feature_fields": ["scheduled_seconds", "distance_m", "route_gtfs_id", "direction_id", "from_stop_id", "to_stop_id", "hour", "weekday", "service_day"],
        "feature_policy": "static GTFS and clock features available at prediction timestamp only",
        "split_note": split_note,
        "split_counts": split_counts,
        "split_boundaries": split_boundaries,
        "target_summary_seconds": {"min": min(row["target_travel_seconds"] for row in rows), "mean": mean(row["target_travel_seconds"] for row in rows), "max": max(row["target_travel_seconds"] for row in rows)},
        "missing_feature_counts": {field: sum(row[field] is None for row in rows) for field in ("scheduled_seconds", "distance_m", "route_gtfs_id", "direction_id", "from_stop_id", "to_stop_id")},
        "rows_sha256": sha256(data).hexdigest(),
        "reproducible_command": f".\\.venv\\Scripts\\python.exe scripts\\build_model_dataset.py --labels {args.labels} --output {args.output}",
    }
    (args.output / "manifest.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
