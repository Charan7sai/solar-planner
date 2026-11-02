import pytest
from fastapi.testclient import TestClient

from app import finance, main, shading, solar

client = TestClient(main.app)
CLIMATE = {
    "ghi": {y: [5.5] * 12 for y in range(2004, 2024)},
    "diff": {y: [2.2] * 12 for y in range(2004, 2024)},
    "t2m": {y: [27.0] * 12 for y in range(2004, 2024)},
}


def test_surplus_is_banked_before_earning_the_export_rate():
    gen = [50] * 6 + [150] * 6      # same yearly total as use, but surplus comes last
    surplus_last = finance.cash_flow(1, gen, 100, tariff=8, export_rate=3, cost_per_kw=60_000)
    assert surplus_last["year_end_surplus_units"] == 300
    surplus_first = finance.cash_flow(1, gen[::-1], 100, tariff=8, export_rate=3, cost_per_kw=60_000)
    assert surplus_first["year_end_surplus_units"] == 0
    assert surplus_first["monthly_bill_after"] == 0
    assert surplus_first["first_year_savings"] > surplus_last["first_year_savings"]


def test_bill_uses_the_flat_rate():
    money = finance.cash_flow(1, [100] * 12, 300, tariff=7, export_rate=3, cost_per_kw=60_000)
    assert money["monthly_bill_before"] == 2100
    assert money["monthly_bill_after"] == 1400


def test_sun_position_at_equinox_noon_is_due_south_from_india():
    elevation, azimuth = shading.sun_position(17.4, 81, 0)
    assert 71 < elevation < 73 and azimuth == pytest.approx(180, abs=1)
    assert shading.sun_position(17.4, 81, -60)[1] < 180     # morning sun is in the east


def test_shade_is_worst_from_the_south_in_winter_and_from_taller_obstacles():
    south = shading.monthly_loss(17.4, [{"direction": "S", "height_m": 10, "distance_m": 10}])
    assert south[11] > south[5]                             # the sun is low in the south in December
    low = shading.monthly_loss(17.4, [{"direction": "S", "height_m": 2, "distance_m": 10}])
    assert sum(low) < sum(south)
    north = shading.monthly_loss(17.4, [{"direction": "N", "height_m": 10, "distance_m": 10}])
    assert sum(north) < sum(south)                          # in India the sun is mostly south of the roof
    assert all(0 <= v < 1 for v in south + low + north)


def test_estimate_api_applies_the_subsidy_and_shade(monkeypatch):
    async def climate(lat, lon):
        return CLIMATE
    monkeypatch.setattr(solar, "fetch_climate", climate)
    body = {"lat": 10, "lon": 76, "roof_area_m2": 100, "monthly_units": 400, "kwp": 3}
    clear = client.post("/api/estimate", json=body).json()
    assert clear["shade"] is None
    assert clear["finance"]["monthly_bill_before"] == 2800          # the default flat tariff of Rs 7
    assert clear["finance"]["subsidy"] == 78_000

    obstacle = {"direction": "S", "height_m": 8, "distance_m": 6}
    shaded = client.post("/api/estimate", json={**body, "obstacles": [obstacle]}).json()
    assert 0 < shaded["shade"]["annual_pct"] < 100
    assert shaded["generation"]["annual_p50"] < clear["generation"]["annual_p50"]

    bad = {**obstacle, "height_m": 0}
    assert client.post("/api/estimate", json={**body, "obstacles": [bad]}).status_code == 422
