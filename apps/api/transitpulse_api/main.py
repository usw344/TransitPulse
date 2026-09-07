from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from transitpulse_api.database import get_engine

app = FastAPI(title="TransitPulse API", version="0.1.0")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Report that the API process is accepting requests."""

    return {"status": "ok"}


@app.get("/health/db", tags=["health"])
def database_health() -> dict[str, str]:
    """Execute a real PostGIS query and report the connected extension version."""

    try:
        with get_engine().connect() as connection:
            postgis_version = connection.execute(text("SELECT PostGIS_Version()")).scalar_one()
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="database unavailable") from error

    return {"status": "ok", "postgis_version": str(postgis_version)}
