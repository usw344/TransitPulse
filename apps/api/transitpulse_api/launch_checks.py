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


def check_postgresql_server() -> int:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        print(f"PostgreSQL is not reachable at {database_target()}.")
        return 2
    print(f"PostgreSQL is reachable at {database_target()}.")
    return 0


def check_postgis_files() -> int:
    """Check server-side extension control files without requiring installation in this DB."""

    try:
        with get_engine().connect() as connection:
            default_version = connection.scalar(
                text("SELECT default_version FROM pg_available_extensions WHERE name = 'postgis'")
            )
    except SQLAlchemyError:
        print(f"PostgreSQL is reachable at {database_target()}, but PostGIS extension files cannot be checked.")
        return 3
    if default_version is None:
        print(f"PostGIS extension files are unavailable to PostgreSQL at {database_target()}.")
        print("Install the PostGIS bundle that matches the local PostgreSQL major version, then retry.")
        return 3
    print(f"PostGIS extension files are available (default version {default_version}).")
    return 0


def check_postgis_active() -> int:
    """Verify migration 0001 enabled PostGIS in this database."""

    try:
        with get_engine().connect() as connection:
            installed_version = connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'postgis'")
            )
            if installed_version is not None:
                connection.execute(text("SELECT PostGIS_Version()"))
    except SQLAlchemyError:
        print(f"PostGIS is registered in {database_target()}, but it could not be activated.")
        return 4
    if installed_version is None:
        print(f"PostGIS extension files are installed but PostGIS is not enabled in {database_target()}.")
        print("Run TransitPulse migrations so migration 0001 can enable the extension.")
        return 4
    print(f"PostGIS is active in {database_target()} (version {installed_version}).")
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
    parser.add_argument("check", choices=("server", "postgis-files", "postgis-active", "feed"))
    args = parser.parse_args()
    checks = {
        "server": check_postgresql_server,
        "postgis-files": check_postgis_files,
        "postgis-active": check_postgis_active,
        "feed": has_static_feed,
    }
    raise SystemExit(checks[args.check]())


if __name__ == "__main__":
    main()
