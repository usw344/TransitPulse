import json
from hashlib import sha256

from transitpulse_api.model_lab import model_lab_summary


def test_model_lab_withholds_metrics_when_evidence_is_missing(tmp_path) -> None:
    summary = model_lab_summary(tmp_path)
    assert summary["availability"] == "unavailable"
    assert "twin_diagnostic" not in summary


def test_model_lab_requires_matching_artifact_provenance(tmp_path) -> None:
    rows = b'{}\n'
    terminal_labels = b'{}\n'
    rows_path = tmp_path / "datasets/m3-route004-20260907-10-causal-v5/rows.jsonl"
    rows_path.parent.mkdir(parents=True, exist_ok=True)
    rows_path.write_bytes(rows)
    terminal_labels_path = tmp_path / "datasets/m7-route004-terminal-inclusive-v1/labels.jsonl"
    terminal_labels_path.parent.mkdir(parents=True, exist_ok=True)
    terminal_labels_path.write_bytes(terminal_labels)
    rows_hash = sha256(rows).hexdigest()
    terminal_labels_hash = sha256(terminal_labels).hexdigest()
    files = {
        "datasets/m3-route004-20260907-10-causal-v5/manifest.json": {"row_count": 1, "service_days": ["2026-09-07"], "rows_sha256": rows_hash, "source_labels_sha256": "labels"},
        "experiments/m4-route004-baselines-causal-v7/metrics.json": {"source_rows_sha256": rows_hash, "model_selection": {"selected_model": "segment_median"}, "results": {"test": {"segment_median": {"mae": 1}}}},
        "experiments/m5-route004-mlp-causal-v6/metrics.json": {"source_rows_sha256": rows_hash, "results": {"test": {"mae": 2}}, "deployment_status": "REJECTED"},
        "experiments/m6-route004-contiguous-causal-v5/result.json": {"source_model_rows_sha256": rows_hash, "selected_run_id": "run", "from_stop_sequence": 1, "to_stop_sequence": 2, "validation": {"segment_travel_time": {"mae": 1}}},
        "experiments/m7-route004-contiguous-causal-v6/metrics.json": {"source_model_rows_sha256": rows_hash, "validation_status": "BLOCKED", "eligible_contiguous_spans": 1, "eligible_labels": 2, "segment_travel_time": {"mae": 1}, "contiguous_span_travel_time": {"mae": 2}, "empirical_train_residual_interval": {"held_out_coverage": .5}, "full_route_terminal_validation": {"status": "NOT REACHED"}, "headway_gap_bunching_validation": {"anchored_stop_arrival_diagnostic": {"status": "PARTIAL_ANCHORED_DIAGNOSTIC_ONLY", "eligible_adjacent_pairs": 1}}},
        "experiments/m7-route004-terminal-coverage-v1.json": {"source_labels_sha256": terminal_labels_hash, "label_run_count": 1, "complete_terminal_to_terminal_run_count": 0, "full_route_terminal_validation": {"status": "NOT REACHED", "reason": "no strict complete run"}},
    }
    for relative, payload in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    summary = model_lab_summary(tmp_path)
    assert summary["availability"] == "experimental"
    assert summary["selected_model"]["name"] == "Train-only segment median baseline"
    assert summary["twin_diagnostic"]["partial_headway_diagnostic"]["status"] == "PARTIAL_ANCHORED_DIAGNOSTIC_ONLY"
    (tmp_path / "datasets/m3-route004-20260907-10-causal-v5/rows.jsonl").write_bytes(b"changed\n")
    assert model_lab_summary(tmp_path)["availability"] == "unavailable"
