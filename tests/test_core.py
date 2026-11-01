import pytest

from app import finance, solar


@pytest.mark.parametrize("kwp, expected", [(1, 30_000), (2, 60_000), (2.5, 69_000), (3, 78_000), (10, 78_000)])
def test_subsidy_slabs(kwp, expected):
    assert finance.pm_surya_ghar_subsidy(kwp) == expected


def test_size_limited_by_roof():
    s = finance.recommend_size(roof_area_m2=20, monthly_units=1000, specific_yield=1400)
    assert s["limited_by"] == "roof"
    assert s["recommended_kwp"] == 1.5  # 20 m2 * 0.75 / 10 = 1.5 kWp


def test_size_limited_by_consumption():
    s = finance.recommend_size(roof_area_m2=200, monthly_units=300, specific_yield=1440)
    assert s["limited_by"] == "consumption"
    assert s["recommended_kwp"] == 2.5  # 3600 kWh / 1440 = 2.5 kWp


def test_by_year_drops_annual_and_incomplete_years():
    series = {f"2020{m:02d}": 5.0 for m in range(1, 14)}
    series.update({f"2021{m:02d}": 5.0 for m in range(1, 13)})
    series["202106"] = solar.FILL_VALUE
    assert list(solar._by_year(series)) == [2020]
    assert len(solar._by_year(series)[2020]) == 12


def test_simulation_and_payback_are_sane():
    climate = {
        "ghi": {y: [5.5] * 12 for y in range(2004, 2024)},
        "t2m": {y: [27.0] * 12 for y in range(2004, 2024)},
    }
    gen = solar.simulate(3.0, climate)
    # India typically yields 1300-1600 kWh/kWp/yr at ~5.5 kWh/m2/day
    assert 1300 < gen["specific_yield"] < 1700
    assert gen["annual_p90"] <= gen["annual_p50"]

    money = finance.cash_flow(3.0, gen["monthly_p50"], 400, tariff=7, export_rate=3, cost_per_kw=60_000)
    assert money["net_cost"] == 180_000 - 78_000
    assert 2 < money["payback_years"] < 8
