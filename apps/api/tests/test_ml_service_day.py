from datetime import date, datetime, timezone

import pytest

from transitpulse_ml.service_day import ServiceSchedule, resolve_service_day


def test_normal_trip_uses_local_wall_clock_service_date() -> None:
    observed = datetime(2026, 9, 10, 15, 10, tzinfo=timezone.utc)  # 09:10 Regina
    assert resolve_service_day(
        observed,
        scheduled_start_seconds=9 * 3600,
        timezone_name="America/Regina",
    ) == date(2026, 9, 10)


def test_over_24_hour_trip_uses_previous_service_date_after_midnight() -> None:
    observed = datetime(2026, 9, 10, 7, 35, tzinfo=timezone.utc)  # 01:35 Regina
    assert resolve_service_day(
        observed,
        scheduled_start_seconds=25 * 3600 + 30 * 60,
        timezone_name="America/Regina",
    ) == date(2026, 9, 9)


def test_calendar_inactive_candidate_is_rejected() -> None:
    observed = datetime(2026, 9, 10, 15, 10, tzinfo=timezone.utc)
    assert resolve_service_day(
        observed,
        scheduled_start_seconds=9 * 3600,
        timezone_name="America/Regina",
        is_active=lambda day: day != date(2026, 9, 10),
    ) is None


def test_naive_observation_is_not_accepted() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_service_day(
            datetime(2026, 9, 10, 9, 10),
            scheduled_start_seconds=9 * 3600,
            timezone_name="America/Regina",
        )


def test_calendar_exception_overrides_base_weekday_service() -> None:
    schedule = ServiceSchedule(
        active_weekdays=frozenset({0, 1, 2, 3, 4}),
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
        exceptions={date(2026, 9, 10): 2, date(2026, 9, 12): 1},
    )
    assert schedule.is_active_on(date(2026, 9, 10)) is False
    assert schedule.is_active_on(date(2026, 9, 12)) is True
    assert schedule.is_active_on(date(2026, 9, 14)) is True
