"""T011: First small-scale training run (minimal, dependency-free).

Runs the validated training pipeline (src.distiller.train) on the 115 real
samples for 1 epoch, batch_size=1, and persists the trained weights as the
training artifact under models/.

IMPORTANT (honest scope): no LoRA framework is installed (torch/peft/mlx
absent) and installing new dependencies is forbidden. This run validates the
training LOOP end-to-end with the dependency-free regressor. Real LoRA SFT on
qwen3.5:9b requires an approved framework install (e.g. mlx_lm) and is a
Dispatcher decision.
"""
import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.distiller.data import create_dataloader, load_data
from src.distiller.train import SimpleRegressor, train_one_epoch, validate

DATA = os.path.join(PROJECT_ROOT, "data", "real_samples.jsonl")
MODEL_ARTIFACT = os.path.join(PROJECT_ROOT, "models", "qxen_minimal_v0.1.json")
REPORT = os.path.join(PROJECT_ROOT, "logs", "T011_training_report.md")

CONFIG = {
    "batch_size": 1,
    "epochs": 1,
    "learning_rate": 0.01,
    "shuffle": True,
    "seed": 42,
    "data_path": DATA,
    "n_features": 4,
    "framework": "dependency-free SimpleRegressor (no torch/peft/mlx installed; install forbidden)",
}


def main():
    t0 = time.time()
    samples = load_data(CONFIG["data_path"])
    dl = create_dataloader(
        samples,
        batch_size=CONFIG["batch_size"],
        shuffle=CONFIG["shuffle"],
        seed=CONFIG["seed"],
    )
    model = SimpleRegressor(n_features=CONFIG["n_features"], seed=CONFIG["seed"])
    loss = train_one_epoch(model, dl, learning_rate=CONFIG["learning_rate"])
    metrics = validate(model, dl)
    elapsed = time.time() - t0

    artifact = {
        "artifact": "QXEN minimal pipeline v0.1",
        "framework": CONFIG["framework"],
        "config": {k: v for k, v in CONFIG.items() if k != "framework"},
        "weights": {"w": model.w, "b": model.b},
        "final_loss": loss,
        "validation": metrics,
        "n_samples": len(samples),
        "trained_at": "2026-08-12",
        "elapsed_sec": round(elapsed, 2),
    }
    os.makedirs(os.path.dirname(MODEL_ARTIFACT), exist_ok=True)
    with open(MODEL_ARTIFACT, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, ensure_ascii=False, indent=2)

    md = [
        "# T011 Training Report",
        "",
        f"- Date: 2026-08-12",
        f"- Data: `data/real_samples.jsonl` ({len(samples)} samples)",
        f"- Config: epochs=1, batch_size=1, lr=0.01, shuffle=True, seed=42",
        f"- Framework: dependency-free SimpleRegressor (no LoRA framework installed; install forbidden)",
        f"- Final loss: {loss:.6f}",
        f"- Validation: MSE={metrics['mse']:.6f}, MAE={metrics['mae']:.6f}, n={metrics['n_samples']}",
        f"- Elapsed: {elapsed:.2f}s",
        f"- Artifact: `models/qxen_minimal_v0.1.json`",
        "",
        "## Scope note",
        "",
        "This validates the training loop end-to-end (data -> dataloader ->",
        "optimizer step -> metrics -> persisted artifact) with zero new",
        "dependencies. Real LoRA SFT on qwen3.5:9b requires an approved",
        "framework install (e.g. mlx_lm / torch+peft) and is a Dispatcher",
        "decision. Evaluation of the trained model is out of scope for T011.",
    ]
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")

    print(f"trained on {len(samples)} samples, 1 epoch, batch_size=1")
    print(f"final_loss={loss:.6f}")
    print(f"validation={ {k: round(v, 6) if isinstance(v, float) else v for k, v in metrics.items()} }")
    print(f"elapsed={elapsed:.2f}s")
    print(f"artifact={MODEL_ARTIFACT}")
    print(f"report={REPORT}")


if __name__ == "__main__":
    main()
