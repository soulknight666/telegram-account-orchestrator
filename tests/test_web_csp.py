"""The shipped page, lexical bridge and CSP must be one deterministic artifact."""
import base64
import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "tam" / "web"


class Attributes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.events = []
        self.policy = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.events.extend(name for name, _ in attrs if re.fullmatch(r"on[a-z]+", name))
        if tag == "meta" and values.get("http-equiv", "").lower() == "content-security-policy":
            self.policy = values["content"]


def parse_page(page):
    parsed = Attributes()
    parsed.feed(page)
    return parsed


def test_generated_page_has_no_inline_events_and_exact_script_hashes():
    page = (WEB / "index.html").read_text(encoding="utf-8")
    parsed = parse_page(page)
    assert parsed.events == []
    assert parsed.policy is not None
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script\s*>", page, re.S | re.I)
    hashes = ["'sha256-" + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() + "'" for s in scripts]
    assert len(scripts) >= 2
    assert re.findall(r"'sha256-[^']+'", parsed.policy) == hashes
    script_src = next(part.strip() for part in parsed.policy.split(";") if part.strip().startswith("script-src "))
    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in parsed.policy
    assert "frame-ancestors" not in parsed.policy
    assert "connect-src 'self'" in parsed.policy
    assert "object-src 'none'" in parsed.policy


def test_csp_normalizes_crlf_and_supports_external_app_script():
    from tam.web.csp import content_security_policy, with_csp_meta

    inline = "\nconst theme = 'dark';\n"
    external = '<meta charset="utf-8"><script>' + inline + '</script><script src="app.js"></script>'
    policy = content_security_policy(external)
    assert policy == content_security_policy(external.replace("\n", "\r\n"))
    assert len(re.findall(r"'sha256-[^']+'", policy)) == 1
    assert "script-src 'self'" in policy
    output = with_csp_meta(external)
    assert with_csp_meta(output) == output
    assert parse_page(output).policy == policy


def test_build_reproduces_shipped_page_and_external_lexical_bridge():
    from tam.web.build import render_page, generate_lexical_bridge

    src = (WEB / "index.src.html").read_text(encoding="utf-8")
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    vendor = "\n".join(line.rstrip() for line in (WEB / "vendor" / "gsap-3.13.0.min.js").read_text(encoding="utf-8").splitlines()).strip()
    assert render_page(src, css, js, vendor) == (WEB / "index.html").read_text(encoding="utf-8")
    assert generate_lexical_bridge(js) == js
    assert js.index("function __taoLexGet") < js.index("const TAO_ACT =")


def test_lexical_bridge_includes_all_top_level_declarators_not_nested_locals():
    from tam.web.build import generate_lexical_bridge

    js = '''const check = () => 1, save = () => 2;
let accounts = [], readonly = false, tableLimit = 300;
function inner() {
const secretLocal = 4;
}
/* TAO_LEX_START */
/* TAO_LEX_END */
const TAO_ACT = (() => {});
'''
    output = generate_lexical_bridge(js)
    bridge = output.split("/* TAO_LEX_START */", 1)[1].split("/* TAO_LEX_END */", 1)[0]
    for name in ("check", "save", "accounts", "readonly", "tableLimit"):
        assert f"case '{name}': return {name};" in bridge
    assert "secretLocal" not in bridge
    assert "case 'tableLimit': tableLimit = v; return true;" in bridge
    assert "case 'check': check =" not in bridge
    assert generate_lexical_bridge(output) == output


def test_root_page_http_csp_matches_meta_and_blocks_framing():
    from tam.api import app

    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert re.search(r"/release/app\.min\.[0-9a-f]{8}\.js", response.text)
    meta = parse_page(response.text).policy
    assert meta
    assert response.headers["content-security-policy"] == meta + "; frame-ancestors 'none'"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["etag"].startswith('"')
    cached = TestClient(app).get("/", headers={"If-None-Match": response.headers["etag"]})
    assert cached.status_code == 304
    assert cached.headers["etag"] == response.headers["etag"]
