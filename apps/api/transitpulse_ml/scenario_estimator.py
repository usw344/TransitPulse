"""The planning estimator behind the SCENARIOS product surface.

What this is
------------
An approximation trained on scheduled service design across eight real transit
systems.  Given a route as it is scheduled today and a proposed change to its
design, it estimates the proposed commercial speed, runtime, cycle time and
concurrent vehicle requirement, with an interval and an explicit confidence
level.  It is **not** a substitute for a professional running-time analysis: it
has never seen a street, a signal, a turn movement or a passenger.

Why estimates are anchored to the measured route
------------------------------------------------
Held out, the model's absolute speed predictions carry a large per-city offset —
about +3.7 km/h on Montreal and -3.9 on Minneapolis.  Cities differ in ways the
features cannot express: signal priority, grid geometry, schedule padding
convention.  Predicting a scenario's speed directly would hand the planner that
whole offset, and worse, would return a *different* speed than the schedule says
for a scenario that changed nothing at all.

So the estimator predicts a **difference** and applies it to the route's own
measured speed::

    estimated_speed = measured_speed + (predict(scenario) - predict(current))

A scenario with no changes therefore returns today's schedule exactly, the city
and route offsets cancel, and the quantity being modelled is the one the model
was actually shown to have skill at: within-city change.

How much skill: measured **with this exact frozen operator** on cities
the model never saw, the predicted difference beats "assume the change does
nothing" by about 18% pooled and gets the direction of a material change right
about 71% of the time.  Those figures are dominated by pairs of wholly different
routes.  **For small edits — the regime this product actually serves — the
advantage is close to nothing**, and the skill broken out by design distance in
``artifacts/experiments/cross_city_scenario_deltas_v3`` is what the model card
must show, not the pooled number.

Where the interval comes from
-----------------------------
Empirical absolute errors of predicted differences, measured on cities the model
never saw, bucketed by how far apart the two designs sit in standardized feature
space.  A small stop-spacing tweak sits close to its baseline and gets a narrow
band; a wholesale redesign sits far away and gets a wide one.  No distributional
assumption is made and nothing is fitted to the interval itself.

The band is conservative on purpose.  It is measured between *different* routes,
so it carries two independent route-identity offsets — corridor, traffic,
terminal layout — that an anchored estimate cancels.  A planning tool should err
wide.

Why frequency does not move the estimated speed
-----------------------------------------------
Fitted across the dataset, higher frequency predicts *lower* commercial speed:
the coefficient on trips per day is strongly negative.  That correlation is real
and it is useful for describing a route — frequent routes are the ones running
in dense, congested corridors — but it is not a causal mechanism.  Adding buses
to a corridor does not make the corridor slower, and telling a planner who
improved a headway from 15 to 12 minutes that the route would now run slower
would be an unreliable result with no causal basis.

So the service-intensity inputs listed in :data:`FROZEN_IN_DELTA` are held at
the route's current values on *both* sides of the difference.  Their
contributions cancel exactly, the estimated speed responds only to geometry —
length, stop count, spacing, directness — and frequency instead does what it
genuinely does: change the vehicle requirement, through cycle time over headway,
which is arithmetic rather than inference.

The features are still supplied to the model, because they improve its
description of the route as it stands today (held-out MAE 3.36 km/h with them,
3.65 without).  They are simply not permitted to answer a counterfactual they
cannot support.

What is *not* modelled
----------------------
Ridership, wait time, crowding, passenger benefit, and any effect of a change on
demand.  No adopted agency publishes comparable route-level ridership, so this
estimator is silent about passengers by construction.
"""

from __future__ import annotations

import json
import math
import pickle
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import numpy as np

from transitpulse_ml.cross_city_features import (
    DAY_TYPES,
    TARGET,
    build_matrix,
    runtime_minutes_from_speed,
)

Confidence = Literal["high", "moderate", "low"]

#: Same planning assumptions the dataset was built with, restated here so a
#: scenario's cycle time is computed the same way the training rows were.
RECOVERY_FRACTION_OF_ROUND_TRIP = 0.10
MIN_RECOVERY_MINUTES = 5.0

#: Inputs whose fitted relationship with speed is confounded rather than causal.
#: Held at the current route's values on both sides of the difference, so they
#: cancel and cannot move the estimated speed.  See the module docstring.
FROZEN_IN_DELTA: frozenset[str] = frozenset(
    {
        "peak_headway_minutes",
        "offpeak_headway_minutes",
        "median_headway_minutes",
        "trips_per_day",
        "service_span_hours",
    }
)


@dataclass(frozen=True)
class RoutePlan:
    """A route's design, either as scheduled today or as proposed.

    Only the fields a planner can actually choose.  Everything the model needs
    that follows arithmetically from these — stop density, stop spacing, trips
    per day — is derived in :meth:`to_features` so a scenario can never carry an
    internally inconsistent combination.
    """

    one_way_length_km: float
    stop_count: int
    day_type: str = "weekday"
    peak_headway_minutes: float | None = None
    offpeak_headway_minutes: float | None = None
    median_headway_minutes: float | None = None
    service_span_hours: float | None = None
    loop_route: bool = False
    directness_ratio: float | None = None
    branch_count: int = 1
    #: Ratio of median to mean stop spacing on the route as scheduled today.
    #: Carried through a scenario so an irregular route stays irregular rather
    #: than silently becoming evenly spaced.
    spacing_skew: float = 1.0
    #: Recovery as a share of round-trip running time; exposed so a planner can
    #: test a different layover policy.
    recovery_fraction: float = RECOVERY_FRACTION_OF_ROUND_TRIP

    def __post_init__(self) -> None:
        # NaN and infinity pass every comparison below without tripping one, and
        # would propagate silently into a served estimate.
        for name in (
            "one_way_length_km",
            "peak_headway_minutes",
            "offpeak_headway_minutes",
            "median_headway_minutes",
            "service_span_hours",
            "directness_ratio",
            "spacing_skew",
            "recovery_fraction",
        ):
            value = getattr(self, name)
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{name} must be a finite number")
        if self.one_way_length_km <= 0:
            raise ValueError("one_way_length_km must be positive")
        if self.stop_count < 2:
            raise ValueError("a route needs at least 2 stops")
        if self.day_type not in DAY_TYPES:
            raise ValueError(f"day_type must be one of {DAY_TYPES}")
        for name in ("peak_headway_minutes", "offpeak_headway_minutes", "median_headway_minutes"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when given")
        # A GTFS service day legitimately exceeds 24 h when a route runs past
        # midnight; the dataset's longest observed span is 24.75 h.
        if self.service_span_hours is not None and not (0 < self.service_span_hours <= 25.0):
            raise ValueError("service_span_hours must lie in (0, 25]")
        if self.recovery_fraction < 0 or self.recovery_fraction > 1:
            raise ValueError("recovery_fraction must lie in [0, 1]")

    @property
    def stops_per_km(self) -> float:
        return self.stop_count / self.one_way_length_km

    @property
    def mean_stop_spacing_m(self) -> float:
        # Spacing is measured between stops, so n stops give n-1 gaps.
        return 1000.0 * self.one_way_length_km / max(1, self.stop_count - 1)

    @property
    def sizing_headway_minutes(self) -> float | None:
        return self.peak_headway_minutes or self.median_headway_minutes or self.offpeak_headway_minutes

    def to_features(self) -> dict[str, Any]:
        headway = self.median_headway_minutes or self.peak_headway_minutes
        trips = None
        if headway and self.service_span_hours:
            # Trips follow from span and headway; letting a planner set them
            # independently would allow a 10-minute headway over a 2-hour span
            # with 300 trips, which describes nothing.
            trips = max(1.0, self.service_span_hours * 60.0 / headway)
        return {
            "one_way_length_km": self.one_way_length_km,
            "stop_count": self.stop_count,
            "stops_per_km": self.stops_per_km,
            "mean_stop_spacing_m": self.mean_stop_spacing_m,
            "median_stop_spacing_m": self.mean_stop_spacing_m * self.spacing_skew,
            "directness_ratio": self.directness_ratio,
            "branch_count": self.branch_count,
            "peak_headway_minutes": self.peak_headway_minutes,
            "offpeak_headway_minutes": self.offpeak_headway_minutes,
            "median_headway_minutes": self.median_headway_minutes,
            "trips_per_day": trips,
            "service_span_hours": self.service_span_hours,
            "loop_route": self.loop_route,
            "day_type": self.day_type,
            TARGET: 0.0,  # placeholder; build_matrix reads but never uses it here
        }


@dataclass(frozen=True)
class Interval:
    """A point estimate with an empirical uncertainty range around it."""

    point: float
    low: float
    high: float

    def rounded(self, digits: int = 1) -> "Interval":
        return Interval(round(self.point, digits), round(self.low, digits), round(self.high, digits))


@dataclass(frozen=True)
class FleetEstimate:
    """Concurrent vehicles required, and the arithmetic behind it.

    This is deliberately computed rather than learned.  ``cycle / headway`` is
    the standard planning identity and is far more reliable than anything a
    model fitted to 4,900 rows could infer; the only uncertain input is the
    runtime, which is where the interval comes from.
    """

    vehicles: Interval
    cycle_time_minutes: Interval
    recovery_minutes: float
    headway_minutes: float
    basis: str


@dataclass(frozen=True)
class ScenarioEstimate:
    commercial_speed_kmh: Interval
    runtime_minutes: Interval
    fleet: FleetEstimate | None
    confidence: Confidence
    #: Human-readable reasons the confidence is what it is.
    confidence_reasons: tuple[str, ...]
    #: Features outside the range the model was trained on, with the observed range.
    out_of_distribution: tuple[dict[str, Any], ...]
    #: What changed and which direction each change pushes the estimate.
    drivers: tuple[dict[str, Any], ...]
    baseline_speed_kmh: float
    predicted_delta_kmh: float
    #: True only when the proposed plan is identical to today's in every field.
    #: Distinct from ``design_distance == 0``, which is also true when only the
    #: deliberately-frozen service inputs moved.
    scenario_unchanged: bool
    #: Standardized distance between the current and proposed designs, over the
    #: inputs allowed to affect speed. Zero for a frequency-only change.
    design_distance: float
    #: The wider 90th-percentile band, for callers that want the tail.
    band_p90_kmh: float
    method: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["out_of_distribution"] = list(self.out_of_distribution)
        payload["drivers"] = list(self.drivers)
        payload["confidence_reasons"] = list(self.confidence_reasons)
        return payload


@dataclass
class EstimatorArtifact:
    """Everything the estimator needs, loaded from a persisted directory."""

    model: Any
    feature_names: list[str]
    #: Design-distance bucket upper bounds -> absolute-error quantiles.
    delta_error_buckets: list[dict[str, Any]]
    #: Same buckets -> measured skill over "assume the change does nothing".
    #: Confidence must reflect this: a band can be narrow while the model has no
    #: demonstrated ability to say which way the number moves.
    skill_buckets: list[dict[str, Any]]
    #: Median / mean / std used to standardize features for the distance metric.
    #: Identical to the values the bands were calibrated with, so a distance
    #: computed at serve time indexes the bucket it was measured in.
    distance_scaling: dict[str, list[float]]
    #: Robust per-feature envelope observed in training, for the OOD guard.
    feature_envelope: dict[str, dict[str, float]]
    metadata: dict[str, Any]


class ScenarioEstimator:
    """Serve scenario estimates from a persisted artifact.

    The frontend never touches a scikit-learn object: it calls
    :meth:`estimate` and receives plain data with units in the names.
    """

    def __init__(self, artifact: EstimatorArtifact) -> None:
        self._artifact = artifact

    @classmethod
    def load(cls, directory: Path | str) -> "ScenarioEstimator":
        directory = Path(directory)
        with (directory / "model.pkl").open("rb") as handle:
            model = pickle.load(handle)  # noqa: S301 - our own artifact, written by our own script
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        return cls(
            EstimatorArtifact(
                model=model,
                feature_names=metadata["feature_names"],
                delta_error_buckets=metadata["delta_error_buckets"],
                skill_buckets=(metadata.get("held_out_change_skill") or {}).get(
                    "by_design_distance", []
                ),
                distance_scaling=metadata["distance_scaling"],
                feature_envelope=metadata["feature_envelope"],
                metadata=metadata,
            )
        )

    @property
    def metadata(self) -> dict[str, Any]:
        return self._artifact.metadata

    # -- internals ------------------------------------------------------

    @staticmethod
    def _aligned_features(plan: RoutePlan, *, service_from: RoutePlan) -> dict[str, Any]:
        """Plan features with the confounded service inputs taken from ``service_from``.

        Applied to both sides of the difference with the *current* route as
        ``service_from``, so those inputs are identical in the two vectors and
        contribute nothing to the predicted change.
        """

        features = plan.to_features()
        donor = service_from.to_features()
        for name in FROZEN_IN_DELTA:
            features[name] = donor[name]
        return features

    def _predict_features(self, features: dict[str, Any]) -> float:
        matrix, _, _ = build_matrix([features])
        return float(self._artifact.model.predict(matrix)[0])

    def _design_distance(self, current: RoutePlan, scenario: RoutePlan) -> float:
        """How far the proposal sits from the route as scheduled today.

        Euclidean distance in the same standardized feature space the bands were
        calibrated in.  An unchanged scenario scores exactly zero, which is what
        makes the interval collapse when nothing was changed.
        """

        scaling = self._artifact.distance_scaling
        median = np.asarray(scaling["median"], dtype=np.float64)
        mean = np.asarray(scaling["mean"], dtype=np.float64)
        std = np.asarray(scaling["std"], dtype=np.float64)

        def standardize(features: dict[str, Any]) -> np.ndarray:
            matrix, _, _ = build_matrix([features])
            row = matrix[0]
            filled = np.where(np.isfinite(row), row, median)
            return (filled - mean) / std

        # The same alignment the prediction uses, so a pure frequency change
        # scores distance zero and does not widen a band it cannot affect.
        current_features = self._aligned_features(current, service_from=current)
        scenario_features = self._aligned_features(scenario, service_from=current)
        return float(np.linalg.norm(standardize(scenario_features) - standardize(current_features)))

    def _skill_for(self, design_distance: float) -> dict[str, Any] | None:
        """Measured skill for a change of this size, from held-out cities."""

        for bucket in self._artifact.skill_buckets:
            if design_distance <= bucket["max_feature_distance"]:
                return bucket
        return self._artifact.skill_buckets[-1] if self._artifact.skill_buckets else None

    def _interval_for(self, design_distance: float) -> tuple[float, float]:
        """Absolute-error band for a change of this size, from held-out data."""

        for bucket in self._artifact.delta_error_buckets:
            if design_distance <= bucket["max_feature_distance"]:
                return bucket["abs_error_p80_kmh"], bucket["abs_error_p90_kmh"]
        last = self._artifact.delta_error_buckets[-1]
        return last["abs_error_p80_kmh"], last["abs_error_p90_kmh"]

    def _out_of_distribution(self, plan: RoutePlan, features: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        features = features if features is not None else plan.to_features()
        flagged: list[dict[str, Any]] = []
        for name, bounds in self._artifact.feature_envelope.items():
            value = features.get(name)
            if value is None or not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if value < bounds["low"] or value > bounds["high"]:
                flagged.append(
                    {
                        "feature": name,
                        "value": round(float(value), 3),
                        "trained_range": [round(bounds["low"], 3), round(bounds["high"], 3)],
                        "direction": "below" if value < bounds["low"] else "above",
                    }
                )
        return flagged

    @staticmethod
    def _drivers(current: RoutePlan, scenario: RoutePlan) -> list[dict[str, Any]]:
        """What the planner changed, and which way each change pushes speed.

        Directions come from the sign of the fitted linear coefficients, which
        are physically sensible (denser stops slower, wider spacing faster), and
        are stated as tendencies rather than as attributed amounts — decomposing
        a single prediction into per-feature contributions would imply a
        precision this model does not have.
        """

        drivers: list[dict[str, Any]] = []

        def note(label: str, before: Any, after: Any, effect: str, unit: str = "") -> None:
            if before is None or after is None:
                return
            if isinstance(before, float) and abs(before - after) < 1e-9:
                return
            if before == after:
                return
            drivers.append(
                {
                    "label": label,
                    "from": round(before, 2) if isinstance(before, float) else before,
                    "to": round(after, 2) if isinstance(after, float) else after,
                    "unit": unit,
                    "effect_on_speed": effect,
                }
            )

        # Length's fitted relationship with speed is confounded in the same way
        # frequency's is — long routes are long because they run on fast
        # suburban arterials. It is kept live because changing length really
        # does change which road a route uses, but the model cannot know which
        # end was trimmed, so the wording must not sound causal.
        note("Route length", current.one_way_length_km, scenario.one_way_length_km,
             "longer routes in the data tend to be faster ones; the model cannot know "
             "which segment you added"
             if scenario.one_way_length_km > current.one_way_length_km
             else "shorter routes in the data tend to be slower ones; the model cannot "
             "know which segment you removed",
             "km")
        note("Stops", current.stop_count, scenario.stop_count,
             "fewer stops tend to raise speed" if scenario.stop_count < current.stop_count
             else "more stops tend to lower speed")
        note("Stop density", current.stops_per_km, scenario.stops_per_km,
             "lower density tends to raise speed" if scenario.stops_per_km < current.stops_per_km
             else "higher density tends to lower speed", "stops/km")
        note("Peak headway", current.peak_headway_minutes, scenario.peak_headway_minutes,
             "changes the vehicle requirement; deliberately not applied to speed", "min")
        note("Service span", current.service_span_hours, scenario.service_span_hours,
             "changes service hours; deliberately not applied to speed", "h")
        return drivers

    def _fleet(
        self,
        scenario: RoutePlan,
        runtime: Interval,
        *,
        opposite_runtime_minutes: float | None,
        current_runtime_minutes: float | None,
    ) -> FleetEstimate | None:
        """Cycle time and concurrent vehicles, by the standard planning identity.

        Three cases, and the precedence matters.  **A loop wins over an
        observed opposite direction.**  Where an agency publishes a circular
        route as two directions those are two separate circuits, not two halves
        of one, and adding them doubles the cycle and the fleet.  This is the
        same order ``gtfs_normalizer`` uses when it builds the baseline rows, so
        the scenario column and the current column cannot disagree about a route
        nobody changed.

        The return leg is **scaled** by the same proportion the modelled
        direction moved.  Holding it at today's value while the outbound
        shrinks produced a "round trip" whose return was several times its
        outbound, over-estimating the fleet for every shortening scenario.
        """

        headway = scenario.sizing_headway_minutes
        if not headway:
            return None

        # How far the modelled direction moved, applied to the return leg too:
        # a planner shortening a route shortens both halves of it.
        scale = 1.0
        if current_runtime_minutes and current_runtime_minutes > 0:
            scale = runtime.point / current_runtime_minutes

        if scenario.loop_route:
            basis = "Loop route: one circuit is the full cycle."
        elif opposite_runtime_minutes is not None:
            basis = (
                "Return trip taken from the opposite direction's schedule, scaled by the same "
                f"proportion as this direction (×{scale:.2f})."
            )
        else:
            basis = "Return trip assumed symmetric (no opposite direction is published)."

        def cycle_for(one_way: float) -> tuple[float, float]:
            if scenario.loop_route:
                round_trip = one_way
            elif opposite_runtime_minutes is not None:
                # Each bound scales the return leg by its own proportion. The
                # speed uncertainty applies to the whole route; scaling by the
                # point proportion at every bound let only the outbound half
                # vary, so the vehicle range was narrower than the runtime range
                # printed beside it implied.
                bound_scale = (
                    one_way / current_runtime_minutes
                    if current_runtime_minutes and current_runtime_minutes > 0
                    else scale
                )
                round_trip = one_way + opposite_runtime_minutes * bound_scale
            else:
                round_trip = 2.0 * one_way
            recovery = max(scenario.recovery_fraction * round_trip, MIN_RECOVERY_MINUTES)
            return round_trip + recovery, recovery

        cycle_point, recovery = cycle_for(runtime.point)
        cycle_low, _ = cycle_for(runtime.low)
        cycle_high, _ = cycle_for(runtime.high)

        return FleetEstimate(
            vehicles=Interval(
                point=cycle_point / headway,
                low=cycle_low / headway,
                high=cycle_high / headway,
            ).rounded(2),
            cycle_time_minutes=Interval(cycle_point, cycle_low, cycle_high).rounded(1),
            recovery_minutes=round(recovery, 1),
            headway_minutes=headway,
            basis=basis
            + f" Recovery is assumed at {scenario.recovery_fraction:.0%} of the round trip "
            f"(at least {MIN_RECOVERY_MINUTES:g} min) — a planning assumption, not a "
            "published layover policy. This is an estimated vehicle requirement, never an "
            "assignment: real vehicle blocks interline across routes.",
        )

    # -- public ---------------------------------------------------------

    def estimate(
        self,
        *,
        current: RoutePlan,
        scenario: RoutePlan,
        measured_speed_kmh: float,
        opposite_runtime_minutes: float | None = None,
        current_runtime_minutes: float | None = None,
    ) -> ScenarioEstimate:
        """Estimate the scenario's speed, runtime and fleet.

        ``measured_speed_kmh`` is the route's *scheduled* commercial speed today.
        It anchors the estimate, so an unchanged scenario returns it exactly.
        """

        if measured_speed_kmh <= 0:
            raise ValueError("measured_speed_kmh must be positive")

        # "Did the planner change anything?" and "does the speed estimate move?"
        # are different questions, and conflating them told a planner who had
        # just doubled the frequency that nothing had changed.  Frozen inputs
        # score zero design distance by construction, so the unchanged test must
        # compare the whole plan.
        scenario_unchanged = scenario == current

        delta = self._predict_features(
            self._aligned_features(scenario, service_from=current)
        ) - self._predict_features(self._aligned_features(current, service_from=current))
        point = measured_speed_kmh + delta
        distance = self._design_distance(current, scenario)
        band_80, band_90 = self._interval_for(distance)
        if distance < 1e-9:
            # Nothing was changed, so the schedule itself is the answer and the
            # model has nothing to add.  Returning a band here would tell the
            # planner today's timetable is uncertain, which it is not.
            band_80 = band_90 = 0.0

        # Speed cannot sensibly go non-positive, and a band that crosses zero
        # would produce an infinite runtime; clamp to a floor well below the
        # slowest route ever observed rather than emitting an implausible speed.
        point = max(2.0, point)
        if band_80 <= 0.0:
            # An unchanged scenario is the published schedule. Widening it by even
            # a token amount would present today's timetable as uncertain.
            speed = Interval(point, point, point)
        else:
            low = max(2.0, point - band_80)
            speed = Interval(point, low, max(low + 0.1, point + band_80))

        runtime = Interval(
            point=runtime_minutes_from_speed(scenario.one_way_length_km, speed.point),
            # A faster speed means a shorter runtime, so the bounds swap.
            low=runtime_minutes_from_speed(scenario.one_way_length_km, speed.high),
            high=runtime_minutes_from_speed(scenario.one_way_length_km, speed.low),
        )

        ood = self._out_of_distribution(scenario)
        skill = self._skill_for(distance)
        reasons: list[str] = []
        confidence: Confidence = "high"

        # Confidence must track demonstrated skill, not just interval width. A
        # narrow band on a change the model cannot call the direction of is a
        # precise answer with no evidence behind it, and pairing that with a
        # "higher confidence" chip could lead a reader to over-interpret it.
        if skill and not scenario_unchanged:
            measured = skill.get("skill_vs_do_nothing")
            if measured is not None and measured < 0.05:
                confidence = "low"
                reasons.append(
                    f"for a change this size the model beats 'assume no effect' by only "
                    f"{measured:.0%} on held-out cities, and calls the direction right "
                    f"{skill.get('sign_agreement', 0):.0%} of the time"
                )
            elif measured is not None and measured < 0.15:
                confidence = "moderate"
                reasons.append(
                    f"held-out skill for a change this size is {measured:.0%} over assuming "
                    "no effect — treat the direction as a hint, not a finding"
                )

        if ood:
            confidence = "low"
            reasons.append(
                f"{len(ood)} input{'s' if len(ood) > 1 else ''} outside the range the model "
                "was trained on"
            )
        if abs(delta) > 4.0:
            confidence = "low" if confidence == "low" else "moderate"
            reasons.append(
                f"a predicted change of {delta:+.1f} km/h is large; the model's held-out "
                "accuracy on changes this size is much weaker"
            )
        relative_change = abs(scenario.stops_per_km - current.stops_per_km) / max(current.stops_per_km, 1e-6)
        if relative_change > 0.35:
            confidence = "low" if confidence == "low" else "moderate"
            reasons.append("stop density changes by more than a third")
        if abs(scenario.one_way_length_km - current.one_way_length_km) / current.one_way_length_km > 0.35:
            confidence = "low" if confidence == "low" else "moderate"
            reasons.append("route length changes by more than a third")
        if scenario_unchanged:
            confidence = "high"
            reasons = ["nothing was changed, so this is the published schedule, not an estimate"]
        elif distance < 1e-9:
            # Only frozen inputs moved. The speed genuinely does not change, but
            # the cycle time and fleet below very much do, and they rest on an
            # assumed recovery policy — so this is not "the published schedule".
            # The speed model is not consulted at all, so its held-out skill caveat
            # does not apply; keeping it beside "high" contradicted the chip.
            confidence = "high"
            reasons = [
                "only service frequency or span changed; those are deliberately not applied "
                "to running speed, so the speed shown is today's schedule",
                "the vehicle requirement below does change, and rests on the assumed "
                "recovery policy rather than on a published one",
            ]
        elif not reasons:
            reasons.append(
                "the change is modest and every input sits inside the observed training range"
            )

        return ScenarioEstimate(
            commercial_speed_kmh=speed.rounded(1),
            runtime_minutes=runtime.rounded(0),
            fleet=self._fleet(
                scenario,
                runtime,
                opposite_runtime_minutes=opposite_runtime_minutes,
                current_runtime_minutes=(
                    current_runtime_minutes
                    if current_runtime_minutes is not None
                    else runtime_minutes_from_speed(current.one_way_length_km, measured_speed_kmh)
                ),
            ),
            confidence=confidence,
            confidence_reasons=tuple(reasons),
            out_of_distribution=tuple(ood),
            drivers=tuple(self._drivers(current, scenario)),
            baseline_speed_kmh=round(measured_speed_kmh, 2),
            predicted_delta_kmh=round(delta, 3),
            scenario_unchanged=scenario_unchanged,
            design_distance=round(distance, 3),
            band_p90_kmh=round(band_90, 2),
            method=(
                "anchored difference: measured scheduled speed + model(scenario) - model(current); "
                "interval from held-out within-city errors for changes of this size"
            ),
        )
