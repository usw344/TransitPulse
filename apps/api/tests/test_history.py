from datetime import datetime, timezone
from uuid import uuid4

import pytest

from transitpulse_api.main import history_window
from transitpulse_api.realtime import NormalizedVehicle, observation_key


def _vehicle() -> NormalizedVehicle:
    return NormalizedVehicle(
        entity_id="entity-1",
        vehicle_id="bus-42",
        trip_id="trip-1",
        route_id="100",
        latitude=53.5,
        longitude=-113.5,
        bearing=None,
        speed=None,
        observed_at=datetime(2026, 9, 7, 12, tzinfo=timezone.utc),
        current_stop_sequence=None,
        current_status=None,
        schedule_relationship=None,
    )


def test_observation_key_is_deterministic_and_feed_version_scoped() -> None:
    source_time = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    feed_id = uuid4()
    assert observation_key(feed_id, source_time, _vehicle(), None) == observation_key(feed_id, source_time, _vehicle(), None)
    assert observation_key(uuid4(), source_time, _vehicle(), None) != observation_key(feed_id, source_time, _vehicle(), None)


def test_history_window_rejects_unbounded_or_reversed_ranges() -> None:
    start = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    end = datetime(2026, 9, 7, 13, tzinfo=timezone.utc)
    assert history_window(start, end) == (start, end)
    with pytest.raises(Exception, match="before end"):
        history_window(end, start)
    with pytest.raises(Exception, match="24 hours"):
        history_window(start, datetime(2026, 9, 8, 13, tzinfo=timezone.utc))
