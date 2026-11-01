"""Detailed evaluation of a trained run: validation-tuned threshold and per-country test IoU.

The threshold is chosen on the validation split only, then applied unchanged to test.

Usage:
    python ml/evaluate.py --run unet_baseline
Writes ml/runs/<run>/evaluation.json and curves.png
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import torch
from torch.utils.data import DataLoader

from dataset import DATA_DIR, RoofDataset
from model import build_model

RUNS_DIR = Path(__file__).parent / "runs"
THRESHOLDS = np.round(np.arange(0.05, 0.75, 0.05), 2)


def countries(split: str) -> list[str]:
    """Country of every cached tile, in cache order (shards sorted by name, rows in order)."""
    shards = sorted((DATA_DIR / "data").glob(f"{split}-*.parquet"))
    return [c for s in shards for c in pq.read_table(s, columns=["country"]).column("country").to_pylist()]


@torch.no_grad()
def confusion_by_group(model, dataset, groups, device, thresholds):
    """tp/fp/fn per (group, threshold), accumulated over the split."""
    stats = defaultdict(lambda: np.zeros((len(thresholds), 3), dtype=np.int64))
    loader = DataLoader(dataset, batch_size=32, num_workers=4)
    t = torch.tensor(thresholds, device=device).view(-1, 1, 1, 1, 1)
    i = 0
    for x, y in loader:
        with torch.autocast(device.type, enabled=device.type == "cuda"):
            prob = torch.sigmoid(model(x.to(device)).float())
        truth = y.to(device)[None] > 0.5          # 1,B,1,H,W
        pred = prob[None] > t                      # T,B,1,H,W
        tp = (pred & truth).sum(dim=(2, 3, 4)).cpu().numpy()   # T,B
        fp = (pred & ~truth).sum(dim=(2, 3, 4)).cpu().numpy()
        fn = (~pred & truth).sum(dim=(2, 3, 4)).cpu().numpy()
        for b in range(x.shape[0]):
            for key in ("all", groups[i + b]):
                stats[key] += np.stack([tp[:, b], fp[:, b], fn[:, b]], axis=1)
        i += x.shape[0]
    return stats


def scores(row) -> dict:
    tp, fp, fn = (int(v) for v in row)
    p, r = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    return {"iou": round(tp / max(tp + fp + fn, 1), 4), "f1": round(2 * p * r / max(p + r, 1e-9), 4),
            "precision": round(p, 4), "recall": round(r, 4)}


def plot_history(run_dir: Path) -> None:
    import csv

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with open(run_dir / "history.csv") as f:
        rows = list(csv.DictReader(f))
    epochs = [int(r["epoch"]) for r in rows]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.5))
    ax1.plot(epochs, [float(r["train_loss"]) for r in rows], label="train")
    ax1.plot(epochs, [float(r["val_loss"]) for r in rows], label="validation")
    ax1.set(title="BCE + Dice loss", xlabel="epoch")
    ax1.legend()
    ax2.plot(epochs, [float(r["val_iou"]) for r in rows], color="tab:green")
    ax2.set(title="Validation IoU (threshold 0.5)", xlabel="epoch")
    for ax in (ax1, ax2):
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(run_dir / "curves.png", dpi=90)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run", default="unet_baseline")
    p.add_argument("--checkpoints", default="best,last",
                   help="Candidates compared on validation; the winner alone is scored on test")
    args = p.parse_args()

    run_dir = RUNS_DIR / args.run
    plot_history(run_dir)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    val_ds, val_groups = RoofDataset("validation"), countries("validation")
    default = int(np.where(THRESHOLDS == 0.5)[0][0])

    # Model selection (checkpoint + threshold) uses validation only
    # Older last.pt files didn't record the architecture; best.pt always does
    reference = torch.load(run_dir / "checkpoints" / "best.pt", map_location="cpu", weights_only=False)
    candidates = {}
    for name in args.checkpoints.split(","):
        path = run_dir / "checkpoints" / f"{name}.pt"
        if not path.exists():
            continue
        ckpt = torch.load(path, map_location=device, weights_only=False)
        arch, base = ckpt.get("arch", reference.get("arch", "unet")), ckpt.get("base", reference["base"])
        model = build_model(arch, base, pretrained=False).to(device).eval()
        model.load_state_dict(ckpt["model"])
        val = confusion_by_group(model, val_ds, val_groups, device, THRESHOLDS)
        val_iou = [scores(r)["iou"] for r in val["all"]]
        candidates[name] = {"model": model, "epoch": ckpt["epoch"], "val_iou": val_iou}
        print(f"{name} (epoch {ckpt['epoch']}) validation IoU by threshold:",
              dict(zip(THRESHOLDS.tolist(), val_iou)))

    chosen = max(candidates, key=lambda n: max(candidates[n]["val_iou"]))
    model, val_iou = candidates[chosen]["model"], candidates[chosen]["val_iou"]
    best = int(np.argmax(val_iou))

    test_groups = countries("test")
    test = confusion_by_group(model, RoofDataset("test"), test_groups, device, THRESHOLDS[[default, best]])
    tiles = {c: test_groups.count(c) for c in set(test_groups)}

    report = {
        "checkpoint": chosen,
        "epoch": candidates[chosen]["epoch"],
        "candidates_val_iou": {n: {"epoch": c["epoch"], "best_val_iou": max(c["val_iou"])} for n, c in candidates.items()},
        "threshold": float(THRESHOLDS[best]),
        "val_iou_at_0.5": val_iou[default],
        "val_iou_at_threshold": val_iou[best],
        "test_at_0.5": scores(test["all"][0]),
        "test_at_threshold": scores(test["all"][1]),
        "test_by_country": {
            c: {"tiles": tiles[c], **scores(test[c][1])}
            for c in sorted(tiles, key=lambda c: -tiles[c])
        },
    }
    (run_dir / "evaluation.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
