"""System sizing, PM Surya Ghar subsidy and 25-year cash-flow model."""
from __future__ import annotations

AREA_PER_KWP = 10.0       # m2 of shadow-free roof needed per kWp (MNRE rule of thumb)
USABLE_ROOF_FRACTION = 0.75  # setbacks, walkways, water tanks
GRID_CO2_KG_PER_KWH = 0.71   # CEA India grid emission factor (approx.)
LIFETIME_YEARS = 25


def pm_surya_ghar_subsidy(kwp: float) -> float:
    """Central financial assistance for residential rooftop solar (PM Surya Ghar, 2024).

    Rs 30,000/kW for the first 2 kW, Rs 18,000 for the 3rd kW, capped at Rs 78,000.
    """
    first = min(kwp, 2) * 30_000
    third = min(max(kwp - 2, 0), 1) * 18_000
    return min(first + third, 78_000)


def roof_capacity_kwp(roof_area_m2: float) -> float:
    return roof_area_m2 * USABLE_ROOF_FRACTION / AREA_PER_KWP


def recommend_size(roof_area_m2: float, monthly_units: float, specific_yield: float) -> dict:
    """Size to cover annual consumption, limited by roof space; rounded down to 0.5 kW steps."""
    cap = roof_capacity_kwp(roof_area_m2)
    need = monthly_units * 12 / specific_yield
    kwp = max(min(cap, need) // 0.5 * 0.5, 1.0)
    return {
        "roof_capacity_kwp": round(cap, 2),
        "consumption_kwp": round(need, 2),
        "recommended_kwp": kwp,
        "limited_by": "roof" if cap < need else "consumption",
        "panels": int(round(kwp * 1000 / 550)),  # 550 Wp modules
    }


def cash_flow(
    kwp: float,
    monthly_gen: list[float],
    monthly_units: float,
    tariff: float,
    export_rate: float,
    cost_per_kw: float,
    degradation: float = 0.005,
    tariff_escalation: float = 0.03,
    om_rate: float = 0.01,
) -> dict:
    """Net-metering savings: self-used units save the retail tariff, surplus earns the export rate.

    Netting is done month by month against a flat monthly consumption profile.
    """
    gross_cost = kwp * cost_per_kw
    subsidy = pm_surya_ghar_subsidy(kwp)
    net_cost = gross_cost - subsidy

    cumulative = -net_cost
    yearly, payback = [], None
    for year in range(1, LIFETIME_YEARS + 1):
        derate = (1 - degradation) ** (year - 1)
        escal = (1 + tariff_escalation) ** (year - 1)
        saved = exported = 0.0
        for gen in monthly_gen:
            g = gen * derate
            saved += min(g, monthly_units)
            exported += max(g - monthly_units, 0)
        savings = saved * tariff * escal + exported * export_rate - gross_cost * om_rate
        prev = cumulative
        cumulative += savings
        if payback is None and cumulative >= 0:
            payback = year - 1 + (-prev / savings)
        yearly.append({"year": year, "savings": round(savings), "cumulative": round(cumulative)})

    first_year_gen = sum(monthly_gen)
    return {
        "gross_cost": round(gross_cost),
        "subsidy": round(subsidy),
        "net_cost": round(net_cost),
        "first_year_savings": yearly[0]["savings"],
        "payback_years": round(payback, 1) if payback is not None else None,
        "lifetime_net_savings": yearly[-1]["cumulative"],
        "co2_tonnes_per_year": round(first_year_gen * GRID_CO2_KG_PER_KWH / 1000, 2),
        "yearly": yearly,
    }
