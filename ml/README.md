# Roof segmentation model

Goal: given a satellite tile of a neighbourhood, mark which pixels are rooftops. The planner can
then measure usable roof area from a single pin drop instead of asking users to trace the roof.

## Data

[HOT Building Segmentation](https://huggingface.co/datasets/hotosm/vhr-building-segmentation)
(Humanitarian OpenStreetMap Team; imagery CC-BY-4.0, labels ODbL): 256×256 aerial tiles at
zoom 19 (~0.3 m/pixel) from OpenAerialMap with expert-validated OpenStreetMap building masks.

- Zoom 19 matches the satellite map used by the app, so tiles can be fed to the model directly.
- Mostly informal and peri-urban settlements in the Global South, closer to Indian
  neighbourhoods than US/European benchmarks.
- **Splits are by mapping project**, so validation and test tiles come from locations the model
  never saw. Scores therefore measure generalisation to new places, not memorisation.

| Split | Tiles | Countries |
|---|---|---|
| Train (4 of 11 shards) | 21,052 | 15 (Myanmar, Mozambique, Mexico, Kenya, …) |
| Validation | 7,237 | 3 |
| Test | 7,236 | 6 (Eswatini, Peru, Japan, Mozambique, Tajikistan, Philippines) |

## CP2 baseline: U-Net from scratch

| | |
|---|---|
| Architecture | U-Net, 4 levels, base width 32, BatchNorm, 7.76 M parameters |
| Loss | BCE + soft Dice (counters the background/building imbalance) |
| Training | AdamW, OneCycle LR (max 1e-3), batch 16, mixed precision, 15 epochs |
| Augmentation | 8 dihedral flips/rotations, brightness/contrast jitter |
| Hardware | RTX 3050 Laptop GPU (4 GB), 106 min |

### Results

Pixel-level metrics, micro-averaged over each split. The best epoch (9) was selected by
validation IoU. The decision threshold was tuned on **validation only** and then applied
unchanged to test.

| Split | Threshold | IoU | F1 | Precision | Recall |
|---|---|---|---|---|---|
| Validation | 0.5 | 0.364 | 0.533 | 0.755 | 0.412 |
| Validation | 0.1 (tuned) | 0.454 | – | – | – |
| **Test** | 0.5 | 0.283 | 0.441 | 0.610 | 0.346 |
| **Test** | 0.1 (tuned) | **0.308** | **0.471** | 0.468 | 0.474 |

Test IoU by country (tuned threshold):

| Country | Tiles | IoU | F1 |
|---|---|---|---|
| Philippines | 70 | 0.606 | 0.755 |
| Mozambique | 1,296 | 0.600 | 0.750 |
| Japan | 1,307 | 0.464 | 0.634 |
| Tajikistan | 791 | 0.421 | 0.593 |
| Eswatini | 2,376 | 0.265 | 0.419 |
| Peru | 1,396 | 0.123 | 0.219 |

![Training curves](runs/unet_baseline/curves.png)
![Test predictions](runs/unet_baseline/predictions.png)

### What we learned

- **It generalises unevenly.** On imagery similar to training (Mozambique, Philippines) it reaches
  ~0.60 IoU. On Peru, whose roof materials and building density differ from anything in
  training, it drops to 0.12.
- **It's under-confident.** At 0.5 it has high precision but low recall. The best validation
  threshold is 0.1, which suggests the Dice term pushes probabilities down. Calibrating the
  outputs or using a lower threshold recovers recall.
- **Validation is noisy** (0.23–0.36 between epochs) because three locations dominate it.
  Selecting the checkpoint by validation IoU is still better than taking the last epoch.
- **Some labels are noisy.** Some test tiles are blurry or the OSM outlines are offset from the
  imagery, which caps the achievable IoU (see the first rows of the prediction grid).

These results motivate CP3: an **ImageNet-pretrained encoder** should transfer far better to
unseen locations than features learned from 21k tiles, and training on all 11 shards adds
diversity.

## Reproduce

```bash
pip install torch==2.7.1+cu126 torchvision==0.22.1+cu126 --extra-index-url https://download.pytorch.org/whl/cu126
pip install -r ml/requirements.txt
python ml/prepare_data.py                          # ~3.7 GB download
cd ml
python train.py --run unet_baseline --epochs 15    # add --resume to continue an interrupted run
python evaluate.py --run unet_baseline
```
