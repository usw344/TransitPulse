"""Safety checks for destructive integration-test database fixtures."""

from __future__ import annotations

from sqlalchemy.engine import URL, make_url


def validated_disposable_test_url(value: str | None) -> URL:
    """Return a parsed URL only for an explicitly named disposable test DB.

    Requiring a dedicated database name is intentionally stricter than an
    opt-in boolean: a stale environment flag can never authorize truncation of
    the normal ``transitpulse`` database.
    """

    if not value:
        raise ValueError("TRANSITPULSE_TEST_DATABASE_URL is required")
    url = make_url(value)
    database = (url.database or "").lower()
    if not (database.endswith("_test") or database.startswith("test_")):
        raise ValueError(
            "destructive fixtures require a database named *_test or test_*"
        )
    return url
