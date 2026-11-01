import asyncio

import httpx
from fastapi.testclient import TestClient

from app import imagery, main

client = TestClient(main.app)


def test_imagery_date_endpoint_returns_capture_info(monkeypatch):
    async def fake(lat, lon):
        return {"captured": "2026-02-15", "resolution_m": 0.31, "max_zoom": 19, "provider": "Vantor", "release": "2026-10-29"}

    monkeypatch.setattr(imagery, "capture_info", fake)
    body = client.get("/api/imagery-date", params={"lat": 17.4152, "lon": 78.4345}).json()
    assert body == {"found": True, "captured": "2026-02-15", "resolution_m": 0.31, "max_zoom": 19,
                    "provider": "Vantor", "release": "2026-10-29"}


def test_imagery_date_endpoint_survives_upstream_errors(monkeypatch):
    async def broken(lat, lon):
        raise httpx.ConnectError("metadata service down")

    monkeypatch.setattr(imagery, "capture_info", broken)
    assert client.get("/api/imagery-date", params={"lat": 17.4, "lon": 78.4}).json() == {"found": False}


def test_imagery_date_validates_coordinates():
    assert client.get("/api/imagery-date", params={"lat": 120, "lon": 78.4}).status_code == 422


def test_capture_info_picks_the_finest_layer_and_parses_dates(monkeypatch):
    imagery._results.clear()
    imagery._release.update(url="https://meta.example/MapServer", date="2026-10-29", fetched=10**12)
    hits = {4: [{"attributes": {"SRC_DATE": 20260215, "SRC_RES": 0.31, "NICE_DESC": "Vantor", "MaxMapLevel": 19}}],
            5: [{"attributes": {"SRC_DATE": 20240101, "SRC_RES": 0.6, "NICE_DESC": "Old", "MaxMapLevel": 18}}]}

    def handler(request: httpx.Request):
        layer = int(request.url.path.split("/")[-2])
        return httpx.Response(200, json={"features": hits.get(layer, [])})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(imagery.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    info = asyncio.run(imagery.capture_info(17.4152, 78.4345))
    assert info == {"captured": "2026-02-15", "resolution_m": 0.31, "max_zoom": 19, "provider": "Vantor", "release": "2026-10-29"}
