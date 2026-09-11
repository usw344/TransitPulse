"""GTFS service-day resolution for realtime observations.

GTFS schedules can use times beyond 24:00.  This module keeps that fact
separate from wall-clock dates so an after-midnight observation is not silently
assigned to the following service day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ServiceSchedule:
    """The static GTFS calendar facts needed for one trip's service ID."""

    active_weekdays: frozenset[int]
    start_date: date | None
    end_date: date | None
    exceptions: dict[date, int]

    def is_active_on(self, service_day: date) -> bool:
        """Apply GTFS calendar_dates exception types over the base calendar."""

        exception = self.exceptions.get(service_day)
        if exception == 1:
            return True
        if exception == 2:
            return False
        if self.start_date is not None and service_day < self.start_date:
            return False
        if self.end_date is not None and service_day > self.end_date:
            return False
        return service_day.weekday() in self.active_weekdays


def resolve_service_day(
    observed_at: datetime,
    *,
    scheduled_start_seconds: int,
    timezone_name: str,
    is_active: Callable[[date], bool] | None = None,
    max_start_offset_seconds: int = 14 * 60 * 60,
) -> date | None:
    """Return the GTFS service date compatible with an observation.

    Only the wall-clock date and its predecessor are candidates.  A candidate
    must place the scheduled trip start near the observation and, when given,
    pass the static-calendar predicate.  ``None`` is safer than guessing when
    the trip identity/timing cannot support a unique service day.
    """

    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    if scheduled_start_seconds < 0:
        raise ValueError("scheduled_start_seconds must be non-negative")
    if max_start_offset_seconds < 0:
        raise ValueError("max_start_offset_seconds must be non-negative")

    local_observed = observed_at.astimezone(ZoneInfo(timezone_name))
    candidates: list[tuple[float, date]] = []
    for service_day in (local_observed.date(), local_observed.date() - timedelta(days=1)):
        if is_active is not None and not is_active(service_day):
            continue
        scheduled_start = datetime.combine(service_day, time.min, tzinfo=local_observed.tzinfo)
        scheduled_start += timedelta(seconds=scheduled_start_seconds)
        offset = abs((local_observed - scheduled_start).total_seconds())
        if offset <= max_start_offset_seconds:
            candidates.append((offset, service_day))

    if not candidates:
        return None
    candidates.sort()
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None
    return candidates[0][1]
