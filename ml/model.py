"""U-Net (Ronneberger et al., 2015) trained from scratch as the CP2 baseline."""
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


def count_params(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters() if p.requires_grad)
