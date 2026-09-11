import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from transitpulse_api.database import get_engine
from transitpulse_api.database_safety import validated_disposable_test_url
from transitpulse_api.config import settings
from transitpulse_api.main import app

_database_unavailable_reason: str | None = None
_test_database_url = os.getenv("TRANSITPULSE_TEST_DATABASE_URL")

# Integration fixtures deliberately truncate every TransitPulse table.  They
# must never silently point at the local application database, which can hold
# a multi-million-row GTFS import and recorded observations.  CI explicitly
# opts in because its PostGIS service is disposable.
if _test_database_url:
    # ``str(URL)`` masks the password as ``***``.  Preserve the configured
    # credential only in process memory so the isolated engine can connect;
    # no URL is logged by this fixture.
    settings.database_url = validated_disposable_test_url(_test_database_url).render_as_string(
        hide_password=False
    )
    get_engine.cache_clear()


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def database_ready() -> None:
    """Skip integration coverage only when the configured native DB is unavailable."""

    global _database_unavailable_reason
    if not _test_database_url:
        pytest.skip(
            "integration tests require TRANSITPULSE_TEST_DATABASE_URL; refusing to truncate the application database"
        )
    if _database_unavailable_reason is not None:
        pytest.skip(_database_unavailable_reason)
    try:
        with get_engine().connect() as connection:
            expected_database = validated_disposable_test_url(_test_database_url).database
            actual_database = connection.execute(
                text("SELECT current_database()")
            ).scalar_one()
            if actual_database != expected_database:
                pytest.fail(
                    "connected database identity does not match TRANSITPULSE_TEST_DATABASE_URL"
                )
            connection.execute(text("SELECT PostGIS_Version()"))
            if connection.execute(text("SELECT to_regclass('public.vehicle_observations')")).scalar_one() is None:
                _database_unavailable_reason = "TransitPulse migrations have not been applied to the configured database"
                pytest.skip(_database_unavailable_reason)
    except SQLAlchemyError:
        _database_unavailable_reason = "PostgreSQL/PostGIS is unavailable for integration testing"
        pytest.skip(_database_unavailable_reason)


@pytest.fixture
def db_session(database_ready: None) -> Session:
    """Give an integration test an isolated, migrated PostGIS database."""

    engine = get_engine()
    table_names = "vehicle_observations, realtime_alerts, realtime_trip_states, realtime_vehicle_states, realtime_feed_statuses, stop_times, calendar_dates, trips, shapes, stops, routes, service_calendars, agencies, gtfs_feeds"
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
    with Session(engine) as session:
        yield session
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
