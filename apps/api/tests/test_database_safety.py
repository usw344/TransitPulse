import pytest
from sqlalchemy.engine import URL

from transitpulse_api.database_safety import validated_disposable_test_url


def test_requires_explicit_test_database_url() -> None:
    with pytest.raises(ValueError, match="required"):
        validated_disposable_test_url(None)


@pytest.mark.parametrize("database", ["transitpulse_test", "test_transitpulse"])
def test_accepts_explicitly_named_disposable_database(database: str) -> None:
    value = f"postgresql+psycopg://user:secret@localhost:5432/{database}"
    result = validated_disposable_test_url(value)
    assert isinstance(result, URL)
    assert result.database == database


def test_rejects_application_database_even_if_explicitly_supplied() -> None:
    with pytest.raises(ValueError, match="named"):
        validated_disposable_test_url(
            "postgresql+psycopg://user:secret@localhost:5432/transitpulse"
        )
