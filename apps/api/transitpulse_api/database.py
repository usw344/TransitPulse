from functools import lru_cache

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from transitpulse_api.config import settings


@lru_cache
def get_engine() -> Engine:
    """Create the process-wide SQLAlchemy 2 engine only when it is first needed."""

    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )


def get_session() -> Generator[Session, None, None]:
    """Provide a request-scoped SQLAlchemy session."""

    with Session(get_engine()) as session:
        yield session
