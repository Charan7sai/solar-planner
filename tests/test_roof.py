import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import main, roof

LAT, LON = 17.4401, 78.3489  # Hyderabad


def rectangle_prob(size=512, box=(236, 226, 296, 266)):
    """Probability map with one 60x40 px 'roof' around the window centre."""
    prob = np.zeros((size, size), dtype=np.float32)
    x0, y0, x1, y1 = box
    prob[y0:y1, x0:x1] = 0.9
    return prob


def test_pixel_latlon_roundtrip():
    x, y = roof.latlon_to_pixel(LAT, LON)
    lat, lon = roof.pixel_to_latlon(x, y)
    assert lat == pytest.approx(LAT, abs=1e-9) and lon == pytest.approx(LON, abs=1e-9)


def test_meters_per_pixel_at_zoom_19():
    assert roof.meters_per_pixel(0) == pytest.approx(0.2986, abs=1e-3)
    assert roof.meters_per_pixel(LAT) < roof.meters_per_pixel(0)


def test_extract_roof_under_pin():
    result = roof.extract_roof(rectangle_prob(), pin=(256, 246), threshold=0.5)
    assert len(result["polygon_px"]) == 4
    assert result["area_px"] == pytest.approx(60 * 40, rel=0.05)
    assert result["confidence"] == pytest.approx(0.9)


def test_extract_roof_snaps_to_nearby_roof_only():
    prob = rectangle_prob()
    assert roof.extract_roof(prob, pin=(256, 276), threshold=0.5) is not None  # 10 px below the roof
    assert roof.extract_roof(prob, pin=(256, 320), threshold=0.5) is None      # 54 px away


def test_model_runs_on_cpu():
    prob = roof.predict(np.zeros((256, 256, 3), dtype=np.uint8))
    assert prob.shape == (256, 256)
    assert 0 <= prob.min() and prob.max() <= 1


def test_detect_roof_requires_api_key(monkeypatch):
    monkeypatch.delenv("ARCGIS_API_KEY", raising=False)
    r = TestClient(main.app).post("/api/detect-roof", json={"lat": LAT, "lon": LON})
    assert r.status_code == 503


def test_detect_roof_returns_polygon_and_area(monkeypatch):
    monkeypatch.setenv("ARCGIS_API_KEY", "test-key")

    async def fake_fetch(lat, lon, api_key, size=roof.WINDOW):
        cx, cy = roof.latlon_to_pixel(lat, lon)
        return np.zeros((size, size, 3), dtype=np.uint8), (int(cx) - size // 2, int(cy) - size // 2)

    monkeypatch.setattr(roof, "fetch_window", fake_fetch)
    monkeypatch.setattr(roof, "predict", lambda image: rectangle_prob())

    r = TestClient(main.app).post("/api/detect-roof", json={"lat": LAT, "lon": LON})
    body = r.json()
    assert r.status_code == 200 and body["found"]
    ring = body["polygon"]["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) == 5
    # 60 x 40 px at ~0.285 m/px is about 195 m2
    assert body["area_m2"] == pytest.approx(2400 * roof.meters_per_pixel(LAT) ** 2, rel=0.05)
    lons, lats = zip(*ring)
    assert min(lats) < LAT < max(lats) and min(lons) < LON < max(lons)


def test_watershed_splits_touching_roofs():
    prob = np.zeros((128, 128), dtype=np.float32)
    prob[40:80, 20:60] = 0.9   # roof A
    prob[40:80, 66:106] = 0.9  # roof B
    prob[40:80, 60:66] = 0.45  # low-confidence strip where they touch
    merged = roof.extract_roof(prob, pin=(40, 60), threshold=0.35)
    split = roof.extract_roof(prob, pin=(40, 60), threshold=0.35, core_threshold=0.65)
    assert merged["area_px"] > 3000             # A + strip + B
    assert split["area_px"] == pytest.approx(40 * 40, rel=0.15)
