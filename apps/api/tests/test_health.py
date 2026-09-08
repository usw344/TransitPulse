from fastapi.testclient import TestClient
from sqlalchemy import text

from transitpulse_api.database import get_engine


def test_api_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    proxied_response = client.get("/api/health")
    assert proxied_response.status_code == 200
    assert proxied_response.json() == {"status": "ok"}


def test_database_health_uses_real_postgis_connection(
    client: TestClient, database_ready: None
) -> None:
    response = client.get("/health/db")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["postgis_version"]


def test_postgis_spatial_function_is_available(database_ready: None) -> None:
    """This is an integration test: it requires the real configured PostgreSQL service."""

    with get_engine().connect() as connection:
        point = connection.execute(
            text(
                "SELECT ST_AsText(ST_SetSRID(ST_MakePoint(-114.0708, 51.0486), 4326))"
            )
        ).scalar_one()

    assert point == "POINT(-114.0708 51.0486)"
