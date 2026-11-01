"""Download the HOT Building Segmentation dataset shards used for training.

Dataset: hotosm/vhr-building-segmentation (CC-BY-4.0 imagery / ODbL labels)
256x256 OpenAerialMap tiles at zoom 19 with OpenStreetMap building masks.
Splits are made per mapping project, so test tiles come from unseen locations.

Usage:
    python ml/prepare_data.py --train-shards 4 --val-shards 2 --test-shards 2
"""
import argparse
from pathlib import Path

import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download

REPO = "hotosm/vhr-building-segmentation"
SHARD_COUNTS = {"train": 11, "validation": 2, "test": 2}
DATA_DIR = Path(__file__).parent / "data"


def download(split: str, n: int) -> list[Path]:
    total = SHARD_COUNTS[split]
    paths = []
    for i in range(min(n, total)):
        name = f"data/{split}-{i:05d}-of-{total:05d}.parquet"
        paths.append(Path(hf_hub_download(REPO, name, repo_type="dataset", local_dir=DATA_DIR)))
    return paths


def summarize(split: str, paths: list[Path]) -> None:
    table = pq.read_table(paths, columns=["country", "num_buildings"])
    countries = table.column("country").to_pylist()
    print(f"{split}: {table.num_rows} tiles from {len(set(countries))} countries")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-shards", type=int, default=4)
    parser.add_argument("--val-shards", type=int, default=2)
    parser.add_argument("--test-shards", type=int, default=2)
    args = parser.parse_args()

    for split, n in [("train", args.train_shards), ("validation", args.val_shards), ("test", args.test_shards)]:
        summarize(split, download(split, n))


if __name__ == "__main__":
    main()
