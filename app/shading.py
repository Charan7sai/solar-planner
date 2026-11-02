"""Shade from nearby buildings and trees, from sun positions and each obstacle's height and distance.

Treats the roof as one point on a flat plane, so it can miss partial shade across a long array.
"""
from __future__ import annotations

import math

MID_MONTH_DAY = [15, 46, 75, 105, 135, 162, 198, 228, 258, 288, 318, 344]
SECTOR = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}   # each obstacle blocks 90 degrees of the horizon
CLEAR_SKY_TRANSMITTANCE = 0.7
DEFAULT_DIFFUSE = 0.4


def sun_position(lat: float, day: int, hour_angle: float) -> tuple[float, float]:
    """Sun elevation and azimuth (clockwise from north) in degrees. hour_angle is degrees from solar noon."""
    decl = math.radians(23.45 * math.sin(math.radians(360 * (284 + day) / 365)))
    phi, h = math.radians(lat), math.radians(hour_angle)
    elevation = math.asin(math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(h))
    azimuth = math.atan2(-math.cos(decl) * math.sin(h),
                         math.sin(decl) * math.cos(phi) - math.cos(decl) * math.sin(phi) * math.cos(h))
    return math.degrees(elevation), math.degrees(azimuth) % 360


def _blocked_beam_fraction(lat: float, day: int, horizons: list[tuple[float, float]]) -> float:
    total = blocked = 0.0
    for step in range(-720, 720, 15):   # every 15 minutes
        elevation, azimuth = sun_position(lat, day, step / 4)
        if elevation <= 0:
            continue
        sin_el = math.sin(math.radians(elevation))
        beam = sin_el * CLEAR_SKY_TRANSMITTANCE ** (1 / sin_el)
        total += beam
        if any(elevation < angle and abs((azimuth - centre + 180) % 360 - 180) <= 45 for centre, angle in horizons):
            blocked += beam
    return blocked / total if total else 0.0


def monthly_loss(lat: float, obstacles: list[dict], diffuse: list[float] | None = None) -> list[float]:
    """Fraction of sunlight lost in each month. Obstacles have direction, height_m (above the roof) and distance_m."""
    diffuse = diffuse or [DEFAULT_DIFFUSE] * 12
    horizons = [(SECTOR[o["direction"]], math.degrees(math.atan2(o["height_m"], o["distance_m"]))) for o in obstacles]
    # Uniform sky: a wall of elevation a across a quarter of the horizon hides sin^2(a) / 4 of diffuse light
    diffuse_lost = min(sum(math.sin(math.radians(angle)) ** 2 / 4 for _, angle in horizons), 1.0)
    return [(1 - diffuse[m]) * _blocked_beam_fraction(lat, MID_MONTH_DAY[m], horizons) + diffuse[m] * diffuse_lost
            for m in range(12)]


def diffuse_fractions(climate: dict) -> list[float] | None:
    """Average share of sunlight arriving as diffuse light in each month, from NASA's data."""
    if not climate.get("diff"):
        return None
    years = sorted(set(climate["diff"]) & set(climate["ghi"]))
    return [min(sum(climate["diff"][y][m] for y in years) / max(sum(climate["ghi"][y][m] for y in years), 1e-6), 1.0)
            for m in range(12)]
