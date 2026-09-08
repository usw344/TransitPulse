from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.fixtures.tiny_gtfs import make_gtfs_archive
from transitpulse_api.gtfs import import_gtfs_zip


def test_network_endpoints_use_current_feed(client: TestClient, db_session: Session) -> None:
    import_gtfs_zip(
        db_session, make_gtfs_archive(), provider="Test", source_url="https://example.test/feed.zip"
    )

    routes = client.get("/api/routes")
    assert routes.status_code == 200
    assert routes.json()[0]["route_id"] == "100"

    detail = client.get("/api/routes/100")
    assert detail.status_code == 200
    assert detail.json()["agency_name"] == "Example Transit"

    stops = client.get("/api/routes/100/stops")
    assert stops.status_code == 200
    assert stops.json()["type"] == "FeatureCollection"
    assert len(stops.json()["features"]) == 2
    assert stops.json()["features"][0]["geometry"]["coordinates"] == [-113.4938, 53.5461]

    shape = client.get("/api/routes/100/shape")
    assert shape.status_code == 200
    assert shape.json()["features"][0]["geometry"]["type"] == "LineString"


def test_network_returns_404_without_an_imported_feed(client: TestClient, db_session: Session) -> None:
    response = client.get("/api/routes")
    assert response.status_code == 404
