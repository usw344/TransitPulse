"""Read-only, integrity-checked projection of local experimental artifacts."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any


REQUIRED_ARTIFACTS = {
    "model_dataset": Path("datasets/m3-route004-20260907-10-causal-v5/manifest.json"),
    "baselines": Path("experiments/m4-route004-baselines-causal-v7/metrics.json"),
    "deep_model": Path("experiments/m5-route004-mlp-causal-v6/metrics.json"),
    "contiguous_span_replay": Path("experiments/m6-route004-contiguous-causal-v5/result.json"),
    "span_diagnostic": Path("experiments/m7-route004-contiguous-causal-v6/metrics.json"),
    "terminal_coverage_audit": Path("experiments/m7-route004-terminal-coverage-v1.json"),
}


def default_artifact_root() -> Path:
    override = os.getenv("TRANSITPULSE_ARTIFACT_ROOT")
    return Path(override).resolve() if override else Path(__file__).resolve().parents[3] / "artifacts"


def read_artifacts(artifact_root: Path) -> tuple[dict[str, Any], list[str]]:
    loaded: dict[str, Any] = {}
    missing: list[str] = []
    root = artifact_root.resolve()
    for name, relative_path in REQUIRED_ARTIFACTS.items():
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            missing.append(str(relative_path)); continue
        try:
            loaded[name] = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            missing.append(str(relative_path))
    return loaded, missing


def integrity_failure(root: Path, artifacts: dict[str, Any]) -> str | None:
    rows_path = root / "datasets/m3-route004-20260907-10-causal-v5/rows.jsonl"
    if not rows_path.is_file(): return "M3 rows.jsonl is missing."
    rows_hash = sha256(rows_path.read_bytes()).hexdigest()
    if artifacts["model_dataset"].get("rows_sha256") != rows_hash: return "M3 rows checksum does not match its manifest."
    for name in ("baselines", "deep_model"):
        if artifacts[name].get("source_rows_sha256") != rows_hash: return f"{name} does not match the declared M3 rows checksum."
    if artifacts["contiguous_span_replay"].get("source_model_rows_sha256") != rows_hash: return "contiguous span replay does not match the declared M3 rows checksum."
    if artifacts["span_diagnostic"].get("source_model_rows_sha256") != rows_hash: return "span diagnostic does not match the declared M3 rows checksum."
    if artifacts["span_diagnostic"].get("full_route_terminal_validation", {}).get("status") != "NOT REACHED": return "M7 diagnostic is not correctly marked as lacking terminal validation."
    if artifacts["span_diagnostic"].get("headway_gap_bunching_validation", {}).get("anchored_stop_arrival_diagnostic", {}).get("status") != "PARTIAL_ANCHORED_DIAGNOSTIC_ONLY": return "M7 headway diagnostic is missing or not correctly labeled as partial."
    terminal_labels = root / "datasets/m7-route004-terminal-inclusive-v1/labels.jsonl"
    if not terminal_labels.is_file(): return "terminal-inclusive audit labels are missing."
    if artifacts["terminal_coverage_audit"].get("source_labels_sha256") != sha256(terminal_labels.read_bytes()).hexdigest(): return "terminal coverage audit does not match its terminal-inclusive labels."
    if artifacts["terminal_coverage_audit"].get("full_route_terminal_validation", {}).get("status") != "NOT REACHED": return "terminal coverage audit is not correctly marked as lacking full-route evidence."
    return None


def model_lab_summary(artifact_root: Path | None = None) -> dict[str, Any]:
    """Return evidence only; never generate a forecast or operational decision."""
    root = artifact_root or default_artifact_root()
    artifacts, missing = read_artifacts(root)
    if missing:
        return {"availability": "unavailable", "message": "The local experimental evidence bundle is incomplete; Model Lab metrics are withheld.", "missing_artifacts": missing}
    failure = integrity_failure(root, artifacts)
    if failure:
        return {"availability": "unavailable", "message": "Artifact integrity check failed; Model Lab metrics are withheld.", "integrity_failure": failure}
    dataset, baseline, deep = artifacts["model_dataset"], artifacts["baselines"], artifacts["deep_model"]
    selected_baseline = baseline["model_selection"]["selected_model"]
    diagnostic, replay, terminal_coverage = artifacts["span_diagnostic"], artifacts["contiguous_span_replay"], artifacts["terminal_coverage_audit"]
    return {
        "availability": "experimental",
        "message": "Artifact-backed experimental evidence only. It is not an operational recommendation, live forecast, passenger-impact estimate, or validated route simulation.",
        "provenance": {"dataset_rows": dataset["row_count"], "service_days": dataset["service_days"], "dataset_sha256": dataset["rows_sha256"], "source_labels_sha256": dataset["source_labels_sha256"]},
        "selected_model": {"name": f"Train-only {selected_baseline.replace('_', ' ')} baseline", "reason": "It won the validation-only baseline selection; the deep MLP was not selected.", "test_metrics": baseline["results"]["test"][selected_baseline], "deep_model_test_metrics": deep["results"]["test"], "deep_model_status": deep["deployment_status"]},
        "twin_diagnostic": {"status": diagnostic["validation_status"], "eligible_spans": diagnostic["eligible_contiguous_spans"], "eligible_labels": diagnostic["eligible_labels"], "segment_travel_time": diagnostic["segment_travel_time"], "contiguous_span_travel_time": diagnostic["contiguous_span_travel_time"], "interval": diagnostic["empirical_train_residual_interval"], "full_route_terminal_validation": diagnostic["full_route_terminal_validation"], "partial_headway_diagnostic": diagnostic["headway_gap_bunching_validation"]["anchored_stop_arrival_diagnostic"], "terminal_coverage_audit": {"status": terminal_coverage["full_route_terminal_validation"]["status"], "label_runs": terminal_coverage["label_run_count"], "complete_runs": terminal_coverage["complete_terminal_to_terminal_run_count"], "reason": terminal_coverage["full_route_terminal_validation"]["reason"]}},
        "historical_span_replay": {"selected_run_id": replay["selected_run_id"], "from_stop_sequence": replay["from_stop_sequence"], "to_stop_sequence": replay["to_stop_sequence"], "validation": replay["validation"]},
        "constraints": ["No complete terminal-to-terminal route simulation, dwell/layover model, route/block-level headway-gap-bunching validation, or multi-day twin validation exists.", "The displayed headway result is only a partial, observed-start-anchored stop-arrival diagnostic; it is not a schedule or service-regularity forecast.", "No passenger demand, crowding, fare, accessibility, weather, traffic, fleet/block, capacity, or operator-work-rule inputs are present.", "Only four sparse, discontinuous service days and one held-out day are represented.", "No optimizer or operational recommendation is enabled."],
    }
