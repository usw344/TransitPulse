"""Milestone K — does a small neural network beat the linear model here?

Run:

    .venv\\Scripts\\python.exe scripts\\train_cross_city_deep_model.py

The expected outcome is that it will not.  The dataset is ~4,900 rows of 16 tabular
features, the winning baseline is a regularized linear model, and the hard part
of this problem is *cross-city transfer*, where extra capacity mostly buys extra
opportunity to memorize the training cities' idiosyncrasies.  The experiment is
still worth running because "we tried and it lost" is a result, and because the
comparison has to be made on the same leave-one-city-out protocol as the
baselines rather than on a friendlier split.

A rejection of deep learning is an acceptable outcome of this gate.  What is not
acceptable is keeping the network as the served estimator because it is more
impressive, or quoting it on a protocol the baselines never saw.

Architecture: a small residual MLP over standardized features — two residual
blocks of width 64, dropout, trained with AdamW, Huber loss and early stopping
on an inner validation split carved from the *training cities only*.  Small on
purpose: capacity beyond this has nothing to learn from 4,900 rows.

Escalation follows SMALL -> VERIFIED -> FULL: a smoke run proves the forward
pass, loss, gradients and checkpointing work before any full fold runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import torch
from torch import nn

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from transitpulse_ml.cross_city_features import CohortRule, build_matrix  # noqa: E402

SEED = 20260912
DATASET_ROOT = REPO_ROOT / "artifacts" / "datasets"
EXPERIMENT_ROOT = REPO_ROOT / "artifacts" / "experiments"


class ResidualBlock(nn.Module):
    def __init__(self, width: int, dropout: float) -> None:
        super().__init__()
        self.body = nn.Sequential(
            nn.Linear(width, width),
            nn.LayerNorm(width),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(width, width),
        )
        self.norm = nn.LayerNorm(width)
        self.activation = nn.GELU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.activation(self.norm(x + self.body(x)))


class SpeedMLP(nn.Module):
    def __init__(self, n_features: int, width: int = 64, blocks: int = 2, dropout: float = 0.15) -> None:
        super().__init__()
        self.stem = nn.Sequential(nn.Linear(n_features, width), nn.LayerNorm(width), nn.GELU())
        self.blocks = nn.Sequential(*[ResidualBlock(width, dropout) for _ in range(blocks)])
        self.head = nn.Linear(width, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.head(self.blocks(self.stem(x))).squeeze(-1)


def standardize(train: np.ndarray, *others: np.ndarray) -> tuple[np.ndarray, ...]:
    """Impute with the training median and scale by training statistics.

    Every statistic comes from the training cities only.  Fitting the scaler on
    all rows would leak the held-out city's feature distribution into training,
    which is a quieter version of the leak this whole protocol exists to avoid.
    """

    median = np.nanmedian(train, axis=0)
    median = np.where(np.isfinite(median), median, 0.0)

    def fill(matrix: np.ndarray) -> np.ndarray:
        filled = matrix.copy()
        indices = np.where(~np.isfinite(filled))
        filled[indices] = np.take(median, indices[1])
        return filled

    filled_train = fill(train)
    mean = filled_train.mean(axis=0)
    std = filled_train.std(axis=0)
    std = np.where(std < 1e-8, 1.0, std)
    return tuple((fill(m) - mean) / std for m in (train, *others))


def train_one_fold(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_test: np.ndarray,
    *,
    max_epochs: int,
    patience: int,
    checkpoint: Path | None,
    seed: int = SEED,
) -> tuple[np.ndarray, dict[str, Any]]:
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)

    order = rng.permutation(len(x_train))
    split = int(len(order) * 0.85)
    fit_idx, val_idx = order[:split], order[split:]

    device = torch.device("cpu")
    to = lambda a: torch.tensor(a, dtype=torch.float32, device=device)  # noqa: E731
    x_fit, y_fit = to(x_train[fit_idx]), to(y_train[fit_idx])
    x_val, y_val = to(x_train[val_idx]), to(y_train[val_idx])
    x_eval = to(x_test)

    model = SpeedMLP(x_train.shape[1]).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-2)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max_epochs)
    loss_fn = nn.HuberLoss(delta=3.0)

    best_val, best_state, best_epoch, waited = float("inf"), None, 0, 0
    history: list[dict[str, float]] = []
    batch_size = 128

    for epoch in range(max_epochs):
        model.train()
        shuffled = torch.randperm(len(x_fit))
        total = 0.0
        for start in range(0, len(x_fit), batch_size):
            batch = shuffled[start : start + batch_size]
            optimizer.zero_grad()
            loss = loss_fn(model(x_fit[batch]), y_fit[batch])
            loss.backward()
            optimizer.step()
            total += float(loss) * len(batch)
        scheduler.step()

        model.eval()
        with torch.no_grad():
            val_mae = float(torch.mean(torch.abs(model(x_val) - y_val)))
        history.append({"epoch": epoch, "train_loss": total / len(x_fit), "val_mae_kmh": val_mae})

        if val_mae < best_val - 1e-4:
            best_val, best_epoch, waited = val_mae, epoch, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            waited += 1
            if waited >= patience:
                break

    if best_state is not None:
        model.load_state_dict(best_state)
        if checkpoint is not None:
            torch.save({"state_dict": best_state, "epoch": best_epoch, "val_mae": best_val}, checkpoint)

    model.eval()
    with torch.no_grad():
        predicted = model(x_eval).cpu().numpy().astype(np.float64)
    return predicted, {
        "best_epoch": best_epoch,
        "best_inner_val_mae_kmh": best_val,
        "epochs_run": len(history),
        "history_tail": history[-5:],
    }


def smoke_test(x: np.ndarray, y: np.ndarray, tmp: Path) -> dict[str, Any]:
    """Prove forward pass, loss, gradients and checkpointing before the real run."""

    torch.manual_seed(SEED)
    model = SpeedMLP(x.shape[1])
    xs = torch.tensor(x[:32], dtype=torch.float32)
    ys = torch.tensor(y[:32], dtype=torch.float32)
    out = model(xs)
    loss = nn.HuberLoss(delta=3.0)(out, ys)
    loss.backward()
    grad_norm = float(
        torch.sqrt(sum((p.grad**2).sum() for p in model.parameters() if p.grad is not None)).detach()
    )
    checkpoint = tmp / "smoke.pt"
    torch.save(model.state_dict(), checkpoint)
    reloaded = SpeedMLP(x.shape[1])
    reloaded.load_state_dict(torch.load(checkpoint, weights_only=True))
    # Both must be in eval mode: dropout is stochastic, so comparing two
    # training-mode passes would report a checkpoint mismatch that is really
    # just two different dropout masks.
    model.eval()
    reloaded.eval()
    with torch.no_grad():
        identical = bool(torch.allclose(model(xs), reloaded(xs)))
    model.train()
    checkpoint.unlink()
    return {
        "output_shape": list(out.shape),
        "loss": float(loss.detach()),
        "grad_norm": grad_norm,
        "gradients_flow": grad_norm > 0,
        "checkpoint_roundtrip_identical": identical,
        "parameters": int(sum(p.numel() for p in model.parameters())),
    }


def metrics(actual: np.ndarray, predicted: np.ndarray, length_km: np.ndarray) -> dict[str, float]:
    error = predicted - actual
    absolute = np.abs(error)
    safe = np.clip(predicted, 1e-6, None)
    runtime_error = np.abs(60.0 * length_km / safe - 60.0 * length_km / actual)
    return {
        "n": int(actual.size),
        "mae_kmh": float(np.mean(absolute)),
        "rmse_kmh": float(np.sqrt(np.mean(error**2))),
        "median_ae_kmh": float(np.median(absolute)),
        "p90_ae_kmh": float(np.quantile(absolute, 0.90)),
        "bias_kmh": float(np.mean(error)),
        "runtime_mae_minutes": float(np.mean(runtime_error)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="cross_city_routes_v3")
    parser.add_argument("--experiment", default="cross_city_deep_v3")
    parser.add_argument("--baselines", default="cross_city_baselines_v3")
    parser.add_argument("--max-epochs", type=int, default=400)
    parser.add_argument("--patience", type=int, default=40)
    args = parser.parse_args()

    rows_path = DATASET_ROOT / args.dataset / "rows.jsonl"
    all_rows = [json.loads(line) for line in rows_path.open(encoding="utf-8")]
    cohort = CohortRule()
    rows: Sequence[Mapping[str, Any]] = [r for r in all_rows if cohort.admits(r)]

    out_dir = EXPERIMENT_ROOT / args.experiment
    if out_dir.exists():
        print(f"{out_dir} exists — experiments are immutable", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True)

    x_all, y_all, names = build_matrix(rows)
    (x_scaled,) = standardize(x_all)
    smoke = smoke_test(x_scaled, y_all, out_dir)
    print("SMOKE:", json.dumps(smoke))
    if not smoke["gradients_flow"] or not smoke["checkpoint_roundtrip_identical"]:
        print("smoke test failed; refusing to run full training", file=sys.stderr)
        return 1

    cities = sorted({row["city"] for row in rows})
    per_city: dict[str, Any] = {}
    fold_logs: dict[str, Any] = {}
    pooled_actual, pooled_predicted, pooled_length = [], [], []

    for held_out in cities:
        train_rows = [r for r in rows if r["city"] != held_out]
        test_rows = [r for r in rows if r["city"] == held_out]
        x_train_raw, y_train, _ = build_matrix(train_rows)
        x_test_raw, y_test, _ = build_matrix(test_rows)
        x_train, x_test = standardize(x_train_raw, x_test_raw)
        length = np.array([r["one_way_length_km"] for r in test_rows], dtype=np.float64)

        print(f"fold {held_out:14s} train={len(train_rows):5d} test={len(test_rows):5d}", flush=True)
        predicted, log = train_one_fold(
            x_train, y_train, x_test,
            max_epochs=args.max_epochs, patience=args.patience,
            checkpoint=out_dir / f"checkpoint_{held_out.lower()}.pt",
        )
        per_city[held_out] = metrics(y_test, predicted, length)
        fold_logs[held_out] = log
        pooled_actual.append(y_test)
        pooled_predicted.append(predicted)
        pooled_length.append(length)
        print(f"   MAE={per_city[held_out]['mae_kmh']:.3f} km/h "
              f"(best epoch {log['best_epoch']}, inner val {log['best_inner_val_mae_kmh']:.3f})", flush=True)

    actual = np.concatenate(pooled_actual)
    predicted = np.concatenate(pooled_predicted)
    lengths = np.concatenate(pooled_length)
    overall = metrics(actual, predicted, lengths)

    baseline_path = EXPERIMENT_ROOT / args.baselines / "report.json"
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    comparison = None
    if baseline:
        best = baseline["ranking_by_mae_kmh"][0]
        delta = overall["mae_kmh"] - best["mae_kmh"]
        comparison = {
            "strongest_baseline": best["model"],
            "strongest_baseline_mae_kmh": best["mae_kmh"],
            "deep_mae_kmh": overall["mae_kmh"],
            "deep_minus_baseline_kmh": delta,
            "deep_wins": delta < 0,
            # A gain smaller than this is not worth a neural dependency in a
            # planning tool whose realistic error bar is several km/h wide.
            "materiality_threshold_kmh": 0.15,
            "material": abs(delta) >= 0.15,
        }

    report = {
        "experiment": args.experiment,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "dataset": args.dataset,
        "dataset_manifest_checksum": json.loads(
            (DATASET_ROOT / args.dataset / "manifest.json").read_text()
        )["file_checksums"]["rows.jsonl"],
        "protocol": "leave-one-city-out, identical folds and cohort to the baselines",
        "architecture": "residual MLP, width 64, 2 blocks, dropout 0.15, Huber, AdamW, early stopping",
        "features": names,
        "smoke_test": smoke,
        "overall": overall,
        "per_city": per_city,
        "fold_logs": fold_logs,
        "comparison_to_baselines": comparison,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\nDEEP MODEL leave-one-city-out MAE = {overall['mae_kmh']:.3f} km/h")
    if comparison:
        verdict = "WINS" if comparison["deep_wins"] else "LOSES"
        material = "material" if comparison["material"] else "not material"
        print(f"strongest baseline ({comparison['strongest_baseline']}) = "
              f"{comparison['strongest_baseline_mae_kmh']:.3f} km/h")
        print(f"deep model {verdict} by {abs(comparison['deep_minus_baseline_kmh']):.3f} km/h ({material})")
    for city, values in sorted(per_city.items()):
        print(f"  {city:14s} MAE={values['mae_kmh']:6.3f}  bias={values['bias_kmh']:+6.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
