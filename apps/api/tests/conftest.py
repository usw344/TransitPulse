import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from transitpulse_api.database import get_engine
from transitpulse_api.main import app

_database_unavailable_reason: str | None = None


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def database_ready() -> None:
    """Skip integration coverage only when the configured native DB is unavailable."""

    global _database_unavailable_reason
    if _database_unavailable_reason is not None:
        pytest.skip(_database_unavailable_reason)
    try:
        with get_engine().connect() as connection:
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
