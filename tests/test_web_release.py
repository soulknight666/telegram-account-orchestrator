"""Release artifact contracts: size, CSP, debug switch and cache semantics."""
import base64
import gzip
import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "tam" / "web"


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.policy = None
        self.events = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.events.extend(name for name, _ in attrs if re.fullmatch(r"on[a-z]+", name))
        if tag == "meta" and values.get("http-equiv", "").lower() == "content-security-policy":
            self.policy = values["content"]


def _parse(page: str) -> _Page:
    out = _Page()
    out.feed(page)
    return out


def _asset(page: str, kind: str) -> tuple[str, Path]:
    if kind == "app":
        pattern = r'<script\s+src="/release/(app\.min\.[0-9a-f]{8}\.js)"'
    elif kind == "css":
        pattern = r'<link\s+rel="stylesheet"\s+href="/release/(styles\.min\.[0-9a-f]{8}\.css)"'
    else:
        pattern = r'<link\s+rel="apple-touch-icon"\s+href="/release/(tao-touch\.[0-9a-f]{8}\.png)"'
    match = re.search(pattern, page)
    assert match, f"hashed {kind} asset reference missing"
    name = match.group(1)
    return name, WEB / "release" / name


def test_release_page_and_gzip_are_under_limits_and_vendor_is_absent():
    page_path = WEB / "index.min.html"
    page = page_path.read_text(encoding="utf-8")
    app_name, app_path = _asset(page, "app")
    css_name, css_path = _asset(page, "css")
    touch_name, touch_path = _asset(page, "touch")
    app = app_path.read_text(encoding="utf-8")
    assert page_path.stat().st_size < 500 * 1024
    assert len(gzip.compress(page.encode("utf-8"), compresslevel=9)) < 150 * 1024
    assert app_path.is_file() and css_path.is_file() and touch_path.is_file()
    assert hashlib.sha256(app_path.read_bytes()).hexdigest()[:8] == app_name[8:16]
    assert hashlib.sha256(css_path.read_bytes()).hexdigest()[:8] == css_name[11:19]
    assert hashlib.sha256(touch_path.read_bytes()).hexdigest()[:8] == touch_name[10:18]
    assert not list((WEB / "release").glob("app.min.js"))
    assert not list((WEB / "release").glob("styles.min.css"))
    assert "GSAP_PLACEHOLDER" not in page
    assert "ScrollTrigger" not in page
    assert "gsap(" not in page.lower()
    assert "ScrollTrigger" not in app
    assert not re.search(r"\bgsap\s*\.", app, re.I)
    assert _parse(page).events == []


def test_release_csp_hashes_match_inline_scripts():
    page = (WEB / "index.min.html").read_text(encoding="utf-8")
    parsed = _parse(page)
    assert parsed.policy
    scripts = re.findall(r"<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script\s*>", page, re.I | re.S)
    hashes = [
        "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"
        for body in scripts
    ]
    assert re.findall(r"'sha256-[^']+'", parsed.policy) == hashes
    assert "'unsafe-eval'" not in parsed.policy


def test_default_root_serves_release_and_debug_switch_serves_readable(monkeypatch):
    from tam.api import app

    monkeypatch.delenv("TAM_WEB_DEBUG", raising=False)
    client = TestClient(app)
    release = client.get("/")
    assert release.status_code == 200
    app_name, _ = _asset(release.text, "app")
    assert f"/release/{app_name}" in release.text
    monkeypatch.setenv("TAM_WEB_DEBUG", "1")
    readable = TestClient(app).get("/")
    assert readable.status_code == 200
    assert "/* JS_PLACEHOLDER */" not in readable.text
    assert "const $ =" in readable.text


def test_release_assets_are_same_origin_and_gzip_middleware_is_active():
    from tam.api import app

    client = TestClient(app)
    response = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert response.headers.get("content-encoding") == "gzip"
    page = client.get("/").text
    for kind in ("app", "css", "touch"):
        name, _ = _asset(page, kind)
        asset = client.get(f"/release/{name}")
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
