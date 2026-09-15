"""Application service behind the SCENARIOS product surface.

The frontend never sees a scikit-learn object or a raw dataset row.  It asks
this module for a list of routes, for one route's current plan, and for an
estimate of a proposed plan, and gets plain data back with units in the names.

Baselines come from the normalized cross-city dataset rather than from the live
database, deliberately: the model was fitted on rows produced by that exact
extractor, so measuring a baseline any other way would compare a scenario
against a number the model has never been calibrated against.  The dataset row
and the model artifact are pinned together by the dataset checksum recorded in
the model's metadata, and a mismatch is surfaced rather than ignored.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from transitpulse_ml.scenario_estimator import RoutePlan, ScenarioEstimator

#: The city the product offers scenarios for.  The model is trained on eight
#: cities; only Edmonton is exposed because it is the one this deployment holds
#: live operational data for, so a planner can cross-check a baseline.
SCENARIO_CITY = "Edmonton"

DEFAULT_DATASET = "cross_city_routes_v3"
DEFAULT_MODEL = "scenario_estimator_v3"


def artifact_root() -> Path:
    override = os.getenv("TRANSITPULSE_ARTIFACT_ROOT")
    return Path(override).resolve() if override else Path(__file__).resolve().parents[3] / "artifacts"


class ScenarioUnavailable(RuntimeError):
    """The estimator or its dataset is not present on this machine."""


@dataclass(frozen=True)
class BaselineRoute:
    """One Edmonton route direction on one day type, as scheduled today."""

    key: str
    route_label: str
    direction_id: int | None
    day_type: str
    row: dict[str, Any]

    def to_plan(self) -> RoutePlan:
        row = self.row
        mean_spacing = row.get("mean_stop_spacing_m") or 0.0
        median_spacing = row.get("median_stop_spacing_m") or mean_spacing
        return RoutePlan(
            one_way_length_km=row["one_way_length_km"],
            stop_count=row["stop_count"],
            day_type=row["day_type"],
            peak_headway_minutes=row.get("peak_headway_minutes"),
            offpeak_headway_minutes=row.get("offpeak_headway_minutes"),
            median_headway_minutes=row.get("median_headway_minutes"),
            service_span_hours=row.get("service_span_hours"),
            loop_route=bool(row.get("loop_route")),
            directness_ratio=row.get("directness_ratio"),
            branch_count=row.get("branch_count") or 1,
            spacing_skew=(median_spacing / mean_spacing) if mean_spacing else 1.0,
        )

    def summary(self) -> dict[str, Any]:
        row = self.row
        return {
            "key": self.key,
            "route_label": self.route_label,
            "direction_id": self.direction_id,
            "day_type": self.day_type,
            "one_way_length_km": row["one_way_length_km"],
            "stop_count": row["stop_count"],
            "stops_per_km": row["stops_per_km"],
            "mean_stop_spacing_m": row.get("mean_stop_spacing_m"),
            "scheduled_runtime_minutes": row.get("scheduled_runtime_minutes"),
            "scheduled_commercial_speed_kmh": row.get("scheduled_commercial_speed_kmh"),
            "peak_headway_minutes": row.get("peak_headway_minutes"),
            "offpeak_headway_minutes": row.get("offpeak_headway_minutes"),
            "median_headway_minutes": row.get("median_headway_minutes"),
            "service_span_hours": row.get("service_span_hours"),
            "trips_per_day": row.get("trips_per_day"),
            "estimated_cycle_time_minutes": row.get("estimated_cycle_time_minutes"),
            "estimated_required_vehicles": row.get("estimated_required_vehicles"),
            "loop_route": row.get("loop_route"),
            "branch_count": row.get("branch_count"),
            "dominant_pattern_trip_share": row.get("dominant_pattern_trip_share"),
            "directness_ratio": row.get("directness_ratio"),
            "original_route_id": row.get("original_route_id"),
            "agency": row.get("agency"),
        }


@dataclass(frozen=True)
class ScenarioService:
    estimator: ScenarioEstimator
    baselines: dict[str, BaselineRoute]
    #: (route_id, day_type, direction_id) -> scheduled runtime, over EVERY row of
    #: the city, not only the cohort-admitted ones.  A route's opposite direction
    #: is frequently excluded from the modelling cohort while the direction being
    #: served is admitted; looking the return leg up in the cohort-filtered map
    #: made it invisible and silently fell back to assuming symmetry.
    runtime_index: dict[tuple[str, str, int], float]
    dataset: str
    dataset_checksum_matches: bool

    def route_list(self) -> list[dict[str, Any]]:
        return [
            {
                "key": baseline.key,
                "route_label": baseline.route_label,
                "direction_id": baseline.direction_id,
                "day_type": baseline.day_type,
                "one_way_length_km": baseline.row["one_way_length_km"],
                "stop_count": baseline.row["stop_count"],
                "scheduled_commercial_speed_kmh": baseline.row.get("scheduled_commercial_speed_kmh"),
                "scheduled_runtime_minutes": baseline.row.get("scheduled_runtime_minutes"),
                "agency": baseline.row.get("agency"),
            }
            for baseline in sorted(
                self.baselines.values(),
                key=lambda b: (b.route_label.rjust(6), b.direction_id or 0, b.day_type),
            )
        ]

    def baseline(self, key: str) -> BaselineRoute:
        if key not in self.baselines:
            raise KeyError(key)
        return self.baselines[key]

    def opposite_runtime_minutes(self, baseline: BaselineRoute) -> float | None:
        """The return leg's scheduled runtime, when the feed publishes one.

        Cycle time needs both halves.  Without the opposite direction the
        estimator falls back to assuming symmetry and says so, so it matters
        that this is looked up rather than guessed.
        """

        if baseline.direction_id is None:
            return None
        route_id = baseline.row.get("original_route_id")
        if route_id is None:
            return None
        return self.runtime_index.get(
            (route_id, baseline.day_type, 1 - baseline.direction_id)
        )

    def estimate(self, key: str, changes: dict[str, Any]) -> dict[str, Any]:
        baseline = self.baseline(key)
        current = baseline.to_plan()
        measured = baseline.row.get("scheduled_commercial_speed_kmh")
        if not measured:
            raise ScenarioUnavailable(
                "this route direction has no scheduled commercial speed to anchor against"
            )

        scenario = RoutePlan(
            one_way_length_km=float(changes.get("one_way_length_km", current.one_way_length_km)),
            stop_count=int(changes.get("stop_count", current.stop_count)),
            day_type=str(changes.get("day_type", current.day_type)),
            peak_headway_minutes=_or(changes, "peak_headway_minutes", current.peak_headway_minutes),
            offpeak_headway_minutes=_or(changes, "offpeak_headway_minutes", current.offpeak_headway_minutes),
            median_headway_minutes=_or(changes, "median_headway_minutes", current.median_headway_minutes),
            service_span_hours=_or(changes, "service_span_hours", current.service_span_hours),
            loop_route=current.loop_route,
            directness_ratio=current.directness_ratio,
            branch_count=current.branch_count,
            spacing_skew=current.spacing_skew,
            recovery_fraction=float(changes.get("recovery_fraction", current.recovery_fraction)),
        )

        estimate = self.estimator.estimate(
            current=current,
            scenario=scenario,
            measured_speed_kmh=measured,
            opposite_runtime_minutes=self.opposite_runtime_minutes(baseline),
            # The measured runtime, so the return leg scales by the same
            # proportion this direction actually moved.
            current_runtime_minutes=baseline.row.get("scheduled_runtime_minutes"),
        )

        current_vehicles = baseline.row.get("estimated_required_vehicles")
        scenario_vehicles = estimate.fleet.vehicles.point if estimate.fleet else None
        return {
            "baseline": baseline.summary(),
            "scenario_plan": {
                "one_way_length_km": round(scenario.one_way_length_km, 3),
                "stop_count": scenario.stop_count,
                "stops_per_km": round(scenario.stops_per_km, 3),
                "mean_stop_spacing_m": round(scenario.mean_stop_spacing_m, 1),
                "day_type": scenario.day_type,
                "peak_headway_minutes": scenario.peak_headway_minutes,
                "offpeak_headway_minutes": scenario.offpeak_headway_minutes,
                "median_headway_minutes": scenario.median_headway_minutes,
                "service_span_hours": scenario.service_span_hours,
                "recovery_fraction": scenario.recovery_fraction,
            },
            "estimate": estimate.to_dict(),
            "delta": {
                # From the model's own unrounded difference, not from the
                # rounded display value: subtracting a value rounded to 1 dp
                # from the raw measurement reported a 0.03 km/h "change" for a
                # scenario whose speed is by construction unchanged.
                "speed_kmh": round(estimate.predicted_delta_kmh, 2),
                "runtime_minutes": (
                    round(
                        estimate.runtime_minutes.point
                        - (baseline.row.get("scheduled_runtime_minutes") or 0),
                        1,
                    )
                    if baseline.row.get("scheduled_runtime_minutes")
                    else None
                ),
                "vehicles": (
                    round(scenario_vehicles - current_vehicles, 2)
                    if scenario_vehicles is not None and current_vehicles is not None
                    else None
                ),
            },
            "model": self.model_card(),
        }

    def model_card(self) -> dict[str, Any]:
        """What the UI needs to describe the model accurately, straight from the artifact."""

        meta = self.estimator.metadata
        return {
            "version": meta.get("version"),
            "dataset": meta.get("dataset"),
            "dataset_checksum_matches": self.dataset_checksum_matches,
            "schema_version": meta.get("schema_version"),
            "target": meta.get("target"),
            "model_family": meta.get("model_family"),
            "model_selection": meta.get("model_selection"),
            "training_rows": meta.get("training_rows"),
            "training_cities": meta.get("training_cities"),
            "quality_measured_on": meta.get("quality_measured_on"),
            "interval_basis": meta.get("interval_basis"),
            "held_out_absolute_accuracy": meta.get("held_out_absolute_accuracy"),
            "held_out_change_skill": meta.get("held_out_change_skill"),
            "distinct_route_directions": meta.get("distinct_route_directions"),
            "effective_sample_note": meta.get("effective_sample_note"),
            "limitations": meta.get("limitations"),
        }


def _or(changes: dict[str, Any], name: str, fallback: Any) -> Any:
    value = changes.get(name, fallback)
    return None if value is None else float(value)


@lru_cache(maxsize=1)
def load_scenario_service(
    dataset: str = DEFAULT_DATASET, model: str = DEFAULT_MODEL
) -> ScenarioService:
    """Load the estimator and Edmonton baselines once per process.

    Raises :class:`ScenarioUnavailable` rather than returning a half-built
    service, so the API can report the surface as unavailable instead of
    serving estimates against a missing or mismatched artifact.
    """

    root = artifact_root()
    model_dir = root / "models" / model
    rows_path = root / "datasets" / dataset / "rows.jsonl"
    manifest_path = root / "datasets" / dataset / "manifest.json"

    if not model_dir.is_dir() or not rows_path.is_file():
        raise ScenarioUnavailable(
            f"scenario estimator artifacts not found: expected {model_dir} and {rows_path}"
        )

    estimator = ScenarioEstimator.load(model_dir)

    checksum_matches = False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        checksum_matches = (
            manifest["file_checksums"]["rows.jsonl"]
            == estimator.metadata.get("dataset_rows_checksum")
        )
    except (OSError, json.JSONDecodeError, KeyError):
        checksum_matches = False

    from transitpulse_ml.cross_city_features import CohortRule  # noqa: PLC0415

    cohort = CohortRule()
    baselines: dict[str, BaselineRoute] = {}
    runtime_index: dict[tuple[str, str, int], float] = {}
    with rows_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("city") != SCENARIO_CITY:
                continue
            provenance = row.get("provenance") or {}
            row = {
                **row,
                "original_route_id": provenance.get("original_route_id"),
            }
            # Indexed before the cohort filter, so an excluded opposite direction
            # can still supply a real return leg.
            route_id = row.get("original_route_id")
            runtime = row.get("scheduled_runtime_minutes")
            if route_id and runtime and row.get("direction_id") is not None:
                runtime_index[(route_id, row["day_type"], int(row["direction_id"]))] = runtime
            # Only routes the model is calibrated for are offered; a route the
            # cohort excludes would get an estimate the evaluation never covered.
            if not cohort.admits(row):
                continue
            key = f"{provenance.get('original_route_id')}|{row.get('direction_id')}|{row['day_type']}"
            baselines[key] = BaselineRoute(
                key=key,
                route_label=row["route_label"],
                direction_id=row.get("direction_id"),
                day_type=row["day_type"],
                row=row,
            )

    if not baselines:
        raise ScenarioUnavailable(f"no {SCENARIO_CITY} routes in dataset {dataset}")

    return ScenarioService(
        estimator=estimator,
        baselines=baselines,
        runtime_index=runtime_index,
        dataset=dataset,
        dataset_checksum_matches=checksum_matches,
    )
