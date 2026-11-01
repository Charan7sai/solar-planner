"""Evaluate drop-a-pin roof extraction at the building (instance) level.

For held-out buildings, simulate a user clicking the middle of the roof, run the deployed
INT8 model + post-processing, and compare the returned polygon with that building's label.
Pixel IoU (evaluate.py) rewards masks; this measures what users actually get: one roof outline
and its area.

The post-processing method is chosen on validation buildings; only the winner is scored on test.

Usage (from the repo root):
    python ml/evaluate_detection.py --buildings 300
Writes ml/runs/detection_eval.json
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from app import roof  # noqa: E402

CACHE = ROOT / "ml" / "data" / "cache"
MIN_BUILDING_PX = 400  # ~35 m2; skip sheds and label fragments


def sample_buildings(split: str, n: int, seed: int = 0):
    """(tile index, building mask, click point) for random whole buildings in a split."""
    masks = np.load(CACHE / split / "masks.npy", mmap_mode="r")
    rng = np.random.default_rng(seed)
    out = []
    for i in rng.permutation(len(masks)):
        count, labels, stats, _ = cv2.connectedComponentsWithStats(np.ascontiguousarray(masks[i]))
        for k in range(1, count):
            x, y, w, h, area = stats[k]
            touches_edge = x == 0 or y == 0 or x + w == masks.shape[2] or y + h == masks.shape[1]
            if area >= MIN_BUILDING_PX and not touches_edge:
                building = (labels == k).astype(np.uint8)
                # Click the point deepest inside the roof, like a person aiming for the middle
                dist = cv2.distanceTransform(building, cv2.DIST_L2, 5)
                cy, cx = np.unravel_index(np.argmax(dist), dist.shape)
                out.append((int(i), building.astype(bool), (int(cx), int(cy))))
                break  # at most one building per tile keeps samples spread across locations
        if len(out) == n:
            return out
    return out


def region_from_polygon(result, shape):
    region = np.zeros(shape, np.uint8)
    if result is not None:
        cv2.fillPoly(region, [np.array(result["polygon_px"], np.int32)], 1)
    return region.astype(bool)


METHODS = {
    "components@0.2": (0.2, None),
    "components@0.35": (0.35, None),
    "components@0.5": (0.5, None),
    "components@0.65": (0.65, None),
    "watershed@0.2/0.5": (0.2, 0.5),
    "watershed@0.35/0.65": (0.35, 0.65),
}


def score(method, samples, probs) -> dict:
    threshold, core = METHODS[method]
    ious, area_errors, found = [], [], 0
    for i, truth, pin in samples:
        result = roof.extract_roof(probs[i], pin, threshold, core)
        region = region_from_polygon(result, truth.shape)
        ious.append((region & truth).sum() / (region | truth).sum())
        if result is not None:
            found += 1
            area_errors.append(abs(result["area_px"] - truth.sum()) / truth.sum())
    ious = np.array(ious)
    return {
        "found_rate": round(found / len(samples), 3),
        "mean_iou": round(float(ious.mean()), 3),
        "median_iou": round(float(np.median(ious)), 3),
        "share_iou_over_0.5": round(float((ious > 0.5).mean()), 3),
        "median_area_error": round(float(np.median(area_errors)), 3) if area_errors else None,
    }


def run_split(split: str, n: int, methods) -> tuple[int, dict]:
    images = np.load(CACHE / split / "images.npy", mmap_mode="r")
    samples = sample_buildings(split, n)
    probs = {i: roof.predict(np.ascontiguousarray(images[i])) for i, _, _ in samples}
    results = {}
    for m in methods:
        results[m] = score(m, samples, probs)
        print(split, m, results[m], flush=True)
    return len(samples), results


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--buildings", type=int, default=300)
    args = p.parse_args()

    n_val, val = run_split("validation", args.buildings, METHODS)
    best = max(val, key=lambda m: val[m]["mean_iou"])
    n_test, test = run_split("test", args.buildings, [best, "components@0.2"])
    report = {
        "selected_on_validation": best,
        "threshold": METHODS[best][0],
        "core_threshold": METHODS[best][1],
        "validation": {"buildings": n_val, "methods": val},
        "test": {"buildings": n_test, "methods": test},
    }
    (ROOT / "ml" / "runs" / "detection_eval.json").write_text(json.dumps(report, indent=2))
    print("selected:", best)


if __name__ == "__main__":
    main()
