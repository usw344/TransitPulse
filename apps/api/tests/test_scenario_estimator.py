"""Tests for the scenario estimator's contract and its guard rails.

These are the properties a planner's trust actually rests on, and each one is a
way the tool could quietly mislead if it broke:

- an unchanged scenario returns the published schedule, not an approximation
- the estimate responds to geometry in the physically correct direction
- frequency moves the fleet and never the running speed
- fleet arithmetic is the stated identity, not a fitted guess
- impossible inputs are rejected, and out-of-range inputs lower confidence
- no feature derived from the target can reach the model

The estimator is exercised against a small synthetic artifact rather than the
real one so the tests describe behaviour rather than today's fitted numbers.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from transitpulse_ml.cross_city_features import (
    FORBIDDEN_FEATURES,
    NUMERIC_FEATURES,
    TARGET,
    CohortRule,
    build_matrix,
    feature_names,
    runtime_minutes_from_speed,
)
from transitpulse_ml.scenario_estimator import (
    FROZEN_IN_DELTA,
    RoutePlan,
    ScenarioEstimator,
)


# --------------------------------------------------------------------------
# leakage: enforced, not merely intended
# --------------------------------------------------------------------------


def test_no_target_derived_feature_can_reach_the_model():
    """Commercial speed is length / runtime, so any runtime-derived feature
    reconstructs the target exactly. The ban is asserted, not documented."""

    assert FORBIDDEN_FEATURES.isdisjoint(set(feature_names()))
    assert "scheduled_runtime_minutes" not in NUMERIC_FEATURES
    assert "estimated_required_vehicles" not in NUMERIC_FEATURES
    assert "estimated_cycle_time_minutes" not in NUMERIC_FEATURES


def test_length_is_kept_even_though_it_is_the_numerator():
    """Length is a design variable known before any runtime exists; banning it
    would leave the model unable to represent that longer routes use faster
    roads. Keeping the numerator and banning the denominator is the rule."""

    assert "one_way_length_km" in NUMERIC_FEATURES


def test_feature_matrix_column_order_is_stable():
    names = feature_names()
    assert names[: len(NUMERIC_FEATURES)] == list(NUMERIC_FEATURES)
    matrix, _, returned = build_matrix(
        [{**_row(), TARGET: 20.0}],
    )
    assert returned == names
    assert matrix.shape == (1, len(names))


def _row(**overrides):
    row = {
        "one_way_length_km": 12.0,
        "stop_count": 36,
        "stops_per_km": 3.0,
        "mean_stop_spacing_m": 342.0,
        "median_stop_spacing_m": 330.0,
        "directness_ratio": 0.6,
        "branch_count": 1,
        "peak_headway_minutes": 15.0,
        "offpeak_headway_minutes": 20.0,
        "median_headway_minutes": 15.0,
        "trips_per_day": 60,
        "service_span_hours": 18.0,
        "loop_route": False,
        "day_type": "weekday",
        "route_kind": "bus",
        "dominant_pattern_trip_share": 1.0,
        TARGET: 22.0,
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------
# a synthetic estimator whose behaviour is known exactly
# --------------------------------------------------------------------------


class _LinearStub:
    """speed = 40 - 6 * stops_per_km + 0.1 * length - 0.5 * trips_per_day.

    The trips_per_day term exists purely so the frozen-input test can prove the
    freeze works: if service intensity ever leaked into the difference, a
    frequency-only scenario would move the speed by a large, obvious amount.
    """

    def __init__(self, names: list[str]) -> None:
        self._index = {name: position for position, name in enumerate(names)}

    def predict(self, matrix: np.ndarray) -> np.ndarray:
        stops_per_km = matrix[:, self._index["stops_per_km"]]
        length = matrix[:, self._index["one_way_length_km"]]
        trips = np.nan_to_num(matrix[:, self._index["trips_per_day"]])
        return 40.0 - 6.0 * stops_per_km + 0.1 * length - 0.5 * trips


@pytest.fixture
def estimator(tmp_path: Path) -> ScenarioEstimator:
    names = feature_names()
    directory = tmp_path / "estimator"
    directory.mkdir()
    with (directory / "model.pkl").open("wb") as handle:
        pickle.dump(_LinearStub(names), handle)

    count = len(names)
    metadata = {
        "version": "test",
        "feature_names": names,
        # Wider distance buckets get wider bands, as in the real artifact.
        # Skill in the same buckets, so confidence can track it.
        "held_out_change_skill": {
            "by_design_distance": [
                {"max_feature_distance": 2.0, "pairs": 4547,
                 "skill_vs_do_nothing": 0.023, "sign_agreement": 0.603},
                {"max_feature_distance": 1e9, "pairs": 140174,
                 "skill_vs_do_nothing": 0.248, "sign_agreement": 0.75},
            ],
        },
        "delta_error_buckets": [
            {"max_feature_distance": 1.0, "abs_error_p80_kmh": 1.0, "abs_error_p90_kmh": 1.5},
            {"max_feature_distance": 3.0, "abs_error_p80_kmh": 3.0, "abs_error_p90_kmh": 4.0},
            {"max_feature_distance": 1e9, "abs_error_p80_kmh": 6.0, "abs_error_p90_kmh": 8.0},
        ],
        # A single plausible spread for every column: enough that trimming one
        # stop is a small distance and halving the route is a large one, which
        # is what the bucketed band is meant to distinguish.
        "distance_scaling": {
            "median": [0.0] * count,
            "mean": [0.0] * count,
            "std": [8.0] * count,
        },
        "feature_envelope": {
            "one_way_length_km": {"low": 2.0, "high": 40.0, "median": 12.0},
            "stops_per_km": {"low": 0.6, "high": 4.6, "median": 2.9},
            "peak_headway_minutes": {"low": 5.0, "high": 60.0, "median": 20.0},
        },
        "limitations": ["synthetic artifact for tests"],
    }
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return ScenarioEstimator.load(directory)


def plan(**overrides) -> RoutePlan:
    base = dict(
        one_way_length_km=12.0,
        stop_count=36,
        day_type="weekday",
        peak_headway_minutes=15.0,
        offpeak_headway_minutes=20.0,
        median_headway_minutes=15.0,
        service_span_hours=18.0,
        directness_ratio=0.6,
        branch_count=1,
    )
    base.update(overrides)
    return RoutePlan(**base)


# --------------------------------------------------------------------------
# the anchoring contract
# --------------------------------------------------------------------------


def test_unchanged_scenario_returns_the_schedule_exactly(estimator):
    """The single most important behaviour: a planner who changes nothing must
    see today's timetable, not the model's approximation of it."""

    current = plan()
    result = estimator.estimate(current=current, scenario=current, measured_speed_kmh=22.4)
    assert result.commercial_speed_kmh.point == pytest.approx(22.4, abs=0.05)
    assert result.commercial_speed_kmh.low == result.commercial_speed_kmh.high
    assert result.predicted_delta_kmh == 0.0
    assert result.design_distance == 0.0
    assert result.confidence == "high"
    assert "nothing was changed" in " ".join(result.confidence_reasons)


def test_estimate_is_anchored_to_the_measured_speed(estimator):
    """Two routes with identical designs but different measured speeds must get
    different answers: the city and route offset is carried, not modelled."""

    current, scenario = plan(), plan(stop_count=30)
    slow = estimator.estimate(current=current, scenario=scenario, measured_speed_kmh=18.0)
    fast = estimator.estimate(current=current, scenario=scenario, measured_speed_kmh=26.0)
    assert fast.commercial_speed_kmh.point - slow.commercial_speed_kmh.point == pytest.approx(8.0, abs=0.05)
    assert slow.predicted_delta_kmh == pytest.approx(fast.predicted_delta_kmh)


def test_removing_stops_raises_speed_and_shortens_runtime(estimator):
    current = plan()
    result = estimator.estimate(
        current=current, scenario=plan(stop_count=28), measured_speed_kmh=22.0
    )
    assert result.predicted_delta_kmh > 0
    assert result.commercial_speed_kmh.point > 22.0
    scheduled = runtime_minutes_from_speed(12.0, 22.0)
    assert result.runtime_minutes.point < scheduled


def test_adding_stops_lowers_speed_and_lengthens_runtime(estimator):
    current = plan()
    result = estimator.estimate(
        current=current, scenario=plan(stop_count=52), measured_speed_kmh=22.0
    )
    assert result.predicted_delta_kmh < 0
    assert result.commercial_speed_kmh.point < 22.0
    assert result.runtime_minutes.point > runtime_minutes_from_speed(12.0, 22.0)


def test_runtime_bounds_invert_the_speed_bounds(estimator):
    """A faster speed is a shorter runtime. Carrying the bounds straight across
    would report a low runtime for a low speed, which is backwards."""

    result = estimator.estimate(
        current=plan(), scenario=plan(stop_count=24), measured_speed_kmh=22.0
    )
    assert result.runtime_minutes.low < result.runtime_minutes.point < result.runtime_minutes.high
    assert result.commercial_speed_kmh.low < result.commercial_speed_kmh.high
    assert result.runtime_minutes.low == pytest.approx(
        runtime_minutes_from_speed(plan(stop_count=24).one_way_length_km, result.commercial_speed_kmh.high),
        abs=0.6,
    )


# --------------------------------------------------------------------------
# the causal freeze
# --------------------------------------------------------------------------


def test_changing_frequency_alone_does_not_move_the_speed(estimator):
    """The fitted relationship between frequency and speed is confounded, not
    causal: frequent routes run in congested corridors. Applying it to a
    counterfactual would tell a planner that adding buses slows the route."""

    current = plan()
    scenario = plan(peak_headway_minutes=8.0, median_headway_minutes=8.0)
    result = estimator.estimate(current=current, scenario=scenario, measured_speed_kmh=22.0)
    assert result.predicted_delta_kmh == pytest.approx(0.0, abs=1e-9)
    assert result.commercial_speed_kmh.point == pytest.approx(22.0, abs=0.05)
    assert result.design_distance == pytest.approx(0.0, abs=1e-9)


def test_changing_span_alone_does_not_move_the_speed(estimator):
    result = estimator.estimate(
        current=plan(), scenario=plan(service_span_hours=22.0), measured_speed_kmh=22.0
    )
    assert result.predicted_delta_kmh == pytest.approx(0.0, abs=1e-9)


def test_frozen_set_covers_every_service_intensity_input():
    assert FROZEN_IN_DELTA == {
        "peak_headway_minutes",
        "offpeak_headway_minutes",
        "median_headway_minutes",
        "trips_per_day",
        "service_span_hours",
    }


# --------------------------------------------------------------------------
# fleet arithmetic
# --------------------------------------------------------------------------


def test_fleet_is_cycle_over_headway_with_both_legs(estimator):
    current = plan()
    result = estimator.estimate(
        current=current,
        scenario=current,
        measured_speed_kmh=22.0,
        opposite_runtime_minutes=30.0,
    )
    runtime = result.runtime_minutes.point
    round_trip = runtime + 30.0
    recovery = max(0.10 * round_trip, 5.0)
    assert result.fleet is not None
    assert result.fleet.recovery_minutes == pytest.approx(recovery, abs=0.1)
    assert result.fleet.cycle_time_minutes.point == pytest.approx(round_trip + recovery, abs=0.3)
    assert result.fleet.vehicles.point == pytest.approx(
        (round_trip + recovery) / 15.0, abs=0.05
    )


def test_halving_the_headway_roughly_doubles_the_vehicle_requirement(estimator):
    current = plan()
    wide = estimator.estimate(
        current=current, scenario=plan(peak_headway_minutes=20.0),
        measured_speed_kmh=22.0, opposite_runtime_minutes=30.0,
    )
    tight = estimator.estimate(
        current=current, scenario=plan(peak_headway_minutes=10.0),
        measured_speed_kmh=22.0, opposite_runtime_minutes=30.0,
    )
    assert tight.fleet.vehicles.point == pytest.approx(2 * wide.fleet.vehicles.point, rel=0.02)


def test_loop_wins_over_an_observed_opposite_direction(estimator):
    """Agencies publish some circular routes as two directions. Those are two
    separate circuits, not two halves of one, so adding them doubles the cycle
    and the fleet. The loop test must therefore be applied first."""

    loop = plan(loop_route=True)
    result = estimator.estimate(
        current=loop, scenario=loop, measured_speed_kmh=22.0,
        opposite_runtime_minutes=30.0,
    )
    runtime = result.runtime_minutes.point
    assert result.fleet.cycle_time_minutes.point == pytest.approx(
        runtime + max(0.10 * runtime, 5.0), abs=0.3
    )
    assert "loop route" in result.fleet.basis.lower()


def test_return_leg_scales_with_the_modelled_direction(estimator):
    """Holding the return leg at today's value while the outbound shrinks
    produces a round trip whose return is several times its outbound, and
    over-estimates the fleet for every shortening scenario."""

    current = plan()
    short = plan(one_way_length_km=6.0, stop_count=18)
    unscaled = estimator.estimate(
        current=current, scenario=short, measured_speed_kmh=22.0,
        opposite_runtime_minutes=30.0, current_runtime_minutes=32.7,
    )
    outbound = unscaled.runtime_minutes.point
    round_trip = unscaled.fleet.cycle_time_minutes.point - unscaled.fleet.recovery_minutes
    return_leg = round_trip - outbound
    # The return leg must have shrunk with the outbound, not stayed at 30 min.
    assert return_leg < 30.0
    assert return_leg == pytest.approx(30.0 * outbound / 32.7, rel=0.02)
    assert "scaled by the same proportion" in unscaled.fleet.basis


def test_fleet_range_scales_the_return_leg_at_each_bound(estimator):
    """The speed range applies to the whole route. Scaling the return leg by the
    point proportion at every bound let only the outbound half vary, so a planner
    checking the vehicle range against the runtime range found it too narrow."""

    current = plan()
    short = plan(one_way_length_km=6.0, stop_count=18)
    result = estimator.estimate(
        current=current, scenario=short, measured_speed_kmh=22.0,
        opposite_runtime_minutes=30.0, current_runtime_minutes=32.7,
    )
    runtime = result.runtime_minutes
    assert runtime.low < runtime.point < runtime.high

    def expected_cycle(one_way: float) -> float:
        round_trip = one_way * (1.0 + 30.0 / 32.7)
        return round_trip + max(0.10 * round_trip, 5.0)

    # The runtime interval is reported in whole minutes, the fleet from the
    # unrounded values, hence the tolerance.
    cycle = result.fleet.cycle_time_minutes
    assert cycle.low == pytest.approx(expected_cycle(runtime.low), abs=1.5)
    assert cycle.high == pytest.approx(expected_cycle(runtime.high), abs=1.5)
    vehicles = result.fleet.vehicles
    assert vehicles.low == pytest.approx(cycle.low / result.fleet.headway_minutes, abs=0.02)
    assert vehicles.high == pytest.approx(cycle.high / result.fleet.headway_minutes, abs=0.02)


def test_frequency_only_change_is_not_called_unchanged(estimator):
    """A frequency-only scenario scores zero design distance by construction,
    because the frozen inputs are aligned on both sides. Reporting that as
    "nothing was changed" told a planner who had just doubled the frequency —
    and moved the fleet by 23 vehicles — that they had changed nothing."""

    current = plan()
    faster = plan(peak_headway_minutes=7.5, median_headway_minutes=7.5)
    result = estimator.estimate(
        current=current, scenario=faster, measured_speed_kmh=22.0,
        opposite_runtime_minutes=30.0,
    )
    assert result.design_distance == pytest.approx(0.0, abs=1e-9)
    assert result.scenario_unchanged is False
    joined = " ".join(result.confidence_reasons)
    assert "nothing was changed" not in joined
    assert "not applied to running speed" in joined
    assert "recovery policy" in joined
    # The speed model is not consulted, so its skill caveat must not sit beside
    # a "high" confidence it would contradict.
    assert result.confidence == "high"
    assert "assume no effect" not in joined
    # And the thing that did change, changed.
    assert result.fleet.headway_minutes == 7.5


def test_confidence_reflects_measured_skill_not_just_interval_width(estimator):
    """A narrow band on a change whose direction the model cannot call is a
    precise answer with no evidence behind it. Pairing that with a "higher
    confidence" chip could lead a reader to over-interpret the estimate."""

    # A small edit falls in the low-skill bucket (2.3% over assuming no effect).
    small = estimator.estimate(
        current=plan(), scenario=plan(stop_count=35), measured_speed_kmh=22.0
    )
    assert small.design_distance <= 2.0
    assert small.confidence == "low"
    assert any("beats 'assume no effect' by only" in r for r in small.confidence_reasons)

    # A wholesale redesign falls in the high-skill bucket and is not downgraded
    # on skill grounds (it may still be downgraded for other stated reasons).
    large = estimator.estimate(
        current=plan(), scenario=plan(stop_count=18, one_way_length_km=7.0),
        measured_speed_kmh=22.0,
    )
    assert large.design_distance > 2.0
    assert not any("beats 'assume no effect' by only" in r for r in large.confidence_reasons)


def test_genuinely_unchanged_scenario_still_says_so(estimator):
    current = plan()
    result = estimator.estimate(current=current, scenario=current, measured_speed_kmh=22.0)
    assert result.scenario_unchanged is True
    assert "nothing was changed" in " ".join(result.confidence_reasons)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_inputs_are_rejected(bad):
    """NaN passes every comparison without tripping one and would propagate
    silently into a served estimate."""

    with pytest.raises(ValueError):
        plan(one_way_length_km=bad)
    with pytest.raises(ValueError):
        plan(peak_headway_minutes=bad)


def test_loop_route_cycle_is_not_doubled(estimator):
    """A loop's one-way circuit already is its cycle; doubling it would inflate
    every loop route's fleet estimate by roughly a factor of two."""

    loop = plan(loop_route=True)
    result = estimator.estimate(current=loop, scenario=loop, measured_speed_kmh=22.0)
    runtime = result.runtime_minutes.point
    expected_cycle = runtime + max(0.10 * runtime, 5.0)
    assert result.fleet.cycle_time_minutes.point == pytest.approx(expected_cycle, abs=0.3)
    assert "loop route" in result.fleet.basis.lower()


def test_symmetric_assumption_is_declared_when_no_return_leg_is_known(estimator):
    current = plan()
    result = estimator.estimate(current=current, scenario=current, measured_speed_kmh=22.0)
    assert "assumed symmetric" in result.fleet.basis
    assert "never an assignment" in result.fleet.basis
    # The recovery policy is this project's assumption, not a published one, and
    # the sentence a planner reads has to say so.
    assert "not a published layover policy" in result.fleet.basis


def test_recovery_policy_changes_the_fleet(estimator):
    current = plan()
    lean = estimator.estimate(
        current=current, scenario=plan(recovery_fraction=0.05),
        measured_speed_kmh=22.0, opposite_runtime_minutes=30.0,
    )
    generous = estimator.estimate(
        current=current, scenario=plan(recovery_fraction=0.20),
        measured_speed_kmh=22.0, opposite_runtime_minutes=30.0,
    )
    assert generous.fleet.vehicles.point > lean.fleet.vehicles.point


# --------------------------------------------------------------------------
# guard rails
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"one_way_length_km": 0},
        {"one_way_length_km": -3},
        {"stop_count": 1},
        {"peak_headway_minutes": 0},
        {"service_span_hours": 30},
        {"day_type": "tuesday"},
        {"recovery_fraction": 1.5},
    ],
)
def test_impossible_plans_are_rejected(kwargs):
    with pytest.raises(ValueError):
        plan(**kwargs)


def test_out_of_range_inputs_are_flagged_and_lower_confidence(estimator):
    result = estimator.estimate(
        current=plan(),
        scenario=plan(one_way_length_km=90.0, stop_count=6),
        measured_speed_kmh=22.0,
    )
    assert result.confidence == "low"
    flagged = {flag["feature"] for flag in result.out_of_distribution}
    assert "one_way_length_km" in flagged
    assert any("outside the range" in reason for reason in result.confidence_reasons)


def test_interval_widens_with_the_size_of_the_change(estimator):
    current = plan()
    small = estimator.estimate(current=current, scenario=plan(stop_count=35), measured_speed_kmh=22.0)
    large = estimator.estimate(current=current, scenario=plan(stop_count=18, one_way_length_km=7.0), measured_speed_kmh=22.0)
    small_band = small.commercial_speed_kmh.high - small.commercial_speed_kmh.low
    large_band = large.commercial_speed_kmh.high - large.commercial_speed_kmh.low
    assert large.design_distance > small.design_distance
    assert large_band > small_band


def test_speed_never_goes_non_positive(estimator):
    """A non-positive speed would make the runtime infinite or negative."""

    result = estimator.estimate(
        current=plan(), scenario=plan(stop_count=300, one_way_length_km=2.0),
        measured_speed_kmh=8.0,
    )
    assert result.commercial_speed_kmh.low > 0
    assert result.runtime_minutes.point > 0


def test_measured_speed_must_be_positive(estimator):
    with pytest.raises(ValueError):
        estimator.estimate(current=plan(), scenario=plan(), measured_speed_kmh=0.0)


def test_drivers_describe_only_what_changed(estimator):
    result = estimator.estimate(
        current=plan(), scenario=plan(stop_count=30), measured_speed_kmh=22.0
    )
    labels = {driver["label"] for driver in result.drivers}
    assert "Stops" in labels
    assert "Service span" not in labels
    headway_drivers = [d for d in result.drivers if d["label"] == "Peak headway"]
    assert headway_drivers == []


def test_frequency_driver_says_it_does_not_affect_speed(estimator):
    result = estimator.estimate(
        current=plan(), scenario=plan(peak_headway_minutes=10.0), measured_speed_kmh=22.0
    )
    headway = next(d for d in result.drivers if d["label"] == "Peak headway")
    assert "not applied to speed" in headway["effect_on_speed"]


# --------------------------------------------------------------------------
# cohort rule
# --------------------------------------------------------------------------


def test_cohort_excludes_school_trippers_and_keeps_ordinary_service():
    rule = CohortRule()
    assert rule.admits(_row())
    # Two trips a day over three timepoints is a school special, not a service
    # pattern, and its stop density is not comparable with anything.
    assert not rule.admits(_row(trips_per_day=2, stop_count=3))
    assert not rule.admits(_row(route_kind="light_rail"))
    assert not rule.admits(_row(**{TARGET: 95.0}))
    assert not rule.admits(_row(median_headway_minutes=None))
    assert not rule.admits(_row(dominant_pattern_trip_share=0.2))


def test_serialized_estimate_is_plain_data(estimator):
    payload = estimator.estimate(
        current=plan(), scenario=plan(stop_count=30), measured_speed_kmh=22.0
    ).to_dict()
    assert json.loads(json.dumps(payload))["commercial_speed_kmh"]["point"] > 0
    assert isinstance(payload["confidence_reasons"], list)
    assert isinstance(payload["drivers"], list)
