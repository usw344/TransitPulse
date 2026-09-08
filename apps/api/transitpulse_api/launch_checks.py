"""Small launcher checks with Windows-friendly, actionable failure messages."""

from __future__ import annotations

import argparse
from urllib.parse import urlparse

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from transitpulse_api.config import settings
from transitpulse_api.database import get_engine
from transitpulse_api.models import GtfsFeed


def database_target() -> str:
    parsed = urlparse(settings.database_url.replace("postgresql+psycopg", "postgresql", 1))
    return f"{parsed.hostname or 'localhost'}:{parsed.port or 5432}/{parsed.path.lstrip('/') or 'transitpulse'}"


def check_database() -> int:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        print(f"PostgreSQL is not reachable at {database_target()}.")
        return 2
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT PostGIS_Version()"))
    except SQLAlchemyError:
        print(f"PostgreSQL is reachable at {database_target()}, but the PostGIS extension is unavailable.")
        print("Install the PostGIS bundle that matches the local PostgreSQL major version, then retry.")
        return 3
    print(f"PostgreSQL/PostGIS is ready at {database_target()}.")
    return 0


def has_static_feed() -> int:
    try:
        with Session(get_engine()) as session:
            count = session.scalar(
                select(func.count()).select_from(GtfsFeed).where(GtfsFeed.import_status == "succeeded")
            ) or 0
    except SQLAlchemyError:
        print("TransitPulse migrations are not ready; static feed availability cannot be checked.")
        return 2
    print(f"Imported GTFS feeds: {count}")
    return 0 if count else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="TransitPulse launcher prerequisites")
    parser.add_argument("check", choices=("database", "feed"))
    args = parser.parse_args()
    raise SystemExit(check_database() if args.check == "database" else has_static_feed())


if __name__ == "__main__":
    main()
