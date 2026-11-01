import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import finance, roof, solar


def _load_dotenv(path: Path = Path(__file__).parent.parent / ".env") -> None:
    """Read KEY=value lines from a local .env (kept out of git) without overriding real env vars."""
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()
app = FastAPI(title="Rooftop Solar Planner")


class EstimateRequest(BaseModel):
    lat: float = Field(ge=-60, le=60)
    lon: float = Field(ge=-180, le=180)
    roof_area_m2: float = Field(gt=0, le=5000)
    monthly_units: float = Field(gt=0, le=20000, description="Average kWh per month from your bill")
    tariff: float = Field(7.0, gt=0, le=30, description="Effective Rs/kWh you pay")
    export_rate: float = Field(3.0, ge=0, le=30, description="Rs/kWh credited for surplus units")
    cost_per_kw: float = Field(60_000, gt=0, le=200_000)
    kwp: float | None = Field(None, gt=0, le=500, description="Override the recommended size")


class PinRequest(BaseModel):
    lat: float = Field(ge=-85, le=85)
    lon: float = Field(ge=-180, le=180)


def imagery_key() -> str | None:
    return os.environ.get("ARCGIS_API_KEY") or None


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def config():
    # ArcGIS basemap keys are meant to be used in browsers; restrict yours to this site's referrer
    return {"arcgis_api_key": imagery_key()}


@app.post("/api/detect-roof")
async def detect_roof(req: PinRequest):
    key = imagery_key()
    if not key:
        raise HTTPException(503, "Roof detection needs satellite imagery: set the ARCGIS_API_KEY environment variable")
    try:
        result = await roof.detect_roof(req.lat, req.lon, key)
    except roof.TileMissing as exc:
        raise HTTPException(404, f"{exc}. Please trace the roof with the polygon tool instead.") from exc
    except (roof.ImageryUnavailable, httpx.HTTPError) as exc:
        raise HTTPException(502, "Couldn't load satellite imagery right now. Please try again in a moment.") from exc
    if result is None:
        return {"found": False}
    return {"found": True, **result}


@app.post("/api/estimate")
async def estimate(req: EstimateRequest):
    try:
        climate = await solar.fetch_climate(req.lat, req.lon)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"NASA POWER request failed: {exc}") from exc
    if not climate["ghi"]:
        raise HTTPException(422, "No solar data available for this location")

    # Size from a 1 kWp reference run, then simulate the chosen size
    reference = solar.simulate(1.0, climate)
    sizing = finance.recommend_size(req.roof_area_m2, req.monthly_units, reference["specific_yield"])
    kwp = req.kwp or sizing["recommended_kwp"]

    generation = solar.simulate(kwp, climate)
    money = finance.cash_flow(
        kwp,
        generation["monthly_p50"],
        req.monthly_units,
        req.tariff,
        req.export_rate,
        req.cost_per_kw,
    )
    return {"kwp": kwp, "sizing": sizing, "generation": generation, "finance": money}


app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "static", html=True), name="static")
