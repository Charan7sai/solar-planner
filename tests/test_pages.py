import re
from html.parser import HTMLParser
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
    assert "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2010674" in r.text
    assert "DISCLAIMER:" in r.text


def test_custom_404_page():
    r = client.get("/does-not-exist")
    assert r.status_code == 404
    assert "Page not found" in r.text


@pytest.mark.parametrize("path", ["/favicon.ico", "/favicon-32.png", "/icon-192.png", "/apple-touch-icon.png",
                                  "/robots.txt", "/assets/img/logo.png", "/assets/css/site.css",
                                  "/assets/js/site.js", "/assets/js/planner.js"])
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


class _VisibleText(HTMLParser):
    """Collects text a visitor reads: page text plus alt, title, placeholder, aria-label and descriptions."""
    ATTRS = {"alt", "title", "placeholder", "aria-label"}

    def __init__(self):
        super().__init__()
        self.skip = 0
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        for key in self.ATTRS & attrs.keys():
            self.chunks.append(attrs[key] or "")
        if tag == "meta" and attrs.get("name") == "description":
            self.chunks.append(attrs.get("content") or "")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.chunks.append(data)


def test_site_copy_has_no_hyphenated_words():
    hyphenated = re.compile(r"\b[A-Za-z0-9]+(?:-[A-Za-z]+)+\b")
    allowed: set[str] = set()
    for page in ["index.html", "planner/index.html", "404.html"]:
        parser = _VisibleText()
        parser.feed((STATIC / page).read_text(encoding="utf-8"))
        words = set(hyphenated.findall(" ".join(parser.chunks))) - allowed
        assert not words, f"{page} has hyphenated words: {sorted(words)}"
    script = (STATIC / "assets/js/planner.js").read_text(encoding="utf-8")
    messages = re.findall(r'"([^"\n]* [^"\n]*)"', script)
    words = {w for m in messages if m[:1].isupper() for w in hyphenated.findall(m)}  # sentences, not CSS classes
    assert not words, f"planner.js messages have hyphenated words: {sorted(words)}"


def test_blocked_scheme_portal_is_not_linked():
    for page in ["index.html", "planner/index.html", "404.html"]:
        assert "pmsuryaghar.gov.in" not in (STATIC / page).read_text(encoding="utf-8")
