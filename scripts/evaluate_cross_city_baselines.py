"""Leave-one-city-out evaluation of commercial-speed models.

Run:

    .venv\\Scripts\\python.exe scripts\\evaluate_cross_city_baselines.py \\
        --dataset cross_city_routes_v3 --experiment cross_city_baselines_v3

**Why not a random split.**  A random row split would put Vancouver routes in
both train and test.  Routes in one city share street design, signal timing,
stop-spacing policy and schedule padding convention, so a random split measures
how well the model interpolates between neighbouring routes in a city it has
already seen — which is not the question.  The question is whether relationships
learned from other cities transfer to a city the model has never seen, because
that is exactly what applying the model to an Edmonton scenario asks of it.
Every fold here holds out one entire city.

Day-type rows for the same route are near-duplicates of each other.  They never
straddle a fold (they belong to the same city), so they cannot leak, but they do
mean the effective sample is smaller than the row count; the report states both,
and a weekday-only run is included as a robustness check.

Candidates, weakest first, so any claim for a complex model is measured against
something transparent:

``global_median``      the train fold's median speed, ignoring every feature
``spacing_physical``   a two-parameter physical model of cruise speed and dwell
``linear``             ordinary least squares
``ridge``              L2-regularized linear
``decision_tree``      a single depth-limited tree
``random_forest``      bagged trees
``hist_gradient_boost``  sklearn's histogram gradient boosting

``spacing_physical`` matters more than its accuracy: it is the relationship a
transit planner would write down by hand, so if a learned model cannot beat it
the learned model has added nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeRegressor

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from transitpulse_ml.cross_city_features import (  # noqa: E402
    FORBIDDEN_FEATURES,
    TARGET,
    CohortRule,
    build_matrix,
)

SEED = 20260912
DATASET_ROOT = REPO_ROOT / "artifacts" / "datasets"
EXPERIMENT_ROOT = REPO_ROOT / "artifacts" / "experiments"


# --------------------------------------------------------------------------
# the transparent physical baseline
# --------------------------------------------------------------------------


class SpacingPhysicalModel:
    """Commercial speed from cruise speed and per-stop time loss.

    A vehicle covering ``s`` metres between stops at cruise speed ``v`` and
    losing ``t`` seconds per stop to deceleration, dwell and acceleration
    averages::

        v_commercial = s / (s / v + t)

    Two parameters, both physically meaningful, fitted by a coarse grid search
    on the training fold.  A grid is used rather than a gradient method because
    the surface is smooth, the grid is small, and an exhaustive search is
    exactly reproducible with no optimizer state to record.
    """

    def __init__(self) -> None:
        self.cruise_kmh = 30.0
        self.stop_penalty_s = 20.0

    def fit(self, spacing_m: np.ndarray, speed_kmh: np.ndarray) -> "SpacingPhysicalModel":
        usable = np.isfinite(spacing_m) & np.isfinite(speed_kmh) & (spacing_m > 0)
        spacing, observed = spacing_m[usable], speed_kmh[usable]
        best = (np.inf, self.cruise_kmh, self.stop_penalty_s)
        for cruise in np.arange(15.0, 90.1, 1.0):
            travel = spacing / (cruise / 3.6)
            for penalty in np.arange(0.0, 60.1, 1.0):
                predicted = spacing / (travel + penalty) * 3.6
                error = float(np.mean(np.abs(predicted - observed)))
                if error < best[0]:
                    best = (error, float(cruise), float(penalty))
        _, self.cruise_kmh, self.stop_penalty_s = best
        return self

    def predict(self, spacing_m: np.ndarray) -> np.ndarray:
        spacing = np.where(np.isfinite(spacing_m) & (spacing_m > 0), spacing_m, 300.0)
        travel = spacing / (self.cruise_kmh / 3.6)
        return spacing / (travel + self.stop_penalty_s) * 3.6

    def describe(self) -> dict[str, float]:
        return {"cruise_kmh": self.cruise_kmh, "stop_penalty_seconds": self.stop_penalty_s}


# --------------------------------------------------------------------------
# candidates
# --------------------------------------------------------------------------


def make_candidates() -> dict[str, Callable[[], Any]]:
    """Every candidate as a fresh factory, so no fold sees another fold's fit."""

    def linear() -> Pipeline:
        return Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("model", LinearRegression()),
            ]
        )

    def ridge() -> Pipeline:
        return Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("model", Ridge(alpha=10.0, random_state=SEED)),
            ]
        )

    def tree() -> Pipeline:
        return Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("model", DecisionTreeRegressor(max_depth=6, min_samples_leaf=25, random_state=SEED)),
            ]
        )

    def forest() -> Pipeline:
        return Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                (
                    "model",
                    RandomForestRegressor(
                        n_estimators=400,
                        min_samples_leaf=5,
                        n_jobs=-1,
                        random_state=SEED,
                    ),
                ),
            ]
        )

    def boosting() -> HistGradientBoostingRegressor:
        # Handles NaN natively, so no imputation step is needed or wanted.
        return HistGradientBoostingRegressor(
            max_iter=400,
            learning_rate=0.06,
            max_leaf_nodes=31,
            min_samples_leaf=20,
            l2_regularization=1.0,
            early_stopping=True,
            validation_fraction=0.15,
            random_state=SEED,
        )

    return {
        "global_median": lambda: DummyRegressor(strategy="median"),
        "linear": linear,
        "ridge": ridge,
        "decision_tree": tree,
        "random_forest": forest,
        "hist_gradient_boost": boosting,
    }


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


def metrics(actual: np.ndarray, predicted: np.ndarray, length_km: np.ndarray) -> dict[str, float]:
    """Speed error in km/h, plus the runtime error a planner actually feels.

    A 2 km/h speed error means something very different on a 4 km route than on
    a 30 km one, so the runtime translation is reported alongside it.
    """

    error = predicted - actual
    absolute = np.abs(error)
    safe_predicted = np.clip(predicted, 1e-6, None)
    runtime_error = np.abs(60.0 * length_km / safe_predicted - 60.0 * length_km / actual)
    return {
        "n": int(actual.size),
        "mae_kmh": float(np.mean(absolute)),
        "rmse_kmh": float(np.sqrt(np.mean(error**2))),
        "median_ae_kmh": float(np.median(absolute)),
        "p90_ae_kmh": float(np.quantile(absolute, 0.90)),
        "bias_kmh": float(np.mean(error)),
        "mape_percent": float(np.mean(absolute / np.clip(actual, 1e-6, None)) * 100.0),
        "runtime_mae_minutes": float(np.mean(runtime_error)),
        "runtime_median_ae_minutes": float(np.median(runtime_error)),
        "runtime_p90_ae_minutes": float(np.quantile(runtime_error, 0.90)),
    }


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------


def leave_one_city_out(
    rows: Sequence[Mapping[str, Any]],
    *,
    include_city: bool = False,
) -> dict[str, Any]:
    cities = sorted({row["city"] for row in rows})
    candidates = make_candidates()

    per_city: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    pooled_predictions: dict[str, list[np.ndarray]] = defaultdict(list)
    pooled_actuals: list[np.ndarray] = []
    pooled_lengths: list[np.ndarray] = []
    pooled_cities: list[str] = []
    physical_parameters: dict[str, dict[str, float]] = {}

    for held_out in cities:
        train_rows = [row for row in rows if row["city"] != held_out]
        test_rows = [row for row in rows if row["city"] == held_out]

        x_train, y_train, _ = build_matrix(train_rows, include_city=include_city, cities=cities)
        x_test, y_test, _ = build_matrix(test_rows, include_city=include_city, cities=cities)
        length_test = np.array([row["one_way_length_km"] for row in test_rows], dtype=np.float64)

        pooled_actuals.append(y_test)
        pooled_lengths.append(length_test)
        pooled_cities.extend([held_out] * len(test_rows))

        for name, factory in candidates.items():
            model = factory()
            model.fit(x_train, y_train)
            predicted = np.asarray(model.predict(x_test), dtype=np.float64)
            per_city[name][held_out] = metrics(y_test, predicted, length_test)
            pooled_predictions[name].append(predicted)

        spacing_train = np.array(
            [row.get("mean_stop_spacing_m") or np.nan for row in train_rows], dtype=np.float64
        )
        spacing_test = np.array(
            [row.get("mean_stop_spacing_m") or np.nan for row in test_rows], dtype=np.float64
        )
        physical = SpacingPhysicalModel().fit(spacing_train, y_train)
        predicted = physical.predict(spacing_test)
        per_city["spacing_physical"][held_out] = metrics(y_test, predicted, length_test)
        pooled_predictions["spacing_physical"].append(predicted)
        physical_parameters[held_out] = physical.describe()

    actual = np.concatenate(pooled_actuals)
    lengths = np.concatenate(pooled_lengths)
    overall = {
        name: metrics(actual, np.concatenate(chunks), lengths)
        for name, chunks in pooled_predictions.items()
    }

    # Held-out residual quantiles are the basis of the scenario interval: they
    # are measured on cities the model never saw, which is the only unbiased
    # source for "how wrong is this likely to be on a new city".
    residual_quantiles = {}
    for name, chunks in pooled_predictions.items():
        residual = np.concatenate(chunks) - actual
        residual_quantiles[name] = {
            "p05": float(np.quantile(residual, 0.05)),
            "p10": float(np.quantile(residual, 0.10)),
            "p25": float(np.quantile(residual, 0.25)),
            "p50": float(np.quantile(residual, 0.50)),
            "p75": float(np.quantile(residual, 0.75)),
            "p90": float(np.quantile(residual, 0.90)),
            "p95": float(np.quantile(residual, 0.95)),
            "abs_p80": float(np.quantile(np.abs(residual), 0.80)),
            "abs_p90": float(np.quantile(np.abs(residual), 0.90)),
        }

    return {
        "cities": cities,
        "overall": overall,
        "per_city": {name: dict(folds) for name, folds in per_city.items()},
        "residual_quantiles": residual_quantiles,
        "physical_parameters_by_fold": physical_parameters,
        "pooled_out_of_fold": {
            "city": pooled_cities,
            "actual_kmh": actual.tolist(),
            "length_km": lengths.tolist(),
            "predicted_kmh": {
                name: np.concatenate(chunks).tolist() for name, chunks in pooled_predictions.items()
            },
        },
    }


def permutation_importance_loco(
    rows: Sequence[Mapping[str, Any]], names: Sequence[str], *, repeats: int = 5
) -> dict[str, float]:
    """How much held-out MAE worsens when one feature is shuffled.

    Computed across the same leave-one-city-out folds as the headline metrics,
    so it describes what the model relies on when generalizing to a new city —
    not what it relies on when fitting the cities it already has.
    """

    rng = np.random.default_rng(SEED)
    cities = sorted({row["city"] for row in rows})
    baseline_total, increases = 0.0, np.zeros(len(names))
    count = 0

    for held_out in cities:
        train_rows = [row for row in rows if row["city"] != held_out]
        test_rows = [row for row in rows if row["city"] == held_out]
        x_train, y_train, _ = build_matrix(train_rows)
        x_test, y_test, _ = build_matrix(test_rows)
        model = make_candidates()["hist_gradient_boost"]()
        model.fit(x_train, y_train)
        base = float(np.mean(np.abs(model.predict(x_test) - y_test)))
        baseline_total += base * len(test_rows)
        count += len(test_rows)
        for column in range(x_test.shape[1]):
            deltas = []
            for _ in range(repeats):
                shuffled = x_test.copy()
                shuffled[:, column] = rng.permutation(shuffled[:, column])
                deltas.append(float(np.mean(np.abs(model.predict(shuffled) - y_test))) - base)
            increases[column] += float(np.mean(deltas)) * len(test_rows)

    return {
        name: round(float(increases[index] / count), 4) for index, name in enumerate(names)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="cross_city_routes_v3")
    parser.add_argument("--experiment", default="cross_city_baselines_v3")
    args = parser.parse_args()

    dataset_dir = DATASET_ROOT / args.dataset
    rows_path = dataset_dir / "rows.jsonl"
    if not rows_path.exists():
        print(f"missing {rows_path}", file=sys.stderr)
        return 2

    out_dir = EXPERIMENT_ROOT / args.experiment
    if out_dir.exists():
        print(f"{out_dir} exists — experiments are immutable, use a new name", file=sys.stderr)
        return 2

    all_rows = [json.loads(line) for line in rows_path.open(encoding="utf-8")]
    cohort = CohortRule()
    rows = [row for row in all_rows if cohort.admits(row)]

    leaked = FORBIDDEN_FEATURES & set(
        __import__("transitpulse_ml.cross_city_features", fromlist=["x"]).feature_names()
    )
    if leaked:
        print(f"REFUSING TO RUN: target-derived features present: {leaked}", file=sys.stderr)
        return 1

    print(f"cohort {len(rows)} of {len(all_rows)} rows, {len({r['city'] for r in rows})} cities")

    print("leave-one-city-out, all day types ...")
    primary = leave_one_city_out(rows)

    print("leave-one-city-out, weekday only (duplicate-robustness check) ...")
    weekday = leave_one_city_out([row for row in rows if row["day_type"] == "weekday"])

    print("leave-one-city-out, with city identity one-hot (generalization check) ...")
    with_city = leave_one_city_out(rows, include_city=True)

    from transitpulse_ml.cross_city_features import feature_names  # noqa: PLC0415

    print("permutation importance ...")
    importance = permutation_importance_loco(rows, feature_names())

    out_dir.mkdir(parents=True)
    ranked = sorted(primary["overall"].items(), key=lambda item: item[1]["mae_kmh"])
    winner = ranked[0][0]

    report = {
        "experiment": args.experiment,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command": f"python scripts/evaluate_cross_city_baselines.py --dataset {args.dataset} "
        f"--experiment {args.experiment}",
        "seed": SEED,
        "dataset": args.dataset,
        "dataset_manifest_checksum": json.loads((dataset_dir / "manifest.json").read_text())[
            "file_checksums"
        ]["rows.jsonl"],
        "target": TARGET,
        "protocol": "leave-one-city-out; every fold holds out one entire city",
        "cohort_rule": cohort.describe(),
        "cohort_rows": len(rows),
        "dataset_rows": len(all_rows),
        "features": feature_names(),
        "forbidden_features": sorted(FORBIDDEN_FEATURES),
        "ranking_by_mae_kmh": [{"model": name, **values} for name, values in ranked],
        "winner": winner,
        "primary": {k: v for k, v in primary.items() if k != "pooled_out_of_fold"},
        "weekday_only": {k: v for k, v in weekday.items() if k != "pooled_out_of_fold"},
        "with_city_identity": {k: v for k, v in with_city.items() if k != "pooled_out_of_fold"},
        "permutation_importance_mae_increase_kmh": importance,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "out_of_fold_predictions.json").write_text(
        json.dumps(primary["pooled_out_of_fold"]), encoding="utf-8"
    )

    print("\nLEAVE-ONE-CITY-OUT, pooled over all held-out cities")
    print(f"  {'model':22s} {'MAE km/h':>9s} {'RMSE':>7s} {'medAE':>7s} {'P90AE':>7s} {'runtime MAE min':>16s}")
    for name, values in ranked:
        print(
            f"  {name:22s} {values['mae_kmh']:9.3f} {values['rmse_kmh']:7.3f} "
            f"{values['median_ae_kmh']:7.3f} {values['p90_ae_kmh']:7.3f} "
            f"{values['runtime_mae_minutes']:16.2f}"
        )

    print(f"\nPER-CITY MAE (km/h) for {winner}")
    for city, values in sorted(primary["per_city"][winner].items()):
        print(f"  {city:14s} n={values['n']:5d}  MAE={values['mae_kmh']:6.3f}  "
              f"bias={values['bias_kmh']:+6.3f}  runtime MAE={values['runtime_mae_minutes']:5.2f} min")

    print("\nTOP FEATURES (held-out MAE increase when shuffled, km/h)")
    for name, value in sorted(importance.items(), key=lambda item: -item[1])[:8]:
        print(f"  {name:28s} {value:+7.3f}")

    print(f"\nwinner: {winner}  ->  {out_dir.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
