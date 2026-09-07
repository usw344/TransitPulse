from functools import lru_cache

from sqlalchemy import Engine, create_engine

from transitpulse_api.config import settings


@lru_cache
def get_engine() -> Engine:
    """Create the process-wide SQLAlchemy 2 engine only when it is first needed."""

    return create_engine(settings.database_url, pool_pre_ping=True)
