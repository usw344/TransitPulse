"""Milestone N — a deliberately narrow experimental service-design search.

What this searches, and what it refuses to search
-------------------------------------------------

The scenario estimator answers "what would this design do?".  An optimizer asks
the harder question "which design is best?", and it is only allowed to ask that
where the answer rests on something reliable.

Measured leave-one-city-out, the model's skill at predicting the *effect of a
design change* is close to nothing for small changes: about +0.8% over assuming
the change does nothing at design distance 0.5, +2.1% at 1.0.  A search that
optimized stop density or route length against predicted speed would be
optimizing that noise, and would return a confident-looking answer produced
almost entirely by model error — an output that could easily be misread as a
real recommendation.

So the search space is restricted to the variables whose consequences are
**arithmetic rather than inferred**:

``peak_headway_minutes``   fleet is cycle time / headway — an identity
``offpeak_headway_minutes`` same
``service_span_hours``     service hours are span x vehicles — an identity
``recovery_fraction``      a stated policy, not a prediction

Runtime is held at the route's **measured scheduled runtime** throughout.  The
model is not consulted at all.  Every number this module produces is therefore
as trustworthy as the published timetable plus one declared recovery policy,
which is a much stronger footing than a predicted speed.

Geometry — stop count, route length — is deliberately **not** a decision
variable.  :func:`why_geometry_is_excluded` states that in the result so a
reader cannot mistake the omission for an oversight.

What "best" means
-----------------

There is no single objective in transit planning, so the objective is explicit,
weighted and persisted with every result.  The default trades peak vehicles
against the frequency riders actually get, under hard constraints.  Removing
service is the classic way to win a badly-posed transit objective, so a minimum
span and a maximum headway are **constraints, not penalties** — a candidate that
breaches them is discarded rather than scored.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable, Sequence

from transitpulse_ml.scenario_estimator import (
    MIN_RECOVERY_MINUTES,
    RoutePlan,
)


@dataclass(frozen=True)
class ServiceConstraints:
    """Hard limits. A candidate that breaches one is discarded, never scored.

    Expressing these as constraints rather than penalty terms is what stops the
    search from "improving" a route by deleting its service, which is the
    standard failure mode of a naive transit objective.
    """

    #: Peak vehicles the route may require. The binding operational limit.
    max_vehicles: float
    #: Riders must not wait longer than this at peak, whatever it saves.
    max_peak_headway_minutes: float = 30.0
    #: Nor may the route be shortened into a peak-only service.
    min_service_span_hours: float = 12.0
    #: Frequency cannot be raised past what the corridor can absorb.
    min_peak_headway_minutes: float = 5.0
    max_offpeak_headway_minutes: float = 60.0
    #: Recovery below this is not a schedule anyone could operate.
    min_recovery_fraction: float = 0.05
    max_recovery_fraction: float = 0.25

    def describe(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ServiceObjective:
    """What the search is trying to achieve, stated rather than implied.

    Weights are on normalized quantities so they are comparable: vehicles in
    vehicles, and waiting in minutes of average wait (half the headway, the
    standard approximation for random arrivals).
    """

    #: Cost of one additional peak vehicle, in objective units.
    vehicle_weight: float = 1.0
    #: Cost of one additional minute of average peak wait.
    #:
    #: Weighted ABOVE the vehicle cost on purpose.  With waiting cheap relative
    #: to buses, the arithmetic optimum is always the emptiest timetable the
    #: constraints permit -- the search returned a 23-minute headway over a
    #: 14-hour day against a route that runs every 10 minutes for 21.75 hours.
    #: That is a real optimum of a badly posed objective, and it is exactly the
    #: "improve the average by deleting the service" failure a transit reviewer
    #: should reject.  The question worth asking is not "how few buses can we
    #: run" but "given the buses this route already has, what is the best
    #: service?", and these weights pose that question.
    peak_wait_weight: float = 1.2
    #: Cost of one additional minute of average off-peak wait.
    offpeak_wait_weight: float = 0.5
    #: Cost of one additional revenue vehicle-hour.
    vehicle_hour_weight: float = 0.02

    def describe(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "form": "vehicle_weight * peak_vehicles "
            "+ peak_wait_weight * (peak_headway / 2) "
            "+ offpeak_wait_weight * (offpeak_headway / 2) "
            "+ vehicle_hour_weight * vehicle_hours",
            "note": "Average wait is half the headway, the standard approximation for "
            "random passenger arrivals. It is a PROXY for passenger experience, not a "
            "measurement: no ridership data exists for any adopted city, so this weights "
            "waiting time without knowing how many people do the waiting.",
        }


@dataclass(frozen=True)
class Candidate:
    """One feasible service design and its exactly-computed consequences."""

    peak_headway_minutes: float
    offpeak_headway_minutes: float
    service_span_hours: float
    recovery_fraction: float
    cycle_time_minutes: float
    recovery_minutes: float
    peak_vehicles: float
    vehicle_hours: float
    peak_trips: float
    score: float

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


def cycle_time_minutes(
    *,
    runtime_minutes: float,
    opposite_runtime_minutes: float | None,
    loop_route: bool,
    recovery_fraction: float,
) -> tuple[float, float]:
    """Cycle time and recovery, by the same rule the rest of the project uses.

    Loop first, then an observed opposite direction, then symmetry — identical
    to ``gtfs_normalizer`` and ``ScenarioEstimator._fleet`` so a candidate's
    fleet is directly comparable with the route's current one.
    """

    if loop_route:
        round_trip = runtime_minutes
    elif opposite_runtime_minutes is not None:
        round_trip = runtime_minutes + opposite_runtime_minutes
    else:
        round_trip = 2.0 * runtime_minutes
    recovery = max(recovery_fraction * round_trip, MIN_RECOVERY_MINUTES)
    return round_trip + recovery, recovery


def _frange(low: float, high: float, step: float) -> Iterable[float]:
    steps = int(round((high - low) / step))
    for index in range(steps + 1):
        yield round(low + index * step, 6)


def why_geometry_is_excluded() -> dict[str, Any]:
    """Stated in every result so the omission cannot look like an oversight."""

    return {
        "excluded_variables": ["one_way_length_km", "stop_count", "stops_per_km"],
        "reason": "Changing these moves the estimate only through the learned speed model, "
        "whose held-out skill on small design changes is close to zero (+0.8% over "
        "assuming no effect at design distance 0.5, +2.1% at 1.0). A search over them "
        "would optimize model error and return a confident answer built from noise.",
        "what_is_searched_instead": "Only variables whose consequences are arithmetic: "
        "headways, span and the recovery policy. Runtime is held at the route's measured "
        "scheduled value and the model is not consulted.",
        "evidence": "artifacts/experiments/cross_city_scenario_deltas_v3 (frozen operator, "
        "by_design_distance)",
    }


def optimize_service(
    *,
    current: RoutePlan,
    runtime_minutes: float,
    opposite_runtime_minutes: float | None,
    constraints: ServiceConstraints,
    objective: ServiceObjective | None = None,
    peak_step: float = 1.0,
    offpeak_step: float = 5.0,
    span_step: float = 0.5,
    recovery_step: float = 0.05,
    top_n: int = 8,
) -> dict[str, Any]:
    """Exhaustive grid search over the service-intensity variables.

    Exhaustive rather than a heuristic: the space is small enough to enumerate
    in full, and an exhaustive search over an explicit grid is exactly
    reproducible with no optimizer state to record.  Determinism matters more
    here than cleverness.
    """

    objective = objective or ServiceObjective()
    if runtime_minutes <= 0:
        raise ValueError("runtime_minutes must be positive")
    if not math.isfinite(runtime_minutes):
        raise ValueError("runtime_minutes must be finite")

    span_ceiling = min(24.0, max(current.service_span_hours or 18.0, constraints.min_service_span_hours))

    feasible: list[Candidate] = []
    evaluated = 0
    # Only counts things the grid can actually produce. Peak range, span floor
    # and "off-peak no tighter than peak" are enforced by how the grid is
    # generated, so counting them here would be a guard that never fires.
    rejected: dict[str, int] = {"vehicles_over_limit": 0}

    for recovery_fraction in _frange(
        constraints.min_recovery_fraction, constraints.max_recovery_fraction, recovery_step
    ):
        cycle, recovery = cycle_time_minutes(
            runtime_minutes=runtime_minutes,
            opposite_runtime_minutes=opposite_runtime_minutes,
            loop_route=current.loop_route,
            recovery_fraction=recovery_fraction,
        )
        for peak in _frange(
            constraints.min_peak_headway_minutes, constraints.max_peak_headway_minutes, peak_step
        ):
            vehicles = cycle / peak
            # Off-peak starts at the peak headway: service tighter off-peak than
            # at peak describes a timetable no agency operates, so it is never
            # generated rather than generated and then discarded.
            for offpeak in _frange(peak, constraints.max_offpeak_headway_minutes, offpeak_step):
                for span in _frange(constraints.min_service_span_hours, span_ceiling, span_step):
                    evaluated += 1
                    if vehicles > constraints.max_vehicles:
                        rejected["vehicles_over_limit"] += 1
                        continue

                    # Revenue vehicle-hours: peak fleet is required only during
                    # the peaks (assumed 5 h of a weekday), the rest of the span
                    # runs at the off-peak fleet.
                    peak_hours = min(5.0, span)
                    offpeak_hours = max(0.0, span - peak_hours)
                    offpeak_vehicles = cycle / offpeak
                    vehicle_hours = peak_hours * vehicles + offpeak_hours * offpeak_vehicles

                    score = (
                        objective.vehicle_weight * vehicles
                        + objective.peak_wait_weight * (peak / 2.0)
                        + objective.offpeak_wait_weight * (offpeak / 2.0)
                        + objective.vehicle_hour_weight * vehicle_hours
                    )
                    feasible.append(
                        Candidate(
                            peak_headway_minutes=peak,
                            offpeak_headway_minutes=offpeak,
                            service_span_hours=span,
                            recovery_fraction=recovery_fraction,
                            cycle_time_minutes=round(cycle, 2),
                            recovery_minutes=round(recovery, 2),
                            peak_vehicles=round(vehicles, 3),
                            vehicle_hours=round(vehicle_hours, 2),
                            peak_trips=round(peak_hours * 60.0 / peak, 2),
                            score=round(score, 4),
                        )
                    )

    # Sort fully deterministically: score, then every decision variable, so two
    # runs cannot disagree about which of two equal-scoring designs wins.
    feasible.sort(
        key=lambda c: (
            c.score,
            c.peak_headway_minutes,
            c.offpeak_headway_minutes,
            c.service_span_hours,
            c.recovery_fraction,
        )
    )

    current_cycle, current_recovery = cycle_time_minutes(
        runtime_minutes=runtime_minutes,
        opposite_runtime_minutes=opposite_runtime_minutes,
        loop_route=current.loop_route,
        recovery_fraction=current.recovery_fraction,
    )
    current_peak = current.peak_headway_minutes or current.median_headway_minutes
    current_vehicles = current_cycle / current_peak if current_peak else None
    current_offpeak = current.offpeak_headway_minutes or current_peak
    current_span = current.service_span_hours or 0.0
    current_score = None
    if current_peak and current_offpeak and current_span:
        peak_hours = min(5.0, current_span)
        offpeak_hours = max(0.0, current_span - peak_hours)
        current_vehicle_hours = (
            peak_hours * (current_cycle / current_peak)
            + offpeak_hours * (current_cycle / current_offpeak)
        )
        current_score = round(
            objective.vehicle_weight * (current_cycle / current_peak)
            + objective.peak_wait_weight * (current_peak / 2.0)
            + objective.offpeak_wait_weight * (current_offpeak / 2.0)
            + objective.vehicle_hour_weight * current_vehicle_hours,
            4,
        )

    return {
        "search": "exhaustive grid over service-intensity variables only",
        "runtime_minutes_held_at": runtime_minutes,
        "runtime_basis": "the route's measured scheduled runtime; the speed model is "
        "not consulted anywhere in this search",
        "objective": objective.describe(),
        "constraints": constraints.describe(),
        "geometry_excluded": why_geometry_is_excluded(),
        "combinations_evaluated": evaluated,
        "feasible_count": len(feasible),
        "rejected": rejected,
        "current": {
            "peak_headway_minutes": current_peak,
            "offpeak_headway_minutes": current.offpeak_headway_minutes,
            "service_span_hours": current.service_span_hours,
            "recovery_fraction": current.recovery_fraction,
            "cycle_time_minutes": round(current_cycle, 2),
            "recovery_minutes": round(current_recovery, 2),
            "peak_vehicles": round(current_vehicles, 3) if current_vehicles else None,
            # The current design scored on the SAME objective. Without this a
            # reader cannot tell whether a "better" candidate is better by a
            # meaningful margin or by a rounding error -- and cannot see that on
            # a high-frequency route today's design scores worse only because
            # the objective is blind to the demand that justifies it.
            "score_on_this_objective": current_score,
        },
        "best": [candidate.as_row() for candidate in feasible[:top_n]],
        "caveats": [
            "READ THIS FIRST. The objective has no ridership term, because no adopted city "
            "publishes comparable route-level demand. On a HIGH-FREQUENCY route the search "
            "will therefore recommend a large frequency cut -- on Edmonton route 004 it "
            "proposes 17-minute headways against today's 5 -- purely because it cannot see "
            "the passengers that justify running every 5 minutes. That is a statement about "
            "what this objective can see, not a finding about the route. Never read a "
            "recommended frequency reduction on a busy route as advice.",
            "Waiting time is a proxy weighted without any ridership data; it is not a "
            "passenger-benefit claim and no adopted city publishes route-level demand.",
            "Runtime is assumed unchanged. Raising frequency on a congested corridor can "
            "lengthen runtime in reality; nothing here models that.",
            "Vehicle counts are fractional requirements, not assignments: real blocks "
            "interline across routes and a fractional result is resolved by scheduling "
            "decisions this search does not see.",
            "The objective weights are a stated default, not an agency's. Changing them "
            "changes the answer, which is why they are persisted with every result.",
            "The search will ALWAYS choose the minimum allowed recovery, because a shorter "
            "layover is arithmetically a shorter cycle and therefore fewer vehicles. Nothing "
            "here models what recovery is for: absorbing running-time variability so a late "
            "trip does not start late. Treat the recovery figure as the constraint floor you "
            "gave it, never as a recommendation to cut layover.",
            "Frequency and span are bounded by the constraints you supply. The defaults hold "
            "span and fleet at the route's current values so a 'better' design has to serve "
            "the same day with the same buses, rather than score well by running less.",
        ],
    }
