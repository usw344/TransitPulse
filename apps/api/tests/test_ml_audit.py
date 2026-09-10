from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from transitpulse_ml.audit import _json_value


def test_json_value_normalizes_database_types() -> None:
    assert _json_value(datetime(2026, 9, 10, tzinfo=timezone.utc)) == (
        "2026-09-10T00:00:00+00:00"
    )
    assert _json_value(date(2026, 9, 10)) == "2026-09-10"
    assert _json_value(timedelta(seconds=31)) == 31
    assert _json_value(Decimal("1.25")) == 1.25
    assert _json_value(UUID("c5c72535-fe84-4517-bc59-3c9e8e485c27")) == (
        "c5c72535-fe84-4517-bc59-3c9e8e485c27"
    )
