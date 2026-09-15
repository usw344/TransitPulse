"""Create and migrate a disposable local database for the integration tests.

The integration fixtures in ``apps/api/tests/conftest.py`` TRUNCATE every
TransitPulse table, so they only run against a database whose name ends in
``_test`` (or starts with ``test_``).  This script provisions exactly such a
database on the same PostgreSQL server the application uses, then applies the
Alembic migrations to it.

It never drops, truncates or migrates anything else:

- the target name must pass the same validator the fixtures use;
- the target name must differ from the application database's name;
- an existing test database is reused, not recreated;
- migrations run in a child process whose ``TRANSITPULSE_DATABASE_URL`` is the
  test URL, and the script confirms the child connected to the test database.

Usage::

    .venv\\Scripts\\python.exe scripts\\setup_test_database.py [--name transitpulse_test] [--pytest]

``--pytest`` then runs the whole backend suite, integration tests included,
with ``TRANSITPULSE_TEST_DATABASE_URL`` set only in the child process, so the
password never has to be typed or printed. Arguments after ``--`` are passed to
pytest.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from transitpulse_api.config import settings  # noqa: E402
from transitpulse_api.database_safety import validated_disposable_test_url  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="transitpulse_test", help="test database name (must end in _test)")
    parser.add_argument("--pytest", action="store_true", help="run the backend test suite against it afterwards")
    args, pytest_args = parser.parse_known_args()
    pytest_args = [arg for arg in pytest_args if arg != "--"]

    app_url = make_url(settings.database_url)
    test_url = app_url.set(database=args.name)
    try:
        validated_disposable_test_url(test_url.render_as_string(hide_password=False))
    except ValueError as error:
        print(f"Refusing: {error}.")
        return 2
    if (app_url.database or "").lower() == args.name.lower():
        print("Refusing: the test database name matches the application database.")
        return 2

    # CREATE DATABASE cannot run inside a transaction and needs a connection to
    # some other database; the server's maintenance database is the standard one.
    admin_url = app_url.set(database="postgres")
    try:
        engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": args.name}
            ).scalar()
            if exists:
                print(f"Test database {args.name!r} already exists; reusing it.")
            else:
                # The name was validated above and is quoted as an identifier.
                connection.execute(text(f'CREATE DATABASE "{args.name}"'))
                print(f"Created test database {args.name!r}.")
        engine.dispose()
    except SQLAlchemyError as error:
        print(f"Could not create the test database on {app_url.host}:{app_url.port or 5432}.")
        print(f"The configured role needs CREATEDB permission. Detail: {error.__class__.__name__}")
        return 3

    env = dict(os.environ)
    env["TRANSITPULSE_DATABASE_URL"] = test_url.render_as_string(hide_password=False)
    probe = subprocess.run(
        [sys.executable, "-c", "from transitpulse_api.config import settings; from sqlalchemy.engine import make_url; print(make_url(settings.database_url).database)"],
        cwd=ROOT, env={**env, "PYTHONPATH": str(ROOT / "apps" / "api")}, capture_output=True, text=True,
    )
    if probe.stdout.strip() != args.name:
        print(f"Refusing to migrate: child process resolved database {probe.stdout.strip()!r}, not {args.name!r}.")
        return 4

    print(f"Applying migrations to {args.name!r} ...")
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env)
    if result.returncode != 0:
        print("Migrations failed against the test database.")
        return result.returncode

    print()
    if args.pytest:
        test_env = {
            **os.environ,
            "TRANSITPULSE_TEST_DATABASE_URL": test_url.render_as_string(hide_password=False),
            "PYTHONPATH": str(ROOT / "apps" / "api"),
        }
        print(f"Running the backend suite against {args.name!r} ...")
        # A dedicated basetemp: pytest's default (%TEMP%\pytest-of-<user>) can be
        # unwritable on Windows, which turns every tmp_path test into an error.
        basetemp = Path(tempfile.gettempdir()) / "transitpulse-pytest"
        return subprocess.run(
            [sys.executable, "-m", "pytest", "apps/api/tests", "-q", "-p", "no:cacheprovider",
             f"--basetemp={basetemp}", *pytest_args],
            cwd=ROOT,
            env=test_env,
        ).returncode
    print("Test database ready. Run the whole suite against it with:")
    print("  .venv\\Scripts\\python.exe scripts\\setup_test_database.py --pytest")
    print(f"or set TRANSITPULSE_TEST_DATABASE_URL={test_url.render_as_string(hide_password=True)}")
    print("(the password is the application database's; it is not printed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
