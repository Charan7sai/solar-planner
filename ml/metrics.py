"""Pixel-level segmentation metrics, accumulated over a whole split (micro-averaged)."""
import torch
import torch.nn.functional as F


class SegMetrics:
    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        self.tp = self.fp = self.fn = self.tn = 0

    @torch.no_grad()
    def update(self, logits: torch.Tensor, target: torch.Tensor) -> None:
        pred = torch.sigmoid(logits) > self.threshold
        truth = target > 0.5
        self.tp += (pred & truth).sum().item()
        self.fp += (pred & ~truth).sum().item()
        self.fn += (~pred & truth).sum().item()
        self.tn += (~pred & ~truth).sum().item()

    def compute(self) -> dict:
        eps = 1e-9
        precision = self.tp / (self.tp + self.fp + eps)
        recall = self.tp / (self.tp + self.fn + eps)
        return {
            "iou": self.tp / (self.tp + self.fp + self.fn + eps),
            "f1": 2 * precision * recall / (precision + recall + eps),
            "precision": precision,
            "recall": recall,
            "accuracy": (self.tp + self.tn) / (self.tp + self.fp + self.fn + self.tn + eps),
        }


def bce_dice_loss(logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    """BCE for per-pixel calibration + soft Dice to counter background/building imbalance."""
    bce = F.binary_cross_entropy_with_logits(logits, target)
    prob = torch.sigmoid(logits)
    inter = (prob * target).sum(dim=(1, 2, 3))
    union = prob.sum(dim=(1, 2, 3)) + target.sum(dim=(1, 2, 3))
    dice = 1 - (2 * inter + 1) / (union + 1)
    return bce + dice.mean()
