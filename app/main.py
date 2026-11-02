import base64
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import finance, imagery, roof, solar


def _load_dotenv(path: Path = Path(__file__).parent.parent / ".env") -> None:
    """Read KEY=value lines from a local .env (kept out of git) without overriding real env vars."""
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()
app = FastAPI(title="Rooftop Solar Planner")

# Browsers may load scripts and styles only from these places. The one inline script sets the theme
# before first paint; its hash is checked in tests/test_security.py.
THEME_SCRIPT_HASH = "'sha256-opRjTqQ1E5eUpc4SE9v3YoU5wuVpgtqT7zsobCj245Q='"
CSP = "; ".join([
    "default-src 'self'",
    f"script-src 'self' {THEME_SCRIPT_HASH} https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://www.googletagmanager.com",
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com",   # Leaflet positions tiles with inline styles
    "img-src 'self' data: blob: https://cdnjs.cloudflare.com https://tile.openstreetmap.org"
    " https://*.google-analytics.com https://*.googletagmanager.com",
    "connect-src 'self' https://nominatim.openstreetmap.org https://*.google-analytics.com"
    " https://*.analytics.google.com https://*.googletagmanager.com",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.headers["Content-Security-Policy"] = CSP + ("; upgrade-insecure-requests" if https else "")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=(), payment=()"
    if https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


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


UTM_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}


class TrackEvent(BaseModel):
    """Anonymous usage event. Never contains coordinates, addresses or other personal data."""
    event: Literal["page_view", "search", "location_used", "roof_detected", "roof_not_found",
                   "roof_detect_error", "estimate"]
    path: str = Field("", max_length=200)
    referrer: str = Field("", max_length=200)
    utm: dict[str, str] = Field(default_factory=dict)
    data: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("utm")
    @classmethod
    def known_utm_keys(cls, v: dict[str, str]) -> dict[str, str]:
        return {k: val[:100] for k, val in v.items() if k in UTM_KEYS}

    @field_validator("data")
    @classmethod
    def small_payload(cls, v: dict) -> dict:
        if len(v) > 10:
            raise ValueError("too many fields")
        return {k[:40]: (val[:100] if isinstance(val, str) else val) for k, val in v.items()}


@app.post("/api/track", status_code=204)
def track(evt: TrackEvent):
    # One JSON line per event in the server log
    print("event " + json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **evt.model_dump()}), flush=True)
    return Response(status_code=204)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def config():
    # The ArcGIS key never leaves the server: map tiles come through /api/tiles instead
    ga_id = os.environ.get("GA_MEASUREMENT_ID", "")
    return {"imagery": imagery_key() is not None,
            "ga_measurement_id": ga_id if re.fullmatch(r"G-[A-Z0-9]{4,12}", ga_id) else None}


TILE_SERVICES = {
    "imagery": "https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile",
    "labels": "https://ibasemaps-api.arcgis.com/arcgis/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile",
}
BLANK_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNgYGBgAAAABQABeqhXUAAAAABJRU5ErkJggg==")
_tile_client: httpx.AsyncClient | None = None


def tile_client() -> httpx.AsyncClient:
    global _tile_client
    if _tile_client is None or _tile_client.is_closed:
        _tile_client = httpx.AsyncClient(timeout=15, limits=httpx.Limits(max_connections=32))
    return _tile_client


@app.get("/api/tiles/{layer}/{z}/{y}/{x}")
async def tile(layer: Literal["imagery", "labels"], z: int, y: int, x: int, request: Request):
    """Map tiles fetched with the server's key, so the key is never sent to browsers."""
    if not 0 <= z <= 19 or not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise HTTPException(404, "No such tile")
    # Only this site's pages may use the proxy (stops other sites from spending the key's quota)
    referer = request.headers.get("referer")
    if referer and urlsplit(referer).netloc != request.headers.get("host"):
        raise HTTPException(403, "Tiles are only served to this site")
    key = imagery_key()
    if not key:
        raise HTTPException(503, "Satellite imagery is not configured")
    try:
        upstream = await tile_client().get(f"{TILE_SERVICES[layer]}/{z}/{y}/{x}", params={"token": key})
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Imagery service did not respond") from exc
    if upstream.status_code == 404:
        if layer == "labels":   # places with no names to draw: a blank tile keeps the browser console clean
            return Response(BLANK_PNG, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
        raise HTTPException(404, "No imagery at this zoom")   # the map then falls back to a lower zoom
    if upstream.status_code != 200:
        raise HTTPException(502, "Imagery service error")
    return Response(upstream.content, media_type=upstream.headers.get("content-type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=86400"})


def site_url(request: Request) -> str:
    """Public address of the site, from SITE_URL or the request (hosting proxies send X-Forwarded-Proto)."""
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"].rstrip("/")
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{scheme}://{request.headers.get('host', request.url.netloc)}"


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots(request: Request):
    return f"User-agent: *\nAllow: /\nDisallow: /api/\n\nSitemap: {site_url(request)}/sitemap.xml\n"


@app.get("/sitemap.xml")
def sitemap(request: Request):
    base = site_url(request)
    urls = "".join(f"  <url><loc>{base}{path}</loc></url>\n" for path in ("/", "/planner/"))
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n')
    return Response(xml, media_type="application/xml")


@app.get("/google{token}.html", include_in_schema=False)
def search_console_file(token: str):
    """Google Search Console's HTML file check: set GOOGLE_SITE_VERIFICATION to the file name it gives you."""
    name = "google" + token
    expected = os.environ.get("GOOGLE_SITE_VERIFICATION", "").removesuffix(".html")
    if expected and name == expected and re.fullmatch(r"google[0-9a-f]{8,32}", name):
        return PlainTextResponse(f"google-site-verification: {name}.html")
    raise HTTPException(404)


@app.get("/api/imagery-date")
async def imagery_date(lat: float = Query(ge=-85, le=85), lon: float = Query(ge=-180, le=180)):
    """Capture date of the satellite image shown at a point (from Esri's imagery metadata)."""
    try:
        info = await imagery.capture_info(lat, lon)
    except (httpx.HTTPError, ValueError, KeyError):
        info = None
    return {"found": info is not None, **(info or {})}


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
