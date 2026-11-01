"""When was the satellite image at a location captured?

Esri publishes a metadata service for every World Imagery release through its Wayback archive.
The newest release matches the live basemap the planner shows, so its metadata gives the
capture date and resolution of the imagery a visitor is looking at.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import date

import httpx

WAYBACK_CONFIG = "https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json"
METADATA_LAYERS = range(8)          # finest resolution first; the first layer with a hit is what the map shows
CONFIG_TTL_S = 12 * 3600
RESULT_TTL_S = 24 * 3600

_release: dict = {"url": None, "date": None, "fetched": 0.0}
_results: dict[tuple[float, float], tuple[float, dict | None]] = {}


async def _latest_release(client: httpx.AsyncClient) -> tuple[str, str]:
    if _release["url"] and time.time() - _release["fetched"] < CONFIG_TTL_S:
        return _release["url"], _release["date"]
    resp = await client.get(WAYBACK_CONFIG)
    resp.raise_for_status()
    releases = []
    for item in resp.json().values():
        match = re.search(r"(\d{4}-\d{2}-\d{2})", item.get("itemTitle", ""))
        if match and item.get("metadataLayerUrl"):
            releases.append((match.group(1), item["metadataLayerUrl"]))
    released, url = max(releases)
    _release.update(url=url, date=released, fetched=time.time())
    return url, released


async def capture_info(lat: float, lon: float) -> dict | None:
    """Capture date and resolution of the sharpest imagery at a point, or None if unknown."""
    key = (round(lat, 3), round(lon, 3))   # ~100 m cells share a result
    cached = _results.get(key)
    if cached and time.time() - cached[0] < RESULT_TTL_S:
        return cached[1]

    params = {
        "f": "json", "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "returnGeometry": "false",
        "outFields": "SRC_DATE,SRC_RES,SRC_DESC,NICE_DESC,MaxMapLevel",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        url, released = await _latest_release(client)

        async def query(layer: int):
            r = await client.get(f"{url}/{layer}/query", params=params)
            r.raise_for_status()
            return r.json().get("features", [])

        layers = await asyncio.gather(*(query(i) for i in METADATA_LAYERS))

    hit = next((feats[0]["attributes"] for feats in layers if feats), None)
    info = None
    if hit and hit.get("SRC_DATE"):
        raw = str(hit["SRC_DATE"])
        info = {
            "captured": date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])).isoformat(),
            "resolution_m": hit.get("SRC_RES"),
            "max_zoom": hit.get("MaxMapLevel"),
            "provider": hit.get("NICE_DESC"),
            "release": released,
        }
    _results[key] = (time.time(), info)
    return info
