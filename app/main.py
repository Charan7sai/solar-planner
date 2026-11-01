from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import finance, solar

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


@app.get("/api/health")
def health():
    return {"status": "ok"}


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
