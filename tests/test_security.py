import base64
import hashlib
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)
ROOT = Path(main.__file__).parent.parent
FAKE_KEY = "test-key-not-real"


@pytest.fixture
def upstream(monkeypatch):
    """Stand-in for the ArcGIS tile service; records the requests it receives."""
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        if "/19/" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, content=b"jpegbytes", headers={"content-type": "image/jpeg"})

    monkeypatch.setenv("ARCGIS_API_KEY", FAKE_KEY)
    monkeypatch.setattr(main, "_tile_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return seen


def test_config_never_returns_the_key(monkeypatch):
    monkeypatch.setenv("ARCGIS_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GA_MEASUREMENT_ID", "G-ABC123XYZ")
    r = client.get("/api/config")
    assert r.json() == {"imagery": True, "ga_measurement_id": "G-ABC123XYZ"}
    assert FAKE_KEY not in r.text


def test_config_ignores_a_malformed_analytics_id(monkeypatch):
    monkeypatch.setenv("GA_MEASUREMENT_ID", "<script>")
    assert client.get("/api/config").json()["ga_measurement_id"] is None


def test_tile_proxy_adds_the_key_on_the_server(upstream):
    r = client.get("/api/tiles/imagery/18/117000/185000", headers={"referer": "http://testserver/planner/"})
    assert r.status_code == 200 and r.content == b"jpegbytes"
    assert "max-age" in r.headers["cache-control"]
    assert upstream[0].url.params["token"] == FAKE_KEY
    assert FAKE_KEY not in str(r.headers)


def test_tile_proxy_passes_on_missing_tiles_so_the_map_falls_back(upstream):
    assert client.get("/api/tiles/imagery/19/1/1").status_code == 404
    r = client.get("/api/tiles/labels/19/1/1")          # missing labels are just blank
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_tile_proxy_refuses_other_sites_and_bad_tiles(upstream):
    assert client.get("/api/tiles/imagery/18/1/1", headers={"referer": "https://evil.example/"}).status_code == 403
    assert client.get("/api/tiles/imagery/20/1/1").status_code == 404
    assert client.get("/api/tiles/imagery/2/9/1").status_code == 404
    assert client.get("/api/tiles/roads/2/1/1").status_code == 422
    assert not upstream


def test_security_headers_and_https_only_rules():
    r = client.get("/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors" in r.headers["content-security-policy"]
    assert "strict-transport-security" not in r.headers          # plain http on localhost
    r = client.get("/", headers={"x-forwarded-proto": "https"})
    assert r.headers["strict-transport-security"].startswith("max-age=")
    assert "upgrade-insecure-requests" in r.headers["content-security-policy"]


def test_csp_allows_the_inline_theme_script():
    for page in ["index.html", "planner/index.html", "404.html"]:
        html = (ROOT / "static" / page).read_text(encoding="utf-8")
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        assert len(scripts) == 1, page
        digest = base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
        assert f"'sha256-{digest}'" in main.CSP, f"{page} inline script changed: update THEME_SCRIPT_HASH"


def test_every_external_script_is_allowed_by_the_csp():
    script_src = next(d for d in main.CSP.split("; ") if d.startswith("script-src"))
    for page in ["index.html", "planner/index.html", "404.html"]:
        html = (ROOT / "static" / page).read_text(encoding="utf-8")
        for src in re.findall(r'<script src="(https://[^/"]+)', html):
            assert src in script_src, f"{page} loads {src}, which the CSP blocks"


def test_robots_and_sitemap_use_the_public_https_address():
    headers = {"x-forwarded-proto": "https", "host": "solar.example"}
    assert "Sitemap: https://solar.example/sitemap.xml" in client.get("/robots.txt", headers=headers).text
    xml = client.get("/sitemap.xml", headers=headers).text
    assert "<loc>https://solar.example/</loc>" in xml and "<loc>https://solar.example/planner/</loc>" in xml


def test_search_console_file(monkeypatch):
    assert client.get("/google0123456789abcdef.html").status_code == 404
    monkeypatch.setenv("GOOGLE_SITE_VERIFICATION", "google0123456789abcdef.html")
    r = client.get("/google0123456789abcdef.html")
    assert r.text == "google-site-verification: google0123456789abcdef.html"
    assert client.get("/googleffffffffffffffff.html").status_code == 404
    assert client.get("/404.html").status_code == 200              # static pages are not shadowed


def test_no_secrets_in_tracked_files():
    patterns = re.compile(r"AAPT[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{30,}|hf_[A-Za-z0-9]{30,}|token=[A-Za-z0-9_\-]{30,}")
    for path in list((ROOT / "static").rglob("*")) + list((ROOT / "app").glob("*.py")):
        if path.suffix in {".html", ".js", ".css", ".py", ".txt", ".json"}:
            assert not patterns.search(path.read_text(encoding="utf-8")), f"possible secret in {path.name}"


def test_blank_label_tile_is_transparent():
    import io
    from PIL import Image
    assert Image.open(io.BytesIO(main.BLANK_PNG)).convert("RGBA").getpixel((0, 0))[3] == 0
