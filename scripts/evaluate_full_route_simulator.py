"""Evaluate only audit-confirmed complete held-out routes without stitching.

The resulting artifact is a conditional full-route timing diagnostic, not an
automatic Gate 5 pass.  It requires later dwell/departure, layover, route/block
headway-gap-bunching, multi-day, and acceptance-contract evidence.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
from transitpulse_ml.full_route_evaluator import evaluate_complete_test_runs  # noqa: E402


def read_jsonl(path: Path) -> tuple[list[dict[str, object]], bytes]:
    data = path.read_bytes()
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line], data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--model-rows", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--baseline-metrics", type=Path, required=True)
    parser.add_argument("--terminal-audit", type=Path, required=True)
    parser.add_argument("--acceptance-contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output exists: {args.output}")

    labels, label_bytes = read_jsonl(args.labels)
    model_rows, model_rows_bytes = read_jsonl(args.model_rows)
    model_manifest_bytes = args.model_manifest.read_bytes()
    model_manifest = json.loads(model_manifest_bytes.decode("utf-8"))
    baseline_bytes = args.baseline_metrics.read_bytes()
    baseline = json.loads(baseline_bytes.decode("utf-8"))
    audit_bytes = args.terminal_audit.read_bytes()
    audit = json.loads(audit_bytes.decode("utf-8"))
    contract_bytes = args.acceptance_contract.read_bytes()

    labels_hash = sha256(label_bytes).hexdigest()
    rows_hash = sha256(model_rows_bytes).hexdigest()
    if model_manifest.get("source_labels_sha256") != labels_hash:
        parser.error("model dataset provenance does not match complete-route labels")
    if model_manifest.get("rows_sha256") != rows_hash:
        parser.error("model manifest row checksum does not match model rows")
    if baseline.get("source_rows_sha256") != rows_hash:
        parser.error("baseline provenance does not match model rows")
    if baseline.get("model_selection", {}).get("selected_model") != "segment_median":
        parser.error("full-route evaluator requires the exact M4-selected segment_median baseline")
    if audit.get("source_labels_sha256") != labels_hash:
        parser.error("terminal audit provenance does not match complete-route labels")
    if audit.get("full_route_terminal_validation", {}).get("status") != "REACHED":
        parser.error("terminal audit has no complete terminal-to-terminal evidence")

    evaluation = evaluate_complete_test_runs(
        labels=labels,
        model_rows=model_rows,
        terminal_coverage_audit=audit,
        random_seed=args.seed,
    )
    report = {
        "schema_version": 1,
        "simulator": "deterministic-strict-full-route-conditional-v1",
        "source_labels": str(args.labels),
        "source_labels_sha256": labels_hash,
        "source_model_rows": str(args.model_rows),
        "source_model_rows_sha256": rows_hash,
        "model_manifest_sha256": sha256(model_manifest_bytes).hexdigest(),
        "baseline_metrics_sha256": sha256(baseline_bytes).hexdigest(),
        "terminal_coverage_audit_sha256": sha256(audit_bytes).hexdigest(),
        "acceptance_contract": str(args.acceptance_contract),
        "acceptance_contract_sha256": sha256(contract_bytes).hexdigest(),
        "evaluator_script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "evaluator_source_sha256": sha256((REPO_ROOT / "apps" / "api" / "transitpulse_ml" / "full_route_evaluator.py").read_bytes()).hexdigest(),
        **evaluation,
        "reproducible_command": (
            f".\\.venv\\Scripts\\python.exe scripts\\evaluate_full_route_simulator.py "
            f"--labels {args.labels} --model-rows {args.model_rows} "
            f"--model-manifest {args.model_manifest} --baseline-metrics {args.baseline_metrics} "
            f"--terminal-audit {args.terminal_audit} --acceptance-contract {args.acceptance_contract} "
            f"--output {args.output} --seed {args.seed}"
        ),
    }
    args.output.mkdir(parents=True)
    (args.output / "metrics.json").write_bytes((json.dumps(report, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({key: report[key] for key in ("complete_test_runs", "complete_test_run_service_days", "conditional_route_travel_time", "validation_status")}, indent=2))


if __name__ == "__main__":
    main()
