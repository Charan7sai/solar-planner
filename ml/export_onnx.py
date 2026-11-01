"""Export a trained run to ONNX, quantize it to INT8, and benchmark CPU accuracy and latency.

Free hosting (Hugging Face Spaces CPU) has no GPU, so the deployed model must be small and fast
on CPU. Static INT8 quantization uses validation tiles for calibration.

Usage:
    python ml/export_onnx.py --run resnet34_unet
Writes ml/runs/<run>/onnx/{model_fp32,model_int8}.onnx and ml/runs/<run>/export.json
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from onnxruntime.quantization import (CalibrationDataReader, QuantFormat, QuantType, quantize_static)
from onnxruntime.quantization.shape_inference import quant_pre_process

from dataset import RoofDataset
from model import build_model

RUNS_DIR = Path(__file__).parent / "runs"


class TileReader(CalibrationDataReader):
    def __init__(self, dataset, indices):
        self.it = iter(dataset[i][0][None].numpy() for i in indices)

    def get_next(self):
        x = next(self.it, None)
        return None if x is None else {"image": x}


def session(path: Path, threads: int | None = None) -> ort.InferenceSession:
    opts = ort.SessionOptions()
    if threads:
        opts.intra_op_num_threads = threads
    return ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])


def iou(predict, dataset, indices, threshold) -> float:
    tp = fp = fn = 0
    for i in indices:
        x, y = dataset[i]
        prob = 1 / (1 + np.exp(-predict(x[None].numpy())[0, 0]))
        pred, truth = prob > threshold, y[0].numpy() > 0.5
        tp += np.sum(pred & truth)
        fp += np.sum(pred & ~truth)
        fn += np.sum(~pred & truth)
    return round(float(tp / max(tp + fp + fn, 1)), 4)


def latency_ms(sess, size: int, runs: int = 20) -> float:
    x = np.random.rand(1, 3, size, size).astype(np.float32)
    sess.run(None, {"image": x})  # warm-up
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        sess.run(None, {"image": x})
        times.append((time.perf_counter() - t) * 1000)
    return round(float(np.median(times)), 1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run", default="resnet34_unet")
    p.add_argument("--calib-tiles", type=int, default=256)
    p.add_argument("--eval-tiles", type=int, default=1000)
    args = p.parse_args()

    run_dir = RUNS_DIR / args.run
    out = run_dir / "onnx"
    out.mkdir(exist_ok=True)
    fp32, prep, int8 = out / "model_fp32.onnx", out / "model_prep.onnx", out / "model_int8.onnx"

    # Export the checkpoint and threshold that evaluate.py selected on validation
    eval_path = run_dir / "evaluation.json"
    selection = json.loads(eval_path.read_text()) if eval_path.exists() else {}
    threshold = selection.get("threshold", 0.5)
    reference = torch.load(run_dir / "checkpoints" / "best.pt", map_location="cpu", weights_only=False)
    ckpt = torch.load(run_dir / "checkpoints" / f"{selection.get('checkpoint', 'best')}.pt",
                      map_location="cpu", weights_only=False)
    arch, base = ckpt.get("arch", reference.get("arch", "unet")), ckpt.get("base", reference["base"])
    model = build_model(arch, base, pretrained=False).eval()
    model.load_state_dict(ckpt["model"])

    # 1. Export; height/width are dynamic so the app can segment larger areas than one tile
    torch.onnx.export(
        model, torch.randn(1, 3, 256, 256), fp32, input_names=["image"], output_names=["logits"],
        dynamic_axes={"image": {0: "batch", 2: "height", 3: "width"}, "logits": {0: "batch", 2: "height", 3: "width"}},
        opset_version=17, dynamo=False,
    )

    val = RoofDataset("validation")
    rng = np.random.default_rng(0)
    order = rng.permutation(len(val))
    calib_idx, eval_idx = order[: args.calib_tiles], order[args.calib_tiles: args.calib_tiles + args.eval_tiles]

    # 2. Check the ONNX graph reproduces PyTorch
    s32 = session(fp32)
    x = torch.stack([val[i][0] for i in eval_idx[:8]])
    with torch.no_grad():
        max_diff = float(np.abs(model(x).numpy() - s32.run(None, {"image": x.numpy()})[0]).max())

    # 3. Static INT8 quantization (per-channel weights, QDQ format), calibrated on validation tiles
    quant_pre_process(str(fp32), str(prep), skip_symbolic_shape=True)  # dynamic H/W defeats symbolic inference
    quantize_static(str(prep), str(int8), TileReader(val, calib_idx), quant_format=QuantFormat.QDQ,
                    per_channel=True, weight_type=QuantType.QInt8, activation_type=QuantType.QUInt8)
    prep.unlink()
    s8 = session(int8)

    # 4. Accuracy on validation tiles not used for calibration
    with torch.no_grad():
        torch_iou = iou(lambda a: model(torch.from_numpy(a)).numpy(), val, eval_idx, threshold)
    report = {
        "threshold": threshold,
        "onnx_max_abs_diff_vs_torch": max_diff,
        "val_iou": {
            "pytorch_fp32": torch_iou,
            "onnx_fp32": iou(lambda a: s32.run(None, {"image": a})[0], val, eval_idx, threshold),
            "onnx_int8": iou(lambda a: s8.run(None, {"image": a})[0], val, eval_idx, threshold),
        },
        "eval_tiles": len(eval_idx),
        "size_mb": {"fp32": round(fp32.stat().st_size / 1e6, 1), "int8": round(int8.stat().st_size / 1e6, 1)},
        "cpu_latency_ms": {},
    }

    # 5. CPU latency: one 256px tile and a 512px area (2x2 tiles, roughly one city block)
    for name, path in [("fp32", fp32), ("int8", int8)]:
        for threads in (1, 2):
            sess = session(path, threads)
            for size in (256, 512):
                report["cpu_latency_ms"][f"{name}_{threads}thread_{size}px"] = latency_ms(sess, size)

    (run_dir / "export.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
