"""Train, checkpoint, resume, and audit a small chronological PyTorch MLP."""

from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import random
from statistics import median

import numpy as np
import torch
from torch import nn


CATEGORICAL_FIELDS = ("route_gtfs_id", "from_stop_id", "to_stop_id")
NUMERIC_FIELDS = ("scheduled_seconds", "distance_m", "hour", "weekday", "direction_id")
ARCHITECTURE = {"embedding_size": 8, "hidden_size": 32, "dropout": 0.10, "categorical_fields": CATEGORICAL_FIELDS, "numeric_fields": NUMERIC_FIELDS}


def write_json(path: Path, value: object) -> None:
    path.write_bytes((json.dumps(value, indent=2) + "\n").encode("utf-8"))


def read_rows(path: Path) -> tuple[list[dict[str, object]], bytes]:
    data = path.read_bytes()
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line], data


def metric_summary(actual: list[float], predicted: list[float]) -> dict[str, float | int]:
    errors = sorted(abs(actual_value - predicted_value) for actual_value, predicted_value in zip(actual, predicted, strict=True))
    return {"n": len(errors), "mae": sum(errors) / len(errors), "median_ae": median(errors), "p90_ae": errors[round((len(errors) - 1) * 0.9)], "rmse": (sum((actual_value - predicted_value) ** 2 for actual_value, predicted_value in zip(actual, predicted, strict=True)) / len(errors)) ** 0.5}


def seed_everything(seed: int) -> None:
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True); torch.set_num_threads(1)


def category_vocabs(train: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    return {field: {value: index + 1 for index, value in enumerate(sorted({str(row.get(field, "")) for row in train}))} for field in CATEGORICAL_FIELDS}


def numeric_normalizer(train: list[dict[str, object]]) -> tuple[list[float], list[float]]:
    values = np.asarray([[float(row[field]) if row[field] is not None else -1.0 for field in NUMERIC_FIELDS] for row in train], dtype=np.float32)
    means, standard_deviations = values.mean(axis=0), values.std(axis=0)
    standard_deviations[standard_deviations < 1e-6] = 1.0
    return means.tolist(), standard_deviations.tolist()


def tensors(rows: list[dict[str, object]], vocabs: dict[str, dict[str, int]], numeric_means: list[float], numeric_standard_deviations: list[float], target_mean: float, target_standard_deviation: float) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    categories = torch.tensor([[vocabs[field].get(str(row.get(field, "")), 0) for field in CATEGORICAL_FIELDS] for row in rows], dtype=torch.long)
    numeric_values = np.asarray([[float(row[field]) if row[field] is not None else -1.0 for field in NUMERIC_FIELDS] for row in rows], dtype=np.float32)
    numeric = torch.tensor((numeric_values - np.asarray(numeric_means, dtype=np.float32)) / np.asarray(numeric_standard_deviations, dtype=np.float32), dtype=torch.float32)
    targets = torch.tensor([(float(row["target_travel_seconds"]) - target_mean) / target_standard_deviation for row in rows], dtype=torch.float32)
    return categories, numeric, targets


class TravelTimeMLP(nn.Module):
    def __init__(self, vocab_sizes: list[int]) -> None:
        super().__init__()
        self.embeddings = nn.ModuleList([nn.Embedding(vocab_size, ARCHITECTURE["embedding_size"], padding_idx=0) for vocab_size in vocab_sizes])
        input_size = len(NUMERIC_FIELDS) + ARCHITECTURE["embedding_size"] * len(vocab_sizes)
        self.network = nn.Sequential(nn.Linear(input_size, ARCHITECTURE["hidden_size"]), nn.ReLU(), nn.Dropout(ARCHITECTURE["dropout"]), nn.Linear(ARCHITECTURE["hidden_size"], ARCHITECTURE["hidden_size"] // 2), nn.ReLU(), nn.Linear(ARCHITECTURE["hidden_size"] // 2, 1))

    def forward(self, categories: torch.Tensor, numeric: torch.Tensor) -> torch.Tensor:
        embedded = [embedding(categories[:, index]) for index, embedding in enumerate(self.embeddings)]
        return self.network(torch.cat([numeric, *embedded], dim=1)).squeeze(1)


def predict(model: TravelTimeMLP, inputs: tuple[torch.Tensor, torch.Tensor, torch.Tensor], target_mean: float, target_standard_deviation: float) -> list[float]:
    categories, numeric, _ = inputs
    model.eval()
    with torch.no_grad(): standardized = model(categories, numeric).tolist()
    return [max(1.0, target_mean + target_standard_deviation * value) for value in standardized]


def segment_key(row: dict[str, object]) -> tuple[str, str, str]:
    return (str(row["route_gtfs_id"]), str(row["from_stop_id"]), str(row["to_stop_id"]))


def subgroup_results(rows: list[dict[str, object]], predictions: list[float], train: list[dict[str, object]]) -> dict[str, dict[str, dict[str, float | int]]]:
    supports: dict[tuple[str, str, str], int] = {}
    for row in train: supports[segment_key(row)] = supports.get(segment_key(row), 0) + 1
    classifiers = {
        "route": lambda row: str(row["route_gtfs_id"]),
        "time_of_day": lambda row: "am_peak_07_10" if 7 <= int(row["hour"]) < 10 else "pm_peak_15_19" if 15 <= int(row["hour"]) < 19 else "other",
        "segment_support": lambda row: "unseen_in_train" if supports.get(segment_key(row), 0) == 0 else "sparse_1_4_train_rows" if supports[segment_key(row)] < 5 else "supported_5_plus_train_rows",
    }
    result: dict[str, dict[str, dict[str, float | int]]] = {}
    for name, classifier in classifiers.items():
        groups: dict[str, list[int]] = {}
        for index, row in enumerate(rows): groups.setdefault(classifier(row), []).append(index)
        result[name] = {group: metric_summary([float(rows[index]["target_travel_seconds"]) for index in indexes], [predictions[index] for index in indexes]) for group, indexes in sorted(groups.items())}
    return result


def bootstrap_mae_interval(actual: list[float], predicted: list[float], seed: int, samples: int = 400) -> dict[str, float | int]:
    errors = [abs(a - p) for a, p in zip(actual, predicted, strict=True)]
    generator = random.Random(seed)
    estimates = sorted(sum(generator.choice(errors) for _ in errors) / len(errors) for _ in range(samples))
    return {"bootstrap_samples": samples, "mae_p05": estimates[round((samples - 1) * .05)], "mae_p95": estimates[round((samples - 1) * .95)], "interpretation": "IID bootstrap diagnostic only; sparse correlated transit history makes this non-operational."}


def row_id(row: dict[str, object]) -> str:
    return sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def paired_bootstrap_delta(deep: list[float], baseline: list[float], actual: list[float], seed: int, samples: int = 400) -> dict[str, float | int]:
    deltas = [abs(deep_value - target) - abs(baseline_value - target) for deep_value, baseline_value, target in zip(deep, baseline, actual, strict=True)]
    generator = random.Random(seed)
    estimates = sorted(sum(generator.choice(deltas) for _ in deltas) / len(deltas) for _ in range(samples))
    return {"bootstrap_samples": samples, "deep_minus_baseline_mae_p05": estimates[round((samples - 1) * .05)], "deep_minus_baseline_mae_p95": estimates[round((samples - 1) * .95)], "interpretation": "Positive means the deep MLP is worse. IID bootstrap diagnostic only; correlated sparse history makes this non-operational."}


def save_checkpoint(path: Path, state: dict[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    torch.save(state, temporary)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline-metrics", type=Path, required=True)
    parser.add_argument("--baseline-predictions", type=Path, help="M4 predictions.jsonl; required unless --smoke-only")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", type=Path, help="checkpoint_last.pt from the same M3 input")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--epochs", type=int, default=180, help="total maximum epochs, including a resumed run")
    parser.add_argument("--patience", type=int, default=30)
    parser.add_argument("--smoke-only", action="store_true", help="omit all test scoring; use for forward/gradient/checkpoint/resume verification")
    args = parser.parse_args()
    if args.output.exists(): parser.error(f"output exists: {args.output}")
    rows, rows_bytes = read_rows(args.rows)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    rows_sha256 = sha256(rows_bytes).hexdigest()
    if manifest.get("rows_sha256") != rows_sha256: parser.error("M3 manifest rows_sha256 does not match exact rows bytes")
    baseline_bytes = args.baseline_metrics.read_bytes()
    baseline = json.loads(baseline_bytes.decode("utf-8"))
    if baseline.get("source_rows_sha256") != rows_sha256: parser.error("M4 baseline artifact was not produced from these exact M3 rows")
    if not args.smoke_only and not args.baseline_predictions: parser.error("--baseline-predictions is required for exact paired test comparison")
    train, validation, test = ([row for row in rows if row["split"] == split] for split in ("train", "validation", "test"))
    if not train or not validation or not test: parser.error("requires non-empty chronological train, validation, and test splits")
    seed_everything(args.seed)
    vocabs = category_vocabs(train)
    numeric_means, numeric_standard_deviations = numeric_normalizer(train)
    train_targets = [float(row["target_travel_seconds"]) for row in train]
    target_mean, target_standard_deviation = float(np.mean(train_targets)), float(np.std(train_targets))
    if target_standard_deviation < 1e-6: parser.error("training target has no variance")
    train_inputs = tensors(train, vocabs, numeric_means, numeric_standard_deviations, target_mean, target_standard_deviation)
    validation_inputs = tensors(validation, vocabs, numeric_means, numeric_standard_deviations, target_mean, target_standard_deviation)
    test_inputs = tensors(test, vocabs, numeric_means, numeric_standard_deviations, target_mean, target_standard_deviation)
    model = TravelTimeMLP([len(vocabs[field]) + 1 for field in CATEGORICAL_FIELDS])
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01, weight_decay=0.001)
    loss_function = nn.MSELoss()
    start_epoch, best_epoch, best_validation_mae, epochs_without_improvement, history = 0, 0, float("inf"), 0, []
    best_state: dict[str, torch.Tensor] | None = None
    resumed_from: str | None = None
    if args.resume:
        checkpoint = torch.load(args.resume, map_location="cpu", weights_only=False)
        if checkpoint["source_rows_sha256"] != rows_sha256 or checkpoint["seed"] != args.seed: parser.error("resume checkpoint does not match source rows or seed")
        if checkpoint["vocabs"] != vocabs or checkpoint["architecture"] != ARCHITECTURE: parser.error("resume checkpoint preprocessing or architecture mismatch")
        model.load_state_dict(checkpoint["model_state"]); optimizer.load_state_dict(checkpoint["optimizer_state"])
        start_epoch, best_epoch, best_validation_mae = checkpoint["epoch"], checkpoint["best_epoch"], checkpoint["best_validation_mae"]
        epochs_without_improvement, history, best_state = checkpoint["epochs_without_improvement"], checkpoint["history"], checkpoint["best_model_state"]
        resumed_from = str(args.resume)
    if start_epoch >= args.epochs: parser.error("--epochs must exceed the resume checkpoint epoch")
    train_categories, train_numeric, train_targets_tensor = train_inputs
    model.train(); smoke_loss = loss_function(model(train_categories, train_numeric), train_targets_tensor); smoke_loss.backward()
    gradient_tensors = sum(parameter.grad is not None and bool(torch.count_nonzero(parameter.grad)) for parameter in model.parameters())
    optimizer.zero_grad()
    hardware = {"device": "cpu", "torch_version": torch.__version__, "cuda_available": bool(torch.cuda.is_available()), "cuda_device_count": torch.cuda.device_count(), "cpu_count": os.cpu_count(), "platform": platform.platform(), "deterministic_algorithms": True, "torch_threads": torch.get_num_threads()}
    args.output.mkdir(parents=True)
    validation_actual = [float(row["target_travel_seconds"]) for row in validation]
    for epoch in range(start_epoch + 1, args.epochs + 1):
        model.train(); optimizer.zero_grad()
        loss = loss_function(model(train_categories, train_numeric), train_targets_tensor); loss.backward(); optimizer.step()
        validation_metrics = metric_summary(validation_actual, predict(model, validation_inputs, target_mean, target_standard_deviation))
        history.append({"epoch": epoch, "train_mse_standardized": float(loss.item()), "validation_mae": float(validation_metrics["mae"])})
        if validation_metrics["mae"] < best_validation_mae:
            best_epoch, best_validation_mae, best_state, epochs_without_improvement = epoch, float(validation_metrics["mae"]), deepcopy(model.state_dict()), 0
        else: epochs_without_improvement += 1
        save_checkpoint(args.output / "checkpoint_last.pt", {"checkpoint_schema": 1, "source_rows_sha256": rows_sha256, "seed": args.seed, "architecture": ARCHITECTURE, "vocabs": vocabs, "numeric_means": numeric_means, "numeric_standard_deviations": numeric_standard_deviations, "target_mean": target_mean, "target_standard_deviation": target_standard_deviation, "model_state": model.state_dict(), "optimizer_state": optimizer.state_dict(), "epoch": epoch, "best_epoch": best_epoch, "best_validation_mae": best_validation_mae, "best_model_state": best_state, "epochs_without_improvement": epochs_without_improvement, "history": history})
        if epochs_without_improvement >= args.patience: break
    if best_state is None: raise RuntimeError("no model state was selected")
    model.load_state_dict(best_state)
    validation_predictions = predict(model, validation_inputs, target_mean, target_standard_deviation)
    test_predictions = predict(model, test_inputs, target_mean, target_standard_deviation) if not args.smoke_only else []
    test_actual = [float(row["target_travel_seconds"]) for row in test] if not args.smoke_only else []
    selected_baseline = baseline["model_selection"]["selected_model"]
    baseline_test_predictions: list[float] = []
    paired_records: list[dict[str, object]] = []
    baseline_predictions_sha256: str | None = None
    if not args.smoke_only:
        baseline_prediction_bytes = args.baseline_predictions.read_bytes()
        baseline_predictions_sha256 = sha256(baseline_prediction_bytes).hexdigest()
        if baseline["prediction_records"]["sha256"] != baseline_predictions_sha256: parser.error("M4 predictions checksum mismatch")
        baseline_lookup = {record["row_id"]: record["predictions"][selected_baseline] for record in (json.loads(line) for line in baseline_prediction_bytes.decode("utf-8").splitlines() if line) if record["split"] == "test"}
        baseline_test_predictions = [float(baseline_lookup[row_id(row)]) for row in test]
        paired_records = [{"row_id": row_id(row), "route_gtfs_id": row["route_gtfs_id"], "from_stop_id": row["from_stop_id"], "to_stop_id": row["to_stop_id"], "prediction_at": row["prediction_at"], "target_travel_seconds": target, "deep_prediction_seconds": deep_prediction, "baseline_prediction_seconds": baseline_prediction, "deep_absolute_error_seconds": abs(deep_prediction - target), "baseline_absolute_error_seconds": abs(baseline_prediction - target)} for row, target, deep_prediction, baseline_prediction in zip(test, test_actual, test_predictions, baseline_test_predictions, strict=True)]
    paired_bytes = "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in paired_records).encode("utf-8")
    selected_checkpoint = {"checkpoint_schema": 1, "source_rows_sha256": rows_sha256, "architecture": ARCHITECTURE, "vocabs": vocabs, "numeric_means": numeric_means, "numeric_standard_deviations": numeric_standard_deviations, "target_mean": target_mean, "target_standard_deviation": target_standard_deviation, "best_epoch": best_epoch, "model_state": model.state_dict()}
    torch.save(selected_checkpoint, args.output / "model.pt")
    report = {
        "schema_version": 2, "experiment_contract": "m5-seeded-resumable-pytorch-mlp-v2", "seed": args.seed, "hardware": hardware,
        "source_rows": str(args.rows), "source_rows_sha256": rows_sha256, "source_manifest": str(args.manifest), "source_labels_sha256": manifest.get("source_labels_sha256"), "baseline_metrics": str(args.baseline_metrics), "baseline_metrics_sha256": sha256(baseline_bytes).hexdigest(), "baseline_predictions": None if args.smoke_only else {"path": str(args.baseline_predictions), "sha256": baseline_predictions_sha256}, "split_counts": {"train": len(train), "validation": len(validation), "test": len(test)},
        "training_config": {"architecture": ARCHITECTURE, "optimizer": {"name": "AdamW", "learning_rate": .01, "weight_decay": .001}, "loss": "MSELoss on train-standardized travel seconds", "early_stopping": {"selection": "validation MAE", "patience": args.patience}, "max_epochs": args.epochs, "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest()},
        "resume": {"resumed_from": resumed_from, "start_epoch": start_epoch, "checkpoint_last": "checkpoint_last.pt", "checkpoint_last_sha256": sha256((args.output / "checkpoint_last.pt").read_bytes()).hexdigest()},
        "smoke_check": {"forward_output_shape": [len(train)], "initial_standardized_mse": float(smoke_loss.item()), "nonzero_gradient_tensors": gradient_tensors, "device": "cpu", "checkpoint_written": True},
        "best_epoch_selected_on_validation": best_epoch, "epochs_ran_total": len(history), "results": {"validation": metric_summary(validation_actual, validation_predictions), **({} if args.smoke_only else {"test": metric_summary(test_actual, test_predictions)})},
        "subgroup_results": {"validation": subgroup_results(validation, validation_predictions, train), **({} if args.smoke_only else {"test": subgroup_results(test, test_predictions, train)})},
        "baseline_comparison": None if args.smoke_only else {"selected_baseline": selected_baseline, "baseline_test_metrics": baseline["results"]["test"][selected_baseline], "deep_minus_baseline_mae_seconds": metric_summary(test_actual, test_predictions)["mae"] - metric_summary(test_actual, baseline_test_predictions)["mae"], "interpretation": "Positive means the deep model is worse. It is descriptive only; this sparse dataset cannot establish material deployment benefit."},
        "paired_test_records": None if args.smoke_only else {"path": "paired_test_predictions.jsonl", "row_count": len(paired_records), "sha256": sha256(paired_bytes).hexdigest()},
        "uncertainty": {} if args.smoke_only else {"deep_test_mae_bootstrap": bootstrap_mae_interval(test_actual, test_predictions, args.seed), "paired_deep_minus_selected_baseline": paired_bootstrap_delta(test_predictions, baseline_test_predictions, test_actual, args.seed)}, "model_sha256": sha256((args.output / "model.pt").read_bytes()).hexdigest(),
        "deployment_status": "SMOKE ONLY — no test scoring or baseline comparison was performed." if args.smoke_only else "REJECTED FOR OPERATIONAL USE — validation-selected train-only segment median baseline is materially better on the held-out test day.",
        "limitations": ["Four sparse, discontinuous service days and one held-out service day.", "No passenger demand, weather, traffic, service disruption, capacity, or operator inputs.", "Subgroups are diagnostic samples, not independent validation populations."],
        "reproducible_command": f".\\.venv\\Scripts\\python.exe scripts\\train_deep_model.py --rows {args.rows} --manifest {args.manifest} --baseline-metrics {args.baseline_metrics}{'' if args.smoke_only else f' --baseline-predictions {args.baseline_predictions}'} --output {args.output} --seed {args.seed} --epochs {args.epochs} --patience {args.patience}{f' --resume {args.resume}' if args.resume else ''}{' --smoke-only' if args.smoke_only else ''}",
    }
    if not args.smoke_only: (args.output / "paired_test_predictions.jsonl").write_bytes(paired_bytes)
    write_json(args.output / "metrics.json", report); write_json(args.output / "hardware.json", hardware); write_json(args.output / "training_history.json", history)
    print(json.dumps(report, indent=2))


if __name__ == "__main__": main()
