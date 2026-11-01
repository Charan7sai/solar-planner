"""Train a roof-segmentation model and evaluate it on held-out locations.

Usage:
    python ml/train.py --epochs 15 --run unet_baseline
    python ml/train.py --arch resnet34_unet --lr 5e-4 --epochs 15 --run resnet34_unet
Outputs (ml/runs/<run>/): metrics.json, history.csv, predictions.png, checkpoints/best.pt
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import matplotlib
import numpy as np
import torch
from torch.utils.data import DataLoader
from tqdm import tqdm

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from dataset import RoofDataset, to_display  # noqa: E402
from metrics import SegMetrics, bce_dice_loss  # noqa: E402
from model import ARCHS, build_model, count_params, describe  # noqa: E402

RUNS_DIR = Path(__file__).parent / "runs"


@torch.no_grad()
def evaluate(model, loader, device, amp) -> dict:
    model.eval()
    metrics, losses = SegMetrics(), []
    for x, y in loader:
        x, y = x.to(device, non_blocking=True), y.to(device, non_blocking=True)
        with torch.autocast(device.type, enabled=amp):
            logits = model(x)
        losses.append(bce_dice_loss(logits.float(), y).item())
        metrics.update(logits.float(), y)
    return {"loss": float(np.mean(losses)), **metrics.compute()}


@torch.no_grad()
def save_predictions(model, dataset, device, path: Path, n: int = 6, seed: int = 0) -> None:
    """Image / ground truth / prediction grid for tiles that actually contain buildings."""
    model.eval()
    rng = np.random.default_rng(seed)
    candidates = [i for i in rng.permutation(len(dataset))[:500] if dataset.masks[i].mean() > 0.08][:n]
    fig, axes = plt.subplots(len(candidates), 3, figsize=(9, 3 * len(candidates)))
    for row, idx in zip(axes, candidates):
        x, y = dataset[idx]
        prob = torch.sigmoid(model(x[None].to(device)))[0, 0].cpu().numpy()
        for ax, im, title in zip(row, [to_display(x), y[0].numpy(), prob > 0.5], ["Aerial tile", "OSM label", "Prediction"]):
            ax.imshow(im, cmap=None if im.ndim == 3 else "gray")
            ax.set_title(title, fontsize=10)
            ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=80)
    plt.close(fig)


def write_history(path: Path, history: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=history[0].keys())
        writer.writeheader()
        writer.writerows(history)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run", default="unet_baseline")
    p.add_argument("--arch", choices=ARCHS, default="unet")
    p.add_argument("--epochs", type=int, default=15)
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--base", type=int, default=32, help="U-Net width of the first level")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int, default=None, help="Use only N training tiles (smoke tests)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--resume", action="store_true", help="Continue from checkpoints/last.pt")
    args = p.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    amp = device.type == "cuda"
    run_dir = RUNS_DIR / args.run
    (run_dir / "checkpoints").mkdir(parents=True, exist_ok=True)

    train_ds = RoofDataset("train", augment=True, limit=args.limit)
    val_ds = RoofDataset("validation", limit=args.limit)
    test_ds = RoofDataset("test", limit=args.limit)
    loader_kw = dict(batch_size=args.batch_size, num_workers=args.workers, pin_memory=amp,
                     persistent_workers=args.workers > 0)
    train_dl = DataLoader(train_ds, shuffle=True, drop_last=True, **loader_kw)
    val_dl = DataLoader(val_ds, **loader_kw)

    model = build_model(args.arch, args.base).to(device)
    print(f"device={device} params={count_params(model) / 1e6:.2f}M "
          f"train={len(train_ds)} val={len(val_ds)} test={len(test_ds)}")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=args.epochs * len(train_dl))
    scaler = torch.amp.GradScaler(enabled=amp)

    history, best_iou, first_epoch, prior_seconds = [], -1.0, 1, 0.0
    last_path = run_dir / "checkpoints" / "last.pt"
    if args.resume and last_path.exists():
        state = torch.load(last_path, map_location=device, weights_only=False)
        model.load_state_dict(state["model"])
        opt.load_state_dict(state["opt"])
        sched.load_state_dict(state["sched"])
        scaler.load_state_dict(state["scaler"])
        history, best_iou, prior_seconds = state["history"], state["best_iou"], state["seconds"]
        first_epoch = state["epoch"] + 1
        print(f"resumed after epoch {state['epoch']} (best val IoU {best_iou:.4f})", flush=True)

    start = time.time() - prior_seconds
    for epoch in range(first_epoch, args.epochs + 1):
        model.train()
        train_losses = []
        for x, y in tqdm(train_dl, desc=f"epoch {epoch}/{args.epochs}", leave=False, disable=None):
            x = x.to(device, non_blocking=True)
            y = y.to(device, non_blocking=True)
            with torch.autocast(device.type, enabled=amp):
                logits = model(x)
            loss = bce_dice_loss(logits.float(), y)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            sched.step()
            train_losses.append(loss.item())

        val = evaluate(model, val_dl, device, amp)
        row = {"epoch": epoch, "train_loss": float(np.mean(train_losses)), **{f"val_{k}": v for k, v in val.items()}}
        history.append(row)
        print(f"epoch {epoch:2d}  train_loss {row['train_loss']:.4f}  val_loss {val['loss']:.4f}  "
              f"val_iou {val['iou']:.4f}  val_f1 {val['f1']:.4f}", flush=True)

        if val["iou"] > best_iou:
            best_iou = val["iou"]
            torch.save({"model": model.state_dict(), "arch": args.arch, "base": args.base, "epoch": epoch}, run_dir / "checkpoints" / "best.pt")

        # Persist everything each epoch so an interrupted run can continue with --resume
        write_history(run_dir / "history.csv", history)
        torch.save({
            "model": model.state_dict(), "opt": opt.state_dict(), "sched": sched.state_dict(),
            "scaler": scaler.state_dict(), "arch": args.arch, "base": args.base, "epoch": epoch,
            "history": history, "best_iou": best_iou,
            "seconds": time.time() - start,
        }, last_path)

    # Final evaluation of the best checkpoint on unseen test locations
    model.load_state_dict(torch.load(run_dir / "checkpoints" / "best.pt", map_location=device)["model"])
    test = evaluate(model, DataLoader(test_ds, **{**loader_kw, "persistent_workers": False}), device, amp)
    val_best = max(history, key=lambda r: r["val_iou"])

    summary = {
        "model": describe(args.arch, args.base),
        "params_millions": round(count_params(model) / 1e6, 2),
        "train_tiles": len(train_ds),
        "val_tiles": len(val_ds),
        "test_tiles": len(test_ds),
        "epochs": args.epochs,
        "best_epoch": val_best["epoch"],
        "val": {k[4:]: round(v, 4) for k, v in val_best.items() if k.startswith("val_")},
        "test": {k: round(v, 4) for k, v in test.items()},
        "train_minutes": round((time.time() - start) / 60, 1),
        "device": torch.cuda.get_device_name(0) if amp else "cpu",
    }
    (run_dir / "metrics.json").write_text(json.dumps(summary, indent=2))
    save_predictions(model, test_ds, device, run_dir / "predictions.png")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
