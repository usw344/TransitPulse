"""Persist the served scenario estimator and its calibrated intervals.

Run:

    .venv\\Scripts\\python.exe scripts\\build_scenario_estimator.py --version v1

Three artifacts come out of one run, and they must come from one run so they
cannot disagree:

``model.pkl``      the estimator, fitted on **all** adopted cities
``metadata.json``  feature order, interval calibration, OOD envelope, provenance
``calibration.json``  the raw held-out evidence the intervals were read from

The model served to users is fitted on every city, including Edmonton, because
there is no reason to withhold data from the model a planner actually uses.  But
every *quality claim* — the interval widths, the confidence thresholds, the
skill and sign-agreement figures — is measured leave-one-city-out, on cities the
fitted-for-measurement model had never seen.  Fitting on everything and then
quoting in-sample accuracy would be the single easiest way to make this tool
look better than it is.

Interval calibration
--------------------
Pairs of routes are drawn within each held-out city, the model predicts the
speed difference between them, and the absolute error of that difference is
recorded.  Errors are bucketed by how far apart the two designs are in
standardized feature space — the same distance a scenario's own edits produce,
so it is computable at serve time.  A scenario that trims a few stops sits close
to its baseline and gets the narrow band measured on near-identical pairs; one
that halves the route sits far away and gets the wide band those deserve.  As
the change goes to nothing the distance goes to zero and the band collapses,
which is the behaviour a planner should expect and which bucketing by predicted
magnitude failed to produce.

The band is deliberately **conservative**.  Its evidence comes from pairs of
*different* routes, so each measurement contains two independent route-identity
offsets — corridor, traffic, terminal layout — while an anchored scenario
estimate carries none, because the route's own offset cancels between the two
predictions.  That is why the measured band does not reach zero even at zero
distance.  Erring wide is the right direction for a planning tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from transitpulse_ml.cross_city_features import (  # noqa: E402
    FORBIDDEN_FEATURES,
    NUMERIC_FEATURES,
    TARGET,
    CohortRule,
    build_matrix,
    feature_names,
)
from transitpulse_ml.route_schema import ROUTE_SCHEMA_VERSION  # noqa: E402
from transitpulse_ml.scenario_estimator import FROZEN_IN_DELTA  # noqa: E402

SEED = 20260912
DATASET_ROOT = REPO_ROOT / "artifacts" / "datasets"
EXPERIMENT_ROOT = REPO_ROOT / "artifacts" / "experiments"
MODEL_ROOT = REPO_ROOT / "artifacts" / "models"

#: Upper bounds of the standardized feature-distance buckets the interval is
#: read from.  Chosen to put a usable number of pairs in each bucket rather than
#: to produce a flattering curve; realized counts are written to the artifact.
DISTANCE_BUCKET_EDGES = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, float("inf"))

PAIRS_PER_CITY = 40000


def make_model() -> Pipeline:
    """Ridge — the leave-one-city-out winner, not the most complex candidate.

    Boosting and a random forest both lost to it on held-out cities, and a small
    residual MLP was evaluated on the same folds for the same reason.  The model
    that transfers best is the one that ships.
    """

    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", Ridge(alpha=10.0)),
        ]
    )


def _fill(matrix, median):
    filled = matrix.copy()
    indices = np.where(~np.isfinite(filled))
    filled[indices] = np.take(median, indices[1])
    return filled


def _scaling(rows: Sequence[Mapping[str, Any]]):
    """Median fill plus mean/std used only to define the distance metric.

    This is not a model input.  It exists so that "how different are these two
    designs" means the same thing when the bands are calibrated and when a
    scenario is served.
    """

    matrix, _, _ = build_matrix(rows)
    median = np.nanmedian(matrix, axis=0)
    median = np.where(np.isfinite(median), median, 0.0)
    filled = _fill(matrix, median)
    std = filled.std(axis=0)
    return median, filled.mean(axis=0), np.where(std < 1e-8, 1.0, std)


def calibrate_intervals(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rng = np.random.default_rng(SEED)
    cities = sorted({row["city"] for row in rows})
    median, mean, std = _scaling(rows)
    names = feature_names()
    frozen_columns = [names.index(name) for name in sorted(FROZEN_IN_DELTA) if name in names]

    distances: list[np.ndarray] = []
    errors: list[np.ndarray] = []
    signed: list[np.ndarray] = []

    for held_out in cities:
        train_rows = [row for row in rows if row["city"] != held_out]
        test_rows = [row for row in rows if row["city"] == held_out]
        x_train, y_train, _ = build_matrix(train_rows)
        x_test, y_test, _ = build_matrix(test_rows)
        model = make_model()
        model.fit(x_train, y_train)
        count = len(test_rows)
        sample = min(PAIRS_PER_CITY, count * (count - 1) // 2)
        left = rng.integers(0, count, size=sample * 2)
        right = rng.integers(0, count, size=sample * 2)
        keep = left != right
        left, right = left[keep][:sample], right[keep][:sample]

        # Calibrate the operator that actually ships: the served estimator holds
        # the confounded service-intensity inputs at the current route's values
        # on both sides of the difference. Calibrating with them free measured a
        # different estimator, and indexed 26% of pairs into a narrower band than
        # the evidence they came from.
        current = x_test[left]
        proposed = x_test[right].copy()
        proposed[:, frozen_columns] = current[:, frozen_columns]

        predicted_delta = np.asarray(model.predict(proposed), dtype=np.float64) - np.asarray(
            model.predict(current), dtype=np.float64
        )
        actual_delta = y_test[right] - y_test[left]
        standard_current = (_fill(current, median) - mean) / std
        standard_proposed = (_fill(proposed, median) - mean) / std
        distances.append(np.linalg.norm(standard_proposed - standard_current, axis=1))
        errors.append(np.abs(predicted_delta - actual_delta))
        signed.append(predicted_delta - actual_delta)

    distance = np.concatenate(distances)
    error = np.concatenate(errors)
    signed_error = np.concatenate(signed)

    buckets: list[dict[str, Any]] = []
    low = 0.0
    for high in DISTANCE_BUCKET_EDGES:
        mask = (distance > low) & (distance <= high) if np.isfinite(high) else distance > low
        if mask.sum() < 200:
            # Too few pairs to read a 90th percentile from; widen the previous
            # bucket rather than publish a quantile of a handful of points.
            if buckets:
                buckets[-1]["max_feature_distance"] = high if np.isfinite(high) else 1e9
            low = high
            continue
        subset = error[mask]
        buckets.append(
            {
                "max_feature_distance": high if np.isfinite(high) else 1e9,
                "pairs": int(mask.sum()),
                "abs_error_p50_kmh": float(np.quantile(subset, 0.50)),
                "abs_error_p80_kmh": float(np.quantile(subset, 0.80)),
                "abs_error_p90_kmh": float(np.quantile(subset, 0.90)),
            }
        )
        low = high

    return {
        "buckets": buckets,
        "distance_scaling": {
            "median": median.tolist(),
            "mean": mean.tolist(),
            "std": std.tolist(),
        },
        "pairs_total": int(distance.size),
        "overall_abs_error_p80_kmh": float(np.quantile(error, 0.80)),
        "overall_abs_error_p90_kmh": float(np.quantile(error, 0.90)),
        "signed_error_median_kmh": float(np.median(signed_error)),
        "protocol": "leave-one-city-out; pairs drawn only within the held-out city; "
        "bucketed by standardized feature distance between the two designs; the "
        "frozen operator is used, matching what is served",
        "distance_metric_note": "the median/mean/std defining the distance metric are "
        "computed once over all cohort rows, including held-out ones. This is "
        "target-free and only fixes a length scale, but it is not fold-local and is "
        "recorded here rather than left implicit.",
        "conservatism_note": "each measured pair carries two independent route-identity "
        "offsets that an anchored scenario estimate cancels, so these bands are upper bounds",
    }


def feature_envelope(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """Robust per-feature range, for the out-of-distribution guard.

    The 1st and 99th percentiles are used rather than min/max so one mislabelled
    60 km express route does not silently widen the envelope until nothing is
    ever flagged.
    """

    envelope: dict[str, dict[str, float]] = {}
    for name in NUMERIC_FEATURES:
        values = np.array(
            [row[name] for row in rows if row.get(name) is not None], dtype=np.float64
        )
        if values.size < 50:
            continue
        envelope[name] = {
            "low": float(np.quantile(values, 0.01)),
            "high": float(np.quantile(values, 0.99)),
            "median": float(np.median(values)),
        }
    return envelope


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="cross_city_routes_v3")
    parser.add_argument("--version", default="v3")
    parser.add_argument("--baselines", default="cross_city_baselines_v3")
    parser.add_argument("--deltas", default="cross_city_scenario_deltas_v3")
    args = parser.parse_args()

    dataset_dir = DATASET_ROOT / args.dataset
    all_rows = [json.loads(line) for line in (dataset_dir / "rows.jsonl").open(encoding="utf-8")]
    cohort = CohortRule()
    rows = [row for row in all_rows if cohort.admits(row)]

    out_dir = MODEL_ROOT / f"scenario_estimator_{args.version}"
    if out_dir.exists():
        print(f"{out_dir} exists — model artifacts are immutable, use a new version", file=sys.stderr)
        return 2

    names = feature_names()
    leaked = FORBIDDEN_FEATURES & set(names)
    if leaked:
        print(f"REFUSING: target-derived features present: {leaked}", file=sys.stderr)
        return 1

    print(f"calibrating intervals on {len(rows)} rows, {len({r['city'] for r in rows})} cities ...")
    calibration = calibrate_intervals(rows)

    print("fitting served model on all cities ...")
    x, y, _ = build_matrix(rows)
    model = make_model()
    model.fit(x, y)

    out_dir.mkdir(parents=True)
    with (out_dir / "model.pkl").open("wb") as handle:
        pickle.dump(model, handle)

    baselines_path = EXPERIMENT_ROOT / args.baselines / "report.json"
    deltas_path = EXPERIMENT_ROOT / args.deltas / "report.json"
    baselines = json.loads(baselines_path.read_text()) if baselines_path.exists() else None
    deltas = json.loads(deltas_path.read_text()) if deltas_path.exists() else None

    # The SERVED operator's arm, never the free one. Quoting the free
    # operator's skill describes an estimator that does not ship.
    ridge_delta = None
    if deltas:
        ridge_delta = next(
            (r for r in deltas["results"] if r["operator"].startswith("frozen")), None
        )

    metadata = {
        "version": args.version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "scripts/build_scenario_estimator.py",
        "command": f"python scripts/build_scenario_estimator.py --dataset {args.dataset} "
        f"--version {args.version}",
        "seed": SEED,
        "dataset": args.dataset,
        "dataset_rows_checksum": json.loads((dataset_dir / "manifest.json").read_text())[
            "file_checksums"
        ]["rows.jsonl"],
        "schema_version": ROUTE_SCHEMA_VERSION,
        "target": TARGET,
        "model_family": "Ridge(alpha=10) over median-imputed, standardized features",
        "model_selection": "leave-one-city-out; ridge beat gradient boosting, random forest, "
        "a decision tree, a transparent physical stop-spacing model and the global median",
        "fitted_on": "all adopted cities, including Edmonton",
        "quality_measured_on": "leave-one-city-out folds only",
        "feature_names": names,
        "forbidden_features": sorted(FORBIDDEN_FEATURES),
        "cohort_rule": cohort.describe(),
        "training_rows": len(rows),
        "training_cities": sorted({row["city"] for row in rows}),
        "delta_error_buckets": calibration["buckets"],
        "distance_scaling": calibration["distance_scaling"],
        "interval_basis": "held-out within-city absolute error of the predicted speed "
        "difference, bucketed by standardized feature distance between the two designs; "
        "the band is the 80th percentile of that error, and is conservative because each "
        "measured pair carries two route-identity offsets that anchoring cancels",
        "feature_envelope": feature_envelope(rows),
        "held_out_absolute_accuracy": (
            {
                "model": baselines["winner"],
                "mae_kmh": baselines["ranking_by_mae_kmh"][0]["mae_kmh"],
                "per_city_mae_kmh": {
                    city: values["mae_kmh"]
                    for city, values in baselines["primary"]["per_city"][baselines["winner"]].items()
                },
            }
            if baselines
            else None
        ),
        "held_out_change_skill": (
            {
                "operator": ridge_delta["operator"],
                "skill_vs_do_nothing": ridge_delta["pooled"]["skill_vs_do_nothing"],
                "sign_agreement_on_material_changes": ridge_delta["pooled"][
                    "sign_agreement_on_material_changes"
                ],
                "delta_mae_kmh": ridge_delta["pooled"]["delta_model_mae_kmh"],
                "pooled_caveat": "pooled over pairs of arbitrary routes, which are dominated "
                "by wholesale substitutions. For the small edits this product serves, see "
                "by_design_distance: skill is near zero below distance ~1.5.",
                # Skill in the SAME buckets the intervals use, so a band width and
                # a skill figure always describe the same size of change.
                "by_design_distance": [
                    {
                        "max_feature_distance": bucket["max_feature_distance"],
                        "pairs": bucket["pairs"],
                        "skill_vs_do_nothing": bucket["skill_vs_do_nothing"],
                        "sign_agreement": bucket["sign_agreement_on_material_changes"],
                    }
                    for bucket in ridge_delta["by_design_distance"]
                ],
                "per_city": {
                    city: {
                        "skill_vs_do_nothing": values["skill_vs_do_nothing"],
                        "sign_agreement": values["sign_agreement_on_material_changes"],
                    }
                    for city, values in ridge_delta["per_city"].items()
                },
            }
            if ridge_delta
            else None
        ),
        "distinct_route_directions": (deltas or {}).get("distinct_route_directions"),
        "effective_sample_note": (deltas or {}).get("effective_sample_note"),
        "experiments": {
            "baselines": args.baselines,
            "scenario_deltas": args.deltas,
        },
        "limitations": [
            "No ridership, wait time, crowding or passenger benefit is modelled; no adopted "
            "agency publishes comparable route-level demand.",
            "Estimates describe scheduled service design, not observed operations.",
            "The model has no knowledge of streets, signals, turns or traffic; it cannot "
            "evaluate rerouting a bus onto a different road.",
            "Held-out skill on within-city change varies widely by city, and is negative "
            "on Winnipeg, whose routes are unusually uniform.",
            "For small design edits — the case this product actually serves — the model is "
            "barely better than assuming the change has no effect. Skill rises only for "
            "large redesigns. See held_out_change_skill.by_design_distance.",
            "Route length is kept as a live input because changing it really does change "
            "which roads a route uses, but its fitted relationship with speed is "
            "correlational: the model cannot know which end of the route you trimmed.",
            "This is a planning approximation, not a substitute for a running-time study.",
        ],
    }
    # A corrupted or swapped model.pkl would otherwise go undetected, while the
    # dataset it was fitted on is checksummed file by file.
    metadata["model_pkl_sha256"] = hashlib.sha256(
        (out_dir / "model.pkl").read_bytes()
    ).hexdigest().upper()
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (out_dir / "calibration.json").write_text(json.dumps(calibration, indent=2), encoding="utf-8")

    print(f"\ninterval calibration ({calibration['pairs_total']:,} held-out pairs)")
    for bucket in calibration["buckets"]:
        edge = bucket["max_feature_distance"]
        label = f"<= {edge:g}" if edge < 1e8 else "larger"
        print(f"  design distance {label:>8s} : band +/-{bucket['abs_error_p80_kmh']:.2f} "
              f"(p90 {bucket['abs_error_p90_kmh']:.2f})  from {bucket['pairs']:,} pairs")
    print(f"\n-> {out_dir.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
