"""HTTP-level tests for the SCENARIOS endpoints.

The estimator itself is covered by ``test_scenario_estimator.py``.  What these
add is the boundary the frontend actually talks to: that a missing artifact is a
clean 503 rather than a crash, that hostile or impossible input is a 4xx rather
than a 500, and that the response carries the model's own uncertainty and
limitation fields instead of the UI having to invent them.

Every test builds a throwaway artifact directory and points the service at it
through ``TRANSITPULSE_ARTIFACT_ROOT``, so none of this depends on the real
model having been built on this machine, and none of it can read or mutate the
real one.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from transitpulse_api.scenarios import DEFAULT_DATASET, DEFAULT_MODEL
from transitpulse_ml.cross_city_features import TARGET, feature_names

_STOPS_PER_KM = feature_names().index("stops_per_km")


class _StubModel:
    """A deterministic stand-in for the fitted estimator.

    Defined at module level because the service loads its model with
    ``pickle``, and pickle cannot serialize a class defined inside a fixture.
    """

    def predict(self, matrix: np.ndarray) -> np.ndarray:
        return 40.0 - 6.0 * matrix[:, _STOPS_PER_KM]


@pytest.fixture
def artifact_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal but structurally real dataset + model artifact pair."""

    names = feature_names()
    root = tmp_path / "artifacts"
    dataset = root / "datasets" / DEFAULT_DATASET
    model = root / "models" / DEFAULT_MODEL
    dataset.mkdir(parents=True)
    model.mkdir(parents=True)

    rows = []
    for index, (direction, day) in enumerate(
        [(0, "weekday"), (1, "weekday"), (0, "saturday")]
    ):
        rows.append(
            {
                "city": "Edmonton",
                "agency": "Edmonton Transit Service",
                "route_kind": "bus",
                "route_label": "042",
                "direction_id": direction,
                "day_type": day,
                "one_way_length_km": 14.0 + index,
                "stop_count": 40,
                "stops_per_km": (40) / (14.0 + index),
                "mean_stop_spacing_m": 350.0,
                "median_stop_spacing_m": 340.0,
                "directness_ratio": 0.62,
                "branch_count": 1,
                "dominant_pattern_trip_share": 1.0,
                "loop_route": False,
                "scheduled_runtime_minutes": 38.0,
                TARGET: 22.1,
                "peak_headway_minutes": 15.0,
                "offpeak_headway_minutes": 20.0,
                "median_headway_minutes": 15.0,
                "headway_sample_count": 40,
                "trips_per_day": 60,
                "service_span_hours": 18.0,
                "estimated_cycle_time_minutes": 90.0,
                "estimated_recovery_minutes": 8.0,
                "estimated_required_vehicles": 6.0,
                "vehicles_from_block_data": False,
                "ridership_measure": None,
                "route_boardings_per_day": None,
                "provenance": {"original_route_id": "042", "source_id": "edmonton-ets"},
            }
        )
        # A second city must never appear in the Edmonton route list.
        rows.append({**rows[-1], "city": "Calgary", "route_label": "C9"})

    with (dataset / "rows.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    (dataset / "manifest.json").write_text(
        json.dumps({"file_checksums": {"rows.jsonl": "DEADBEEF"}}), encoding="utf-8"
    )

    with (model / "model.pkl").open("wb") as handle:
        pickle.dump(_StubModel(), handle)
    count = len(names)
    (model / "metadata.json").write_text(
        json.dumps(
            {
                "version": "test",
                "dataset": DEFAULT_DATASET,
                "dataset_rows_checksum": "DEADBEEF",
                "schema_version": "1.2.0",
                "target": TARGET,
                "model_family": "stub",
                "model_selection": "stub",
                "training_rows": 10,
                "training_cities": ["Edmonton", "Calgary"],
                "quality_measured_on": "leave-one-city-out folds only",
                "interval_basis": "stub",
                "feature_names": names,
                "delta_error_buckets": [
                    {"max_feature_distance": 1e9, "abs_error_p80_kmh": 3.0, "abs_error_p90_kmh": 4.0}
                ],
                "distance_scaling": {
                    "median": [0.0] * count,
                    "mean": [0.0] * count,
                    "std": [8.0] * count,
                },
                "feature_envelope": {
                    "one_way_length_km": {"low": 2.0, "high": 40.0, "median": 14.0}
                },
                "held_out_absolute_accuracy": {"model": "ridge", "mae_kmh": 3.36, "per_city_mae_kmh": {}},
                "held_out_change_skill": {
                    "operator": "frozen (production)",
                    "skill_vs_do_nothing": 0.182,
                    "sign_agreement_on_material_changes": 0.714,
                    "delta_mae_kmh": 4.34,
                    "pooled_caveat": "pooled over arbitrary pairs",
                    "by_design_distance": [
                        {"max_feature_distance": 1.0, "pairs": 4534,
                         "skill_vs_do_nothing": 0.021, "sign_agreement": 0.60},
                        {"max_feature_distance": 1e9, "pairs": 140137,
                         "skill_vs_do_nothing": 0.248, "sign_agreement": 0.75},
                    ],
                    "per_city": {},
                },
                "distinct_route_directions": 4,
                "effective_sample_note": "6 rows describe 4 route-directions",
                "limitations": ["no ridership is modelled"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("TRANSITPULSE_ARTIFACT_ROOT", str(root))
    import transitpulse_api.scenarios as scenarios

    scenarios.load_scenario_service.cache_clear()
    yield root
    scenarios.load_scenario_service.cache_clear()


@pytest.fixture
def client(artifact_root: Path) -> TestClient:
    from transitpulse_api.main import app

    return TestClient(app)


def test_routes_lists_only_the_scenario_city(client: TestClient):
    """Calgary rows train the model but must never be offered as a baseline: the
    product serves Edmonton, and a Calgary row in the picker would invite
    comparing an estimate against a schedule from another system."""

    response = client.get("/api/scenarios/routes")
    assert response.status_code == 200
    payload = response.json()
    assert payload["city"] == "Edmonton"
    assert payload["dataset_checksum_matches"] is True
    assert {route["route_label"] for route in payload["routes"]} == {"042"}


def test_route_detail_and_unknown_key(client: TestClient):
    key = client.get("/api/scenarios/routes").json()["routes"][0]["key"]
    assert client.get(f"/api/scenarios/routes/{key}").status_code == 200
    assert client.get("/api/scenarios/routes/not-a-real-key").status_code == 404


def test_estimate_with_no_changes_returns_the_schedule(client: TestClient):
    key = client.get("/api/scenarios/routes").json()["routes"][0]["key"]
    payload = client.post("/api/scenarios/estimate", json={"key": key}).json()
    baseline = payload["baseline"]
    estimate = payload["estimate"]
    assert estimate["design_distance"] == 0.0
    assert estimate["commercial_speed_kmh"]["point"] == pytest.approx(
        baseline["scheduled_commercial_speed_kmh"], abs=0.05
    )
    assert payload["delta"]["speed_kmh"] == pytest.approx(0.0, abs=0.05)


def test_estimate_reports_uncertainty_and_limitations(client: TestClient):
    """The UI must not have to invent its caveats: the range, the confidence, the
    held-out numbers and the limitations all come from the response."""

    key = client.get("/api/scenarios/routes").json()["routes"][0]["key"]
    payload = client.post(
        "/api/scenarios/estimate", json={"key": key, "stop_count": 30}
    ).json()
    estimate = payload["estimate"]
    assert estimate["commercial_speed_kmh"]["low"] < estimate["commercial_speed_kmh"]["high"]
    assert estimate["runtime_minutes"]["low"] < estimate["runtime_minutes"]["high"]
    assert estimate["confidence"] in {"high", "moderate", "low"}
    assert estimate["confidence_reasons"]
    assert payload["model"]["limitations"]
    assert payload["model"]["quality_measured_on"] == "leave-one-city-out folds only"
    skill = payload["model"]["held_out_change_skill"]
    assert skill["sign_agreement_on_material_changes"] == 0.714
    # The quality claim must describe the operator that actually ships.
    assert skill["operator"].startswith("frozen")
    assert skill["by_design_distance"]


def test_removing_stops_raises_the_estimated_speed(client: TestClient):
    key = client.get("/api/scenarios/routes").json()["routes"][0]["key"]
    payload = client.post(
        "/api/scenarios/estimate", json={"key": key, "stop_count": 24}
    ).json()
    assert payload["delta"]["speed_kmh"] > 0
    assert payload["delta"]["runtime_minutes"] < 0


def test_frequency_change_moves_fleet_not_speed(client: TestClient):
    key = client.get("/api/scenarios/routes").json()["routes"][0]["key"]
    payload = client.post(
        "/api/scenarios/estimate", json={"key": key, "peak_headway_minutes": 7.5}
    ).json()
    assert payload["delta"]["speed_kmh"] == pytest.approx(0.0, abs=0.01)
    assert payload["estimate"]["fleet"]["headway_minutes"] == 7.5
    assert payload["delta"]["vehicles"] > 0


@pytest.mark.parametrize(
    "body",
    [
        {"one_way_length_km": 0},
        {"one_way_length_km": -5},
        {"stop_count": 1},
        {"stop_count": 100000},
        {"peak_headway_minutes": 0},
        {"peak_headway_minutes": -3},
        {"service_span_hours": 99},
        {"day_type": "funday"},
        {"recovery_fraction": 4},
        {"one_way_length_km": "long"},
    ],
)
def test_hostile_input_is_rejected_without_a_server_error(client: TestClient, body):
    """Every one of these must be a 4xx. A 500 here would mean a planner can
    crash the estimator by dragging a slider somewhere unusual."""

    key = client.get("/api/scenarios/routes").json()["routes"][0]["key"]
    response = client.post("/api/scenarios/estimate", json={"key": key, **body})
    assert 400 <= response.status_code < 500, response.text


def test_unknown_key_on_estimate_is_404(client: TestClient):
    response = client.post("/api/scenarios/estimate", json={"key": "nope|0|weekday"})
    assert response.status_code == 404


def test_model_card_is_served_from_the_artifact(client: TestClient):
    payload = client.get("/api/scenarios/model").json()
    assert payload["training_cities"] == ["Edmonton", "Calgary"]
    assert payload["dataset_checksum_matches"] is True


def test_checksum_mismatch_is_reported_not_hidden(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """A model trained on different bytes than the dataset now on disk is a real
    provenance failure; the surface stays up but must say so rather than serve
    estimates that silently reference the wrong data."""

    manifest = artifact_root / "datasets" / DEFAULT_DATASET / "manifest.json"
    manifest.write_text(json.dumps({"file_checksums": {"rows.jsonl": "CHANGED"}}), encoding="utf-8")
    import transitpulse_api.scenarios as scenarios

    scenarios.load_scenario_service.cache_clear()
    from transitpulse_api.main import app

    payload = TestClient(app).get("/api/scenarios/routes").json()
    assert payload["dataset_checksum_matches"] is False


def test_missing_artifact_is_a_clean_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A fresh checkout has no generated model. That is an unavailable surface,
    not a server fault, and the message must name what to build."""

    monkeypatch.setenv("TRANSITPULSE_ARTIFACT_ROOT", str(tmp_path / "empty"))
    import transitpulse_api.scenarios as scenarios

    scenarios.load_scenario_service.cache_clear()
    from transitpulse_api.main import app

    response = TestClient(app).get("/api/scenarios/routes")
    scenarios.load_scenario_service.cache_clear()
    assert response.status_code == 503
    assert "not found" in response.json()["detail"]
