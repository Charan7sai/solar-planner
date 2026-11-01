"""Solar resource data (NASA POWER) and PV generation modelling."""
from __future__ import annotations

import statistics

import httpx

NASA_URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"
FILL_VALUE = -999.0
START_YEAR, END_YEAR = 2004, 2023  # 20 full years of history

DAYS_IN_MONTH = [31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

# PV system assumptions (typical Indian residential mono-PERC install)
BASE_PR = 0.85          # inverter, wiring, soiling, mismatch losses before temperature
TEMP_COEFF = -0.004     # power loss per degC above 25 degC cell temperature
CELL_TEMP_RISE = 20.0   # cell runs ~20 degC hotter than ambient under sun

_cache: dict[tuple[float, float], dict] = {}


async def fetch_climate(lat: float, lon: float) -> dict:
    """Return {"ghi": {year: [12 values]}, "t2m": {year: [12 values]}} for a location.

    GHI is daily-mean global horizontal irradiance in kWh/m2/day, T2M is air temperature in degC.
    """
    key = (round(lat, 2), round(lon, 2))
    if key in _cache:
        return _cache[key]

    params = {
        "parameters": "ALLSKY_SFC_SW_DWN,T2M",
        "community": "RE",
        "latitude": key[0],
        "longitude": key[1],
        "start": START_YEAR,
        "end": END_YEAR,
        "format": "JSON",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(NASA_URL, params=params)
        resp.raise_for_status()
    raw = resp.json()["properties"]["parameter"]

    climate = {"ghi": _by_year(raw["ALLSKY_SFC_SW_DWN"]), "t2m": _by_year(raw["T2M"])}
    _cache[key] = climate
    return climate


def _by_year(series: dict[str, float]) -> dict[int, list[float]]:
    """Group NASA's "YYYYMM" keys into 12-month lists, dropping annual (MM=13) and incomplete years."""
    years: dict[int, list[float]] = {}
    for stamp, value in series.items():
        year, month = int(stamp[:4]), int(stamp[4:])
        if month == 13:
            continue
        years.setdefault(year, [None] * 12)[month - 1] = None if value == FILL_VALUE else value
    return {y: v for y, v in years.items() if None not in v}


def performance_ratio(air_temp: float) -> float:
    cell_temp = air_temp + CELL_TEMP_RISE
    return BASE_PR * (1 + TEMP_COEFF * max(cell_temp - 25, 0))


def monthly_generation(kwp: float, ghi: list[float], t2m: list[float]) -> list[float]:
    """kWh produced in each month of one year by a kwp-sized system."""
    return [
        kwp * g * DAYS_IN_MONTH[m] * performance_ratio(t2m[m])
        for m, g in enumerate(ghi)
    ]


def simulate(kwp: float, climate: dict) -> dict:
    """Run the system through every historical year and summarise the spread.

    P50 is the median year; P90 is the output exceeded in 90% of years (the bankable,
    conservative figure).
    """
    years = sorted(set(climate["ghi"]) & set(climate["t2m"]))
    per_year = {y: monthly_generation(kwp, climate["ghi"][y], climate["t2m"][y]) for y in years}
    annual = [sum(per_year[y]) for y in years]

    monthly_p50 = [statistics.median(per_year[y][m] for y in years) for m in range(12)]
    deciles = statistics.quantiles(annual, n=10)

    return {
        "years": years,
        "annual_by_year": [round(a) for a in annual],
        "monthly_p50": [round(v, 1) for v in monthly_p50],
        "annual_p50": round(statistics.median(annual)),
        "annual_p90": round(deciles[0]),
        "specific_yield": round(statistics.median(annual) / kwp, 1),  # kWh per kWp per year
        "ghi_mean": round(statistics.mean(sum(climate["ghi"][y]) / 12 for y in years), 2),
    }
