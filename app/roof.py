"""Drop-a-pin roof detection: satellite tiles -> segmentation model -> roof polygon + area.

Imagery comes from the ArcGIS basemap API (World Imagery) at zoom 19, the same zoom the model was
trained on (~0.3 m/pixel). The model is the INT8 ONNX export of the CP3 ResNet-34 U-Net.
"""
from __future__ import annotations

import asyncio
import io
import json
import math
import os
from functools import lru_cache
from pathlib import Path

import cv2
import httpx
import numpy as np
from PIL import Image

ZOOM = 19
TILE = 256
WINDOW = 512                 # pixels analysed around the pin (~150 m at the equator)
SNAP_RADIUS_PX = 15          # accept a roof within ~4.5 m of the pin
EARTH_CIRCUMFERENCE_M = 40_075_016.686

MODEL_DIR = Path(__file__).parent.parent / "models"
MODEL_PATH = Path(os.environ.get("ROOF_MODEL_PATH", MODEL_DIR / "roof_segmenter_int8.onnx"))
MODEL_META = json.loads((MODEL_DIR / "roof_segmenter.json").read_text())

IMAGERY_URL = "https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"


class ImageryUnavailable(RuntimeError):
    pass


class TileMissing(ImageryUnavailable):
    """The imagery service has no tile at this zoom (common above zoom 18 outside big cities)."""


# ---- Web Mercator pixel math ----

def latlon_to_pixel(lat: float, lon: float, z: int = ZOOM) -> tuple[float, float]:
    scale = TILE * 2**z
    x = (lon + 180) / 360 * scale
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * scale
    return x, y


def pixel_to_latlon(x: float, y: float, z: int = ZOOM) -> tuple[float, float]:
    scale = TILE * 2**z
    lon = x / scale * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / scale))))
    return lat, lon


def meters_per_pixel(lat: float, z: int = ZOOM) -> float:
    return EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat)) / (TILE * 2**z)


# ---- Imagery ----

async def fetch_window(lat: float, lon: float, api_key: str, size: int = WINDOW,
                       zoom: int = ZOOM) -> tuple[np.ndarray, tuple[int, int]]:
    """Mosaic the tiles covering a size x size window centred on the pin.

    Returns the RGB image and the global pixel coordinate (at that zoom) of its top-left corner.
    """
    cx, cy = latlon_to_pixel(lat, lon, zoom)
    x0, y0 = int(cx) - size // 2, int(cy) - size // 2
    tx_range = range(x0 // TILE, (x0 + size - 1) // TILE + 1)
    ty_range = range(y0 // TILE, (y0 + size - 1) // TILE + 1)

    async with httpx.AsyncClient(timeout=15) as client:
        async def get(tx, ty):
            r = await client.get(IMAGERY_URL.format(z=zoom, x=tx, y=ty), params={"token": api_key})
            if r.status_code == 404:
                raise TileMissing(f"No zoom-{zoom} imagery for tile {tx},{ty}")
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                raise ImageryUnavailable(f"Imagery tile {tx},{ty} unavailable ({r.status_code}): {r.text[:120]}")
            return tx, ty, Image.open(io.BytesIO(r.content)).convert("RGB")

        tiles = await asyncio.gather(*(get(tx, ty) for tx in tx_range for ty in ty_range))

    mosaic = Image.new("RGB", (len(tx_range) * TILE, len(ty_range) * TILE))
    for tx, ty, img in tiles:
        mosaic.paste(img, ((tx - tx_range.start) * TILE, (ty - ty_range.start) * TILE))
    ox, oy = x0 - tx_range.start * TILE, y0 - ty_range.start * TILE
    return np.asarray(mosaic)[oy:oy + size, ox:ox + size], (x0, y0)


# ---- Model ----

@lru_cache(maxsize=1)
def _session():
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = int(os.environ.get("ROOF_MODEL_THREADS", 2))
    return ort.InferenceSession(str(MODEL_PATH), opts, providers=["CPUExecutionProvider"])


def predict(image: np.ndarray) -> np.ndarray:
    """RGB uint8 HxWx3 (H, W divisible by 32) -> roof probability HxW."""
    x = (image.astype(np.float32) / 255 - MODEL_META["mean"]) / MODEL_META["std"]
    logits = _session().run(None, {"image": x.transpose(2, 0, 1)[None].astype(np.float32)})[0][0, 0]
    return 1 / (1 + np.exp(-logits))


# ---- Post-processing ----

def _split_touching(prob: np.ndarray, mask: np.ndarray, core_threshold: float) -> np.ndarray:
    """Watershed: high-confidence cores seed regions that grow through the low-threshold mask,
    so adjoining roofs that merge at the low threshold become separate segments."""
    core = cv2.morphologyEx((prob > core_threshold).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    _, markers = cv2.connectedComponents(core)
    markers = markers + 1                       # 1 = background, 2.. = seeds
    markers[mask == 0] = 1
    markers[(mask == 1) & (core == 0)] = 0      # unknown: let the flood decide
    cv2.watershed(cv2.cvtColor((prob * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR), markers)
    return np.where(markers > 1, markers - 1, 0).astype(np.int32)


def extract_roof(prob: np.ndarray, pin: tuple[int, int], threshold: float,
                 core_threshold: float | None = None) -> dict | None:
    """Pick the roof segment under (or nearest to) the pin and trace its outline.

    With core_threshold set, touching roofs are split by watershed first.
    Returns the polygon in window pixel coordinates, its pixel area and mean confidence,
    or None if no roof lies within SNAP_RADIUS_PX of the pin.
    """
    mask = (prob > threshold).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)   # remove speckle
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)  # fill pinholes
    if core_threshold is not None:
        labels = _split_touching(prob, mask, core_threshold)
    else:
        _, labels = cv2.connectedComponents(mask)
    if labels.max() == 0:
        return None

    px, py = pin
    label = labels[py, px]
    if label == 0:
        r = SNAP_RADIUS_PX
        y_lo, x_lo = max(py - r, 0), max(px - r, 0)
        window = labels[y_lo:py + r + 1, x_lo:px + r + 1]
        ys, xs = np.nonzero(window)
        if len(ys) == 0:
            return None
        d2 = (ys + y_lo - py) ** 2 + (xs + x_lo - px) ** 2
        nearest = int(np.argmin(d2))
        if d2[nearest] > r * r:
            return None
        label = window[ys[nearest], xs[nearest]]

    region = (labels == label).astype(np.uint8)
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea)
    epsilon = max(1.5, 0.01 * cv2.arcLength(contour, True))
    polygon = cv2.approxPolyDP(contour, epsilon, True)[:, 0, :]
    return {
        "polygon_px": polygon.tolist(),
        "area_px": int(region.sum()),
        "confidence": float(prob[region > 0].mean()),
    }


def detect_in_image(image: np.ndarray, origin: tuple[int, int], lat: float, lon: float) -> dict | None:
    """Run detection on an already-fetched window whose top-left global pixel is origin."""
    prob = predict(image)
    cx, cy = latlon_to_pixel(lat, lon)
    pin = (int(cx) - origin[0], int(cy) - origin[1])
    roof = extract_roof(prob, pin, MODEL_META["pin_threshold"], MODEL_META["pin_core_threshold"])
    if roof is None:
        return None

    mpp = meters_per_pixel(lat)
    ring = [pixel_to_latlon(origin[0] + x + 0.5, origin[1] + y + 0.5) for x, y in roof["polygon_px"]]
    ring.append(ring[0])
    return {
        "polygon": {"type": "Polygon", "coordinates": [[[lo, la] for la, lo in ring]]},
        "area_m2": round(roof["area_px"] * mpp**2, 1),
        "confidence": round(roof["confidence"], 3),
        "meters_per_pixel": round(mpp, 3),
    }


async def detect_roof(lat: float, lon: float, api_key: str) -> dict | None:
    """Detect at zoom 19; where only zoom 18 exists, upscale it 2x so roofs appear at the
    scale the model was trained on (sharpness is lower, so outlines are less precise)."""
    try:
        image, origin = await fetch_window(lat, lon, api_key)
        imagery_zoom = ZOOM
    except TileMissing:
        try:
            small, (ox, oy) = await fetch_window(lat, lon, api_key, size=WINDOW // 2, zoom=ZOOM - 1)
        except TileMissing as exc:
            raise TileMissing("No high-resolution satellite imagery is available at this location") from exc
        image = cv2.resize(small, (WINDOW, WINDOW), interpolation=cv2.INTER_CUBIC)
        origin, imagery_zoom = (ox * 2, oy * 2), ZOOM - 1

    # ONNX Runtime releases the GIL, so inference in a thread keeps the server responsive
    result = await asyncio.to_thread(detect_in_image, image, origin, lat, lon)
    if result is not None:
        result["imagery_zoom"] = imagery_zoom
    return result
