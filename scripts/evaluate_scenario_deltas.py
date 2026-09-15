"""Can the model predict the *effect of a design change*, and in which regime?

This is the question the SCENARIOS product actually asks, and it is not the one
the leave-one-city-out MAE answers.

The baseline evaluation showed a large per-city bias: held out, Montreal's speeds
are over-predicted by about 3.7 km/h and Minneapolis's under-predicted by about
3.9.  Cities differ in ways the features do not capture — signal priority, street
grid, schedule padding convention.  So an *absolute* prediction for one Edmonton
route carries that whole city offset.

A scenario does not need the absolute level.  It compares a proposed design
against a route whose speed is **already measured**, so the difference cancels
the offset::

    scenario_speed = measured_current_speed + (predict(scenario) - predict(current))

This script measures whether that difference is trustworthy, by pairing routes
*within* each held-out city.

Two things this script gets right that an earlier version did not
-----------------------------------------------------------------

**1. It measures the operator that actually ships.**  The served estimator holds
the service-intensity features in ``FROZEN_IN_DELTA`` at the current route's
values on both sides of the difference, because their fitted relationship with
speed is confounded rather than causal.  Evaluating with those features free
measures a different estimator and flatters the one that ships.  Both operators
are reported here; ``frozen`` is the served one and is what the model card
must quote.

**2. It reports skill by size of change, not just pooled.**  Pairing arbitrary
routes is dominated by wholesale substitutions — two unrelated routes — while a
planner trims a few stops.  Pooled skill is therefore carried by a regime the
product does not serve.  Skill is broken out by the same standardized design
distance the interval calibration uses, so the answer to "is this better
than assuming nothing happens?" can be given for the change actually made.

Reference points:

``delta_zero``   assume a design change does nothing.  A scenario tool that
                 cannot beat this is worse than telling the planner nothing.
``delta_model``  the model's predicted difference.

Sign agreement is reported alongside the error, because a planner's first
question is "does this make the route faster or slower?", and an estimator that
gets the direction wrong is worse than one that is merely imprecise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from transitpulse_ml.cross_city_features import (  # noqa: E402
    CohortRule,
    build_matrix,
    feature_names,
)
from transitpulse_ml.scenario_estimator import FROZEN_IN_DELTA  # noqa: E402

SEED = 20260912
DATASET_ROOT = REPO_ROOT / "artifacts" / "datasets"
EXPERIMENT_ROOT = REPO_ROOT / "artifacts" / "experiments"

PAIRS_PER_CITY = 40000

#: Identical to the interval calibration's buckets, so a skill figure and a band
#: width always describe the same regime.
DISTANCE_BUCKET_EDGES = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, float("inf"))


def make_model():
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", Ridge(alpha=10.0)),
        ]
    )


def _fill(matrix: np.ndarray, median: np.ndarray) -> np.ndarray:
    filled = matrix.copy()
    indices = np.where(~np.isfinite(filled))
    filled[indices] = np.take(median, indices[1])
    return filled


def summarize(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    """Model error against the do-nothing reference, plus directional accuracy."""

    if actual.size == 0:
        return {"pairs": 0}
    error_model = np.abs(predicted - actual)
    error_zero = np.abs(actual)
    # Direction is only a meaningful question when there is a real difference to
    # get right; near-zero true deltas are excluded from the sign statistic so it
    # is not dominated by coin flips on noise.
    material = np.abs(actual) >= 1.0
    zero_mae = float(np.mean(error_zero))
    return {
        "pairs": int(actual.size),
        "delta_model_mae_kmh": float(np.mean(error_model)),
        "delta_zero_mae_kmh": zero_mae,
        "skill_vs_do_nothing": float(1.0 - np.mean(error_model) / zero_mae) if zero_mae else float("nan"),
        "delta_model_median_ae_kmh": float(np.median(error_model)),
        "delta_p90_ae_kmh": float(np.quantile(error_model, 0.90)),
        "sign_agreement_on_material_changes": (
            float(np.mean(np.sign(predicted[material]) == np.sign(actual[material])))
            if material.any()
            else float("nan")
        ),
        "material_pairs": int(material.sum()),
        "correlation": float(np.corrcoef(actual, predicted)[0, 1]) if actual.size > 1 else float("nan"),
    }


def evaluate(rows: Sequence[Mapping[str, Any]], *, freeze: bool) -> dict[str, Any]:
    """Leave-one-city-out pair evaluation.

    ``freeze=True`` reproduces the served operator: the right-hand row's
    service-intensity columns are overwritten with the left-hand row's, so those
    features contribute nothing to the predicted difference — exactly what
    ``ScenarioEstimator._aligned_features`` does at serve time.
    """

    rng = np.random.default_rng(SEED)
    names = feature_names()
    frozen_columns = [names.index(name) for name in sorted(FROZEN_IN_DELTA) if name in names]
    cities = sorted({row["city"] for row in rows})

    # Distance metric statistics. Target-free and global on purpose: the metric
    # only has to mean the same thing here as it does at serve time, where no
    # per-fold statistics exist. Recorded so the choice is auditable.
    all_matrix, _, _ = build_matrix(rows)
    global_median = np.nan_to_num(np.nanmedian(all_matrix, axis=0))
    filled_all = _fill(all_matrix, global_median)
    global_mean = filled_all.mean(axis=0)
    global_std = np.where(filled_all.std(axis=0) < 1e-8, 1.0, filled_all.std(axis=0))

    per_city: dict[str, Any] = {}
    pooled_actual: list[np.ndarray] = []
    pooled_predicted: list[np.ndarray] = []
    pooled_distance: list[np.ndarray] = []

    for held_out in cities:
        train_rows = [row for row in rows if row["city"] != held_out]
        test_rows = [row for row in rows if row["city"] == held_out]
        if len(test_rows) < 4:
            continue
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

        current = x_test[left]
        proposed = x_test[right].copy()
        if freeze:
            proposed[:, frozen_columns] = current[:, frozen_columns]

        predicted_delta = np.asarray(model.predict(proposed), dtype=np.float64) - np.asarray(
            model.predict(current), dtype=np.float64
        )
        actual_delta = y_test[right] - y_test[left]

        standard_current = (_fill(current, global_median) - global_mean) / global_std
        standard_proposed = (_fill(proposed, global_median) - global_mean) / global_std
        distance = np.linalg.norm(standard_proposed - standard_current, axis=1)

        pooled_actual.append(actual_delta)
        pooled_predicted.append(predicted_delta)
        pooled_distance.append(distance)
        per_city[held_out] = summarize(actual_delta, predicted_delta)

    actual = np.concatenate(pooled_actual)
    predicted = np.concatenate(pooled_predicted)
    distance = np.concatenate(pooled_distance)

    # Skill by size of change, in the buckets the intervals use.
    buckets: list[dict[str, Any]] = []
    low = 0.0
    for high in DISTANCE_BUCKET_EDGES:
        mask = (distance > low) & (distance <= high) if np.isfinite(high) else distance > low
        if mask.sum() >= 200:
            buckets.append(
                {
                    "max_feature_distance": high if np.isfinite(high) else 1e9,
                    **summarize(actual[mask], predicted[mask]),
                }
            )
        elif buckets:
            buckets[-1]["max_feature_distance"] = high if np.isfinite(high) else 1e9
        low = high

    return {
        "operator": "frozen (production)" if freeze else "free (all features vary)",
        "frozen_features": sorted(FROZEN_IN_DELTA) if freeze else [],
        "pooled": summarize(actual, predicted),
        "per_city": per_city,
        "by_design_distance": buckets,
        "distance_metric": "standardized Euclidean over the model's own feature vector; "
        "median/mean/std computed once over all cohort rows (target-free)",
        "delta_residual_quantiles": {
            f"p{int(q * 100):02d}": float(np.quantile(predicted - actual, q))
            for q in (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
        }
        | {
            "abs_p80": float(np.quantile(np.abs(predicted - actual), 0.80)),
            "abs_p90": float(np.quantile(np.abs(predicted - actual), 0.90)),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="cross_city_routes_v3")
    parser.add_argument("--experiment", default="cross_city_scenario_deltas_v3")
    args = parser.parse_args()

    dataset_dir = DATASET_ROOT / args.dataset
    all_rows = [json.loads(line) for line in (dataset_dir / "rows.jsonl").open(encoding="utf-8")]
    cohort = CohortRule()
    rows = [row for row in all_rows if cohort.admits(row)]

    out_dir = EXPERIMENT_ROOT / args.experiment
    if out_dir.exists():
        print(f"{out_dir} exists — experiments are immutable", file=sys.stderr)
        return 2

    # A route-direction usually contributes three rows (weekday/Sat/Sun). They
    # cannot leak across folds, but they do mean the effective sample is well
    # below the row count, and any n quoted downstream should say so.
    distinct = {
        (row["city"], (row.get("provenance") or {}).get("original_route_id"), row["direction_id"])
        for row in rows
    }

    results = [evaluate(rows, freeze=True), evaluate(rows, freeze=False)]

    out_dir.mkdir(parents=True)
    report = {
        "experiment": args.experiment,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command": f"python scripts/evaluate_scenario_deltas.py --dataset {args.dataset} "
        f"--experiment {args.experiment}",
        "seed": SEED,
        "dataset": args.dataset,
        "dataset_manifest_checksum": json.loads((dataset_dir / "manifest.json").read_text())[
            "file_checksums"
        ]["rows.jsonl"],
        "question": "does the model predict the EFFECT of a design change within a city",
        "protocol": "leave-one-city-out; pairs drawn only within the held-out city",
        "cohort_rows": len(rows),
        "distinct_route_directions": len(distinct),
        "effective_sample_note": f"{len(rows)} rows describe {len(distinct)} distinct "
        "route-directions; day-type siblings share a city and never straddle a fold, "
        "so they cannot leak, but they do not add independent evidence",
        "production_operator": "frozen (production)",
        "results": results,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"cohort {len(rows)} rows / {len(distinct)} distinct route-directions\n")
    for result in results:
        pooled = result["pooled"]
        print(f"{result['operator']}  ({pooled['pairs']:,} within-city pairs)")
        print(f"  skill vs do-nothing      {pooled['skill_vs_do_nothing']:+.1%}")
        print(f"  sign agreement           {pooled['sign_agreement_on_material_changes']:.1%}")
        print(f"  predicted-delta MAE      {pooled['delta_model_mae_kmh']:.3f} km/h "
              f"(do-nothing {pooled['delta_zero_mae_kmh']:.3f})")
        print("  by design distance:")
        for bucket in result["by_design_distance"]:
            edge = bucket["max_feature_distance"]
            label = f"<= {edge:g}" if edge < 1e8 else "larger"
            print(f"    {label:>8s}  n={bucket['pairs']:>7,}  skill={bucket['skill_vs_do_nothing']:+6.1%}  "
                  f"sign={bucket['sign_agreement_on_material_changes']:.1%}")
        print("  per held-out city:")
        for city, values in sorted(result["per_city"].items()):
            print(f"    {city:14s} skill={values['skill_vs_do_nothing']:+6.1%}  "
                  f"sign={values['sign_agreement_on_material_changes']:.1%}")
        print()

    print(f"-> {out_dir.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
