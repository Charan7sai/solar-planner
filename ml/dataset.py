"""Roof segmentation dataset backed by memory-mapped numpy caches.

Parquet shards store PNG/TIFF bytes; decoding them every epoch is slow, so each split is
decoded once into uint8 .npy files that DataLoader workers share through the OS page cache.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import torch
from PIL import Image
from torch.utils.data import Dataset
from tqdm import tqdm

DATA_DIR = Path(__file__).parent / "data"
TILE = 256
# ImageNet statistics; also reasonable for aerial RGB and lets CP3 swap in pretrained encoders
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _decode(cell: dict, mode: str) -> np.ndarray:
    img = Image.open(io.BytesIO(cell["bytes"])).convert(mode)
    if img.size != (TILE, TILE):
        img = img.resize((TILE, TILE), Image.NEAREST if mode == "L" else Image.BILINEAR)
    return np.asarray(img)


def build_cache(split: str) -> tuple[Path, Path]:
    """Decode every downloaded shard of a split into images.npy / masks.npy (once)."""
    out = DATA_DIR / "cache" / split
    img_path, mask_path = out / "images.npy", out / "masks.npy"
    if img_path.exists() and mask_path.exists():
        return img_path, mask_path

    shards = sorted((DATA_DIR / "data").glob(f"{split}-*.parquet"))
    if not shards:
        raise FileNotFoundError(f"No {split} shards found. Run ml/prepare_data.py first.")
    n = sum(pq.ParquetFile(s).metadata.num_rows for s in shards)

    out.mkdir(parents=True, exist_ok=True)
    images = np.lib.format.open_memmap(img_path.with_suffix(".tmp.npy"), "w+", np.uint8, (n, TILE, TILE, 3))
    masks = np.lib.format.open_memmap(mask_path.with_suffix(".tmp.npy"), "w+", np.uint8, (n, TILE, TILE))

    i = 0
    with tqdm(total=n, desc=f"caching {split}") as bar:
        for shard in shards:
            for batch in pq.ParquetFile(shard).iter_batches(batch_size=256, columns=["image", "mask"]):
                for img, mask in zip(batch.column("image").to_pylist(), batch.column("mask").to_pylist()):
                    images[i] = _decode(img, "RGB")
                    masks[i] = _decode(mask, "L") > 0
                    i += 1
                bar.update(batch.num_rows)

    images.flush(), masks.flush()
    del images, masks
    img_path.with_suffix(".tmp.npy").replace(img_path)
    mask_path.with_suffix(".tmp.npy").replace(mask_path)
    return img_path, mask_path


class RoofDataset(Dataset):
    def __init__(self, split: str, augment: bool = False, limit: int | None = None):
        self.img_path, self.mask_path = build_cache(split)
        self._images = self._masks = None
        self.augment = augment
        total = len(self.images)
        self.n = min(limit, total) if limit else total

    # Memmaps are opened lazily: on Windows, DataLoader workers are spawned and pickling an
    # open memmap would copy the whole array into every worker.
    @property
    def images(self):
        if self._images is None:
            self._images = np.load(self.img_path, mmap_mode="r")
        return self._images

    @property
    def masks(self):
        if self._masks is None:
            self._masks = np.load(self.mask_path, mmap_mode="r")
        return self._masks

    def __getstate__(self):
        return {**self.__dict__, "_images": None, "_masks": None}

    def __len__(self):
        return self.n

    def __getitem__(self, idx):
        img = self.images[idx].astype(np.float32) / 255.0
        mask = self.masks[idx].astype(np.float32)

        if self.augment:
            # Aerial tiles have no canonical orientation: all 8 dihedral transforms are valid
            k = np.random.randint(4)
            img, mask = np.rot90(img, k), np.rot90(mask, k)
            if np.random.rand() < 0.5:
                img, mask = img[:, ::-1], mask[:, ::-1]
            # Mild brightness/contrast jitter for different sensors and sun angles
            img = np.clip((img - 0.5) * np.random.uniform(0.8, 1.2) + 0.5 + np.random.uniform(-0.1, 0.1), 0, 1)

        img = (img - MEAN) / STD
        return (
            torch.from_numpy(np.ascontiguousarray(img.transpose(2, 0, 1))),
            torch.from_numpy(np.ascontiguousarray(mask))[None],
        )


def to_display(img: torch.Tensor) -> np.ndarray:
    """Undo normalisation for plotting: CHW tensor -> HWC uint8."""
    arr = img.cpu().numpy().transpose(1, 2, 0) * STD + MEAN
    return (np.clip(arr, 0, 1) * 255).astype(np.uint8)
