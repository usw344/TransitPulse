"""Feature construction for the cross-city commercial-speed model.

This module is deliberately the *only* place features are built.  Training,
evaluation and the live scenario estimator all call the same functions, because
a scenario answered with even slightly different preprocessing than the model
was scored on is a silently wrong answer that no test would catch.

Target
------
``scheduled_commercial_speed_kmh`` — one-way length divided by scheduled
runtime, including dwell.  Speed is the target rather than runtime because it is
roughly scale-free: a 5 km route and a 25 km route with the same stop spacing
and street type run at similar speeds but wildly different runtimes, so speed is
the quantity that transfers between cities.  Runtime is then recovered as
``length / speed``, which keeps a scenario's arithmetic transparent.

Leakage
-------
``scheduled_commercial_speed_kmh = one_way_length_km / (runtime / 60)``.  Any
feature derived from runtime therefore reconstructs the target exactly.  These
are excluded and the exclusion is enforced by :data:`FORBIDDEN_FEATURES` and a
test, not by care:

``scheduled_runtime_minutes``      the denominator of the target
``estimated_cycle_time_minutes``   runtime + runtime + recovery
``estimated_recovery_minutes``     a fraction of the cycle time
``estimated_required_vehicles``    cycle time / headway

``one_way_length_km`` *is* kept.  It is the numerator, but it is also a genuine
design variable a planner chooses, it is known before any runtime exists, and
without it the model cannot represent that longer routes use faster roads.
Keeping the numerator and banning the denominator is the distinction that
matters.

``dominant_pattern_trip_share`` is also excluded, for a different reason: it
describes how cleanly *we extracted* the row, not how the route is designed.  A
proposed scenario has no such value, so a model leaning on it could not be
served.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np


TARGET = "scheduled_commercial_speed_kmh"

#: Continuous design and service variables, in a fixed order.  The order is part
#: of the model contract: a persisted model's coefficients mean nothing if the
#: columns are rebuilt in a different sequence.
NUMERIC_FEATURES: tuple[str, ...] = (
    "one_way_length_km",
    "stop_count",
    "stops_per_km",
    "mean_stop_spacing_m",
    "median_stop_spacing_m",
    "directness_ratio",
    "branch_count",
    "peak_headway_minutes",
    "offpeak_headway_minutes",
    "median_headway_minutes",
    "trips_per_day",
    "service_span_hours",
)

#: Booleans and one-hot day type.
BOOLEAN_FEATURES: tuple[str, ...] = ("loop_route",)
DAY_TYPES: tuple[str, ...] = ("weekday", "saturday", "sunday")

#: Never usable as features for this target.  Asserted by a test.
FORBIDDEN_FEATURES: frozenset[str] = frozenset(
    {
        "scheduled_runtime_minutes",
        "estimated_cycle_time_minutes",
        "estimated_recovery_minutes",
        "estimated_required_vehicles",
        "scheduled_commercial_speed_kmh",
        "dominant_pattern_trip_share",
    }
)


def feature_names(*, include_city: bool = False, cities: Sequence[str] = ()) -> list[str]:
    names = list(NUMERIC_FEATURES) + list(BOOLEAN_FEATURES)
    names += [f"day_type={day}" for day in DAY_TYPES]
    if include_city:
        names += [f"city={city}" for city in cities]
    return names


@dataclass(frozen=True)
class CohortRule:
    """Which rows are eligible for modelling, and why.

    The cohort is a *stated* filter rather than an ad-hoc one so a reviewer can
    see exactly what the reported metrics describe.  Every threshold here
    removes a row whose value would be measured from too little evidence to
    mean anything — not a row whose value is merely inconvenient.
    """

    route_kind: str = "bus"
    #: Below this a route is a school tripper or special, not a service pattern.
    min_trips_per_day: int = 8
    min_stop_count: int = 8
    #: Below this the row describes one branch rather than the route.
    min_dominant_pattern_share: float = 0.5
    #: Plausibility envelope for conventional urban surface transit.
    min_speed_kmh: float = 5.0
    max_speed_kmh: float = 60.0
    min_length_km: float = 1.0
    max_length_km: float = 60.0

    def admits(self, row: Mapping[str, Any]) -> bool:
        speed = row.get(TARGET)
        length = row.get("one_way_length_km")
        return bool(
            row.get("route_kind") == self.route_kind
            and (row.get("trips_per_day") or 0) >= self.min_trips_per_day
            and (row.get("stop_count") or 0) >= self.min_stop_count
            and (row.get("dominant_pattern_trip_share") or 0) >= self.min_dominant_pattern_share
            and speed is not None
            and self.min_speed_kmh <= speed <= self.max_speed_kmh
            and length is not None
            and self.min_length_km <= length <= self.max_length_km
            and row.get("median_headway_minutes") is not None
            and row.get("service_span_hours") is not None
        )

    def describe(self) -> dict[str, Any]:
        return {
            "route_kind": self.route_kind,
            "min_trips_per_day": self.min_trips_per_day,
            "min_stop_count": self.min_stop_count,
            "min_dominant_pattern_share": self.min_dominant_pattern_share,
            "speed_envelope_kmh": [self.min_speed_kmh, self.max_speed_kmh],
            "length_envelope_km": [self.min_length_km, self.max_length_km],
            "requires": ["median_headway_minutes", "service_span_hours"],
        }


def build_matrix(
    rows: Sequence[Mapping[str, Any]],
    *,
    include_city: bool = False,
    cities: Sequence[str] = (),
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Return ``(X, y, feature_names)``.

    Missing numeric values are left as ``NaN`` rather than imputed here.  Two
    reasons: ``HistGradientBoostingRegressor`` handles NaN natively and learns a
    split direction for it, and any imputation choice is a modelling decision
    that belongs to the pipeline being evaluated, not to feature construction
    where it would silently apply to every model at once.
    """

    names = feature_names(include_city=include_city, cities=cities)
    matrix = np.full((len(rows), len(names)), np.nan, dtype=np.float64)
    target = np.empty(len(rows), dtype=np.float64)

    for index, row in enumerate(rows):
        column = 0
        for name in NUMERIC_FEATURES:
            value = row.get(name)
            if value is not None:
                matrix[index, column] = float(value)
            column += 1
        for name in BOOLEAN_FEATURES:
            value = row.get(name)
            if value is not None:
                matrix[index, column] = 1.0 if value else 0.0
            column += 1
        for day in DAY_TYPES:
            matrix[index, column] = 1.0 if row.get("day_type") == day else 0.0
            column += 1
        if include_city:
            for city in cities:
                matrix[index, column] = 1.0 if row.get("city") == city else 0.0
                column += 1
        target[index] = float(row[TARGET])

    return matrix, target, names


def runtime_minutes_from_speed(length_km: float, speed_kmh: float) -> float:
    """Scenario arithmetic, kept in one place so every surface agrees."""

    if speed_kmh <= 0:
        raise ValueError("speed must be positive")
    return 60.0 * length_km / speed_kmh
