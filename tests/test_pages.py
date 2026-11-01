import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)
STATIC = Path(main.__file__).parent.parent / "static"


def test_landing_page():
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>Rooftop Solar Planner: solar cost and savings for your roof</title>" in r.text
    assert '<meta name="description"' in r.text
    assert 'href="/planner/"' in r.text


def test_planner_page_and_redirect():
    r = client.get("/planner", follow_redirects=False)
    assert r.status_code in (301, 307)
    r = client.get("/planner/")
    assert r.status_code == 200
    assert "<title>Planner | Rooftop Solar Planner</title>" in r.text
    assert "https://pmsuryaghar.gov.in/" in r.text


def test_custom_404_page():
    r = client.get("/does-not-exist")
    assert r.status_code == 404
    assert "Page not found" in r.text


@pytest.mark.parametrize("path", ["/favicon.ico", "/favicon.svg", "/robots.txt", "/assets/img/logo.svg",
                                  "/assets/css/site.css", "/assets/js/site.js", "/assets/js/planner.js"])
def test_static_assets_exist(path):
    assert client.get(path).status_code == 200


def test_every_page_links_to_existing_files():
    for page in ["index.html", "planner/index.html", "404.html"]:
        html = (STATIC / page).read_text(encoding="utf-8")
        for ref in re.findall(r'(?:href|src)="(/[^"#?]*)', html):
            if ref.endswith("/"):
                ref += "index.html"
            assert (STATIC / ref.lstrip("/")).exists(), f"{page} links to missing {ref}"


def test_site_copy_has_no_em_dashes_or_emoji():
    emoji = re.compile("[\u2013\u2014\u2600-\u27bf\U0001f300-\U0001faff]")
    for path in STATIC.rglob("*"):
        if path.suffix in {".html", ".css", ".js"}:
            match = emoji.search(path.read_text(encoding="utf-8"))
            assert match is None, f"{path.name} contains {match.group()!r}"


def test_track_accepts_known_events_and_drops_unknown_utm_keys(capsys):
    r = client.post("/api/track", json={"event": "estimate", "path": "/planner/",
                                        "utm": {"utm_source": "linkedin", "evil": "x"}, "data": {"kwp": 2}})
    assert r.status_code == 204
    out = capsys.readouterr().out
    assert '"utm_source": "linkedin"' in out and "evil" not in out


def test_track_rejects_unknown_events():
    assert client.post("/api/track", json={"event": "drop_tables"}).status_code == 422
