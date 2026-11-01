"""Roof segmentation models.

- UNet: Ronneberger et al. (2015), trained from scratch; the CP2 baseline.
- ResNetUNet: same decoder idea on an ImageNet-pretrained ResNet-34 encoder (CP3).
"""
import torch
from torch import nn


class ConvBlock(nn.Sequential):
    def __init__(self, c_in: int, c_out: int):
        super().__init__(
            nn.Conv2d(c_in, c_out, 3, padding=1, bias=False),
            nn.BatchNorm2d(c_out),
            nn.ReLU(inplace=True),
            nn.Conv2d(c_out, c_out, 3, padding=1, bias=False),
            nn.BatchNorm2d(c_out),
            nn.ReLU(inplace=True),
        )


class UNet(nn.Module):
    def __init__(self, in_channels: int = 3, base: int = 32, depth: int = 4):
        super().__init__()
        widths = [base * 2**i for i in range(depth + 1)]  # 32, 64, 128, 256, 512

        self.encoders = nn.ModuleList()
        c = in_channels
        for w in widths:
            self.encoders.append(ConvBlock(c, w))
            c = w
        self.pool = nn.MaxPool2d(2)

        self.ups = nn.ModuleList()
        self.decoders = nn.ModuleList()
        for w in reversed(widths[:-1]):
            self.ups.append(nn.ConvTranspose2d(c, w, 2, stride=2))
            self.decoders.append(ConvBlock(w * 2, w))
            c = w
        self.head = nn.Conv2d(c, 1, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        skips = []
        for i, enc in enumerate(self.encoders):
            x = enc(x)
            if i < len(self.encoders) - 1:
                skips.append(x)
                x = self.pool(x)
        for up, dec, skip in zip(self.ups, self.decoders, reversed(skips)):
            x = dec(torch.cat([up(x), skip], dim=1))
        return self.head(x)  # logits, 1 channel


class DecoderBlock(nn.Module):
    def __init__(self, c_in: int, c_skip: int, c_out: int):
        super().__init__()
        self.up = nn.Upsample(scale_factor=2, mode="nearest")
        self.conv = ConvBlock(c_in + c_skip, c_out)

    def forward(self, x, skip=None):
        x = self.up(x)
        if skip is not None:
            x = torch.cat([x, skip], dim=1)
        return self.conv(x)


class ResNetUNet(nn.Module):
    """U-Net decoder on an ImageNet-pretrained ResNet-34 encoder (CP3).

    Pretrained features (edges, textures, shapes) transfer to new locations far better than
    features learned from ~20k tiles alone.
    """

    def __init__(self, pretrained: bool = True):
        super().__init__()
        from torchvision.models import ResNet34_Weights, resnet34

        enc = resnet34(weights=ResNet34_Weights.IMAGENET1K_V1 if pretrained else None)
        self.stem = nn.Sequential(enc.conv1, enc.bn1, enc.relu)       # 64 ch, 1/2
        self.pool = enc.maxpool
        self.layers = nn.ModuleList([enc.layer1, enc.layer2, enc.layer3, enc.layer4])  # 64/128/256/512 ch

        self.decoder = nn.ModuleList([
            DecoderBlock(512, 256, 256),  # 1/16
            DecoderBlock(256, 128, 128),  # 1/8
            DecoderBlock(128, 64, 64),    # 1/4
            DecoderBlock(64, 64, 32),     # 1/2
            DecoderBlock(32, 0, 16),      # full resolution
        ])
        self.head = nn.Conv2d(16, 1, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x0 = self.stem(x)
        feats = [x0]
        x = self.pool(x0)
        for layer in self.layers:
            x = layer(x)
            feats.append(x)
        x = feats.pop()                              # deepest features, 1/32
        for block in self.decoder:
            x = block(x, feats.pop() if feats else None)
        return self.head(x)


ARCHS = ("unet", "resnet34_unet")


def build_model(arch: str = "unet", base: int = 32, pretrained: bool = True) -> nn.Module:
    if arch == "unet":
        return UNet(base=base)
    if arch == "resnet34_unet":
        return ResNetUNet(pretrained=pretrained)
    raise ValueError(f"Unknown architecture {arch!r}; choose from {ARCHS}")


def describe(arch: str, base: int = 32) -> str:
    return {"unet": f"U-Net (from scratch, base={base})",
            "resnet34_unet": "U-Net with ImageNet-pretrained ResNet-34 encoder"}[arch]


def count_params(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters() if p.requires_grad)
