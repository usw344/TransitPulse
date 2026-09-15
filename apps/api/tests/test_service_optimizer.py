"""Tests for the experimental service-design search.

The properties that matter here are mostly about what the search *refuses* to
do. An optimizer is the easiest place in a project like this to produce a
confident, precise, and completely unfounded recommendation, so these tests pin
the guard rails at least as hard as the arithmetic:

- it never searches geometry, because the model has no skill there
- it never consults the speed model at all
- constraints discard candidates rather than penalising them, so service
  cannot be deleted to win the objective
- results are deterministic and fully ordered
"""

from __future__ import annotations

import pytest

from transitpulse_ml.scenario_estimator import RoutePlan
from transitpulse_ml.service_optimizer import (
    ServiceConstraints,
    ServiceObjective,
    cycle_time_minutes,
    optimize_service,
    why_geometry_is_excluded,
)


def plan(**overrides) -> RoutePlan:
    base = dict(
        one_way_length_km=16.8,
        stop_count=58,
        day_type="weekday",
        peak_headway_minutes=10.0,
        offpeak_headway_minutes=15.0,
        median_headway_minutes=15.0,
        service_span_hours=21.0,
        directness_ratio=0.6,
        branch_count=1,
    )
    base.update(overrides)
    return RoutePlan(**base)


def run(**overrides):
    kwargs = dict(
        current=plan(),
        runtime_minutes=51.0,
        opposite_runtime_minutes=50.0,
        constraints=ServiceConstraints(max_vehicles=14.0),
    )
    kwargs.update(overrides)
    return optimize_service(**kwargs)


# --------------------------------------------------------------------------
# what it refuses to do
# --------------------------------------------------------------------------


def test_geometry_is_never_a_decision_variable():
    """Held out, the model's skill on small design changes is ~+0.8%. Searching
    stop count or route length against predicted speed would optimize model
    error and return an answer built from noise."""

    excluded = why_geometry_is_excluded()["excluded_variables"]
    assert set(excluded) == {"one_way_length_km", "stop_count", "stops_per_km"}

    result = run()
    for candidate in result["best"]:
        assert "one_way_length_km" not in candidate
        assert "stop_count" not in candidate
        assert "stops_per_km" not in candidate


def test_every_result_states_why_geometry_was_excluded():
    """An omission a reader cannot see looks like an oversight."""

    result = run()
    reason = result["geometry_excluded"]["reason"]
    assert "skill" in reason
    assert "noise" in reason
    assert result["geometry_excluded"]["evidence"]


def test_runtime_is_held_at_the_measured_value_and_is_declared():
    result = run()
    assert result["runtime_minutes_held_at"] == 51.0
    assert "not consulted" in result["runtime_basis"]


def test_the_current_design_is_scored_on_the_same_objective():
    """Without the current design's score a reader cannot tell a meaningful
    improvement from a rounding error, nor see that a high-frequency route
    scores badly only because the objective is blind to demand."""

    result = run()
    assert result["current"]["score_on_this_objective"] is not None
    assert result["best"][0]["score"] <= result["current"]["score_on_this_objective"]


def test_demand_blindness_is_the_first_caveat():
    """The search recommends cutting frequency on busy routes because it cannot
    see ridership. That has to be the first thing a reader meets."""

    first = run()["caveats"][0]
    assert "ridership" in first
    assert "frequency cut" in first
    assert "Never read a recommended frequency reduction" in first


def test_objective_and_constraints_are_persisted_with_the_result():
    """Weights change the answer, so an answer without them is unreadable."""

    result = run()
    assert result["objective"]["form"]
    assert result["objective"]["vehicle_weight"] == 1.0
    assert result["constraints"]["max_vehicles"] == 14.0
    assert any("proxy" in c.lower() or "ridership" in c.lower() for c in result["caveats"])


# --------------------------------------------------------------------------
# constraints are constraints
# --------------------------------------------------------------------------


def test_no_candidate_ever_breaches_the_fleet_limit():
    result = run(constraints=ServiceConstraints(max_vehicles=8.0))
    assert result["feasible_count"] > 0
    for candidate in result["best"]:
        assert candidate["peak_vehicles"] <= 8.0


def test_service_cannot_be_deleted_to_win_the_objective():
    """Cutting span and stretching headway is the classic way to win a badly
    posed transit objective. Both are hard constraints, so the winner cannot
    escape them however much it would lower the score."""

    result = run(
        constraints=ServiceConstraints(
            max_vehicles=14.0, min_service_span_hours=16.0, max_peak_headway_minutes=20.0
        )
    )
    for candidate in result["best"]:
        assert candidate["service_span_hours"] >= 16.0
        assert candidate["peak_headway_minutes"] <= 20.0


def test_offpeak_is_never_tighter_than_peak():
    """Enforced by how the grid is generated, not by discarding afterwards, so
    the invariant holds across every feasible candidate rather than only the
    ones that survived a filter."""

    result = run()
    assert result["feasible_count"] > 0
    for candidate in result["best"]:
        assert candidate["offpeak_headway_minutes"] >= candidate["peak_headway_minutes"]


def test_an_impossible_fleet_limit_yields_nothing_rather_than_a_bad_answer():
    result = run(constraints=ServiceConstraints(max_vehicles=0.5))
    assert result["feasible_count"] == 0
    assert result["best"] == []


# --------------------------------------------------------------------------
# arithmetic
# --------------------------------------------------------------------------


def test_cycle_time_uses_the_projects_single_precedence_rule():
    # Loop first: a circular route published as two directions is two circuits,
    # not two halves of one.
    loop, _ = cycle_time_minutes(
        runtime_minutes=40.0, opposite_runtime_minutes=39.0,
        loop_route=True, recovery_fraction=0.10,
    )
    assert loop == pytest.approx(40.0 + 5.0, abs=0.01)

    both, recovery = cycle_time_minutes(
        runtime_minutes=40.0, opposite_runtime_minutes=38.0,
        loop_route=False, recovery_fraction=0.10,
    )
    assert recovery == pytest.approx(7.8, abs=0.01)
    assert both == pytest.approx(78.0 + 7.8, abs=0.01)

    symmetric, _ = cycle_time_minutes(
        runtime_minutes=40.0, opposite_runtime_minutes=None,
        loop_route=False, recovery_fraction=0.10,
    )
    assert symmetric == pytest.approx(80.0 + 8.0, abs=0.01)


def test_fleet_is_exactly_cycle_over_headway():
    result = run()
    for candidate in result["best"]:
        assert candidate["peak_vehicles"] == pytest.approx(
            candidate["cycle_time_minutes"] / candidate["peak_headway_minutes"], abs=0.005
        )


def test_tightening_the_fleet_limit_never_improves_the_best_score():
    """A smaller feasible set cannot contain a better optimum."""

    loose = run(constraints=ServiceConstraints(max_vehicles=14.0))
    tight = run(constraints=ServiceConstraints(max_vehicles=9.0))
    assert tight["best"][0]["score"] >= loose["best"][0]["score"]


def test_weighting_waiting_more_heavily_buys_frequency():
    indifferent = run(objective=ServiceObjective(peak_wait_weight=0.0, offpeak_wait_weight=0.0))
    impatient = run(objective=ServiceObjective(peak_wait_weight=3.0, offpeak_wait_weight=1.0))
    assert impatient["best"][0]["peak_headway_minutes"] < indifferent["best"][0]["peak_headway_minutes"]


# --------------------------------------------------------------------------
# reproducibility
# --------------------------------------------------------------------------


def test_the_search_is_deterministic():
    assert run()["best"] == run()["best"]


def test_ordering_is_total_so_equal_scores_cannot_swap():
    result = run()
    best = result["best"]
    keys = [
        (c["score"], c["peak_headway_minutes"], c["offpeak_headway_minutes"],
         c["service_span_hours"], c["recovery_fraction"])
        for c in best
    ]
    assert keys == sorted(keys)


def test_rejects_an_impossible_runtime():
    with pytest.raises(ValueError):
        run(runtime_minutes=0)
    with pytest.raises(ValueError):
        run(runtime_minutes=float("nan"))
