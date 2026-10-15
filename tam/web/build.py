#!/usr/bin/env python3
"""Build the deterministic single-file page and its strict-CSP app.js bundle."""
from pathlib import Path
import re
import argparse
import base64
import json
import shutil
import subprocess

try:  # supports both ``python -m tam.web.build`` and ``python tam/web/build.py``
    from .csp import with_csp_meta
except ImportError:  # pragma: no cover - script entry point
    from csp import with_csp_meta


_LEX_BLOCK = re.compile(r"/\* TAO_LEX_START \*/.*?/\* TAO_LEX_END \*/\n?", re.DOTALL)


def _skip_template_literal(source: str, start: int) -> int:
    """Return the index after a JS template literal, including nested `${}`."""
    def skip_expr(pos: int) -> int:
        depth = 1
        quote = None
        regex = False
        regex_class = False
        prev_sig = ""
        while pos < len(source):
            c = source[pos]
            n = source[pos + 1] if pos + 1 < len(source) else ""
            if quote:
                if c == "\\":
                    pos += 2
                    continue
                if c == quote:
                    quote = None
                pos += 1
                continue
            if regex:
                if c == "\\":
                    pos += 2
                    continue
                if c == "[":
                    regex_class = True
                elif c == "]":
                    regex_class = False
                elif c == "/" and not regex_class:
                    regex = False
                pos += 1
                continue
            if c in "'\"":
                quote = c
                pos += 1
                continue
            if c == "`":
                pos = _skip_template_literal(source, pos)
                continue
            if c == "/" and prev_sig in "=([{,:;!&|?+-*%^~<>":
                regex = True
                regex_class = False
                pos += 1
                continue
            if c == "{" :
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return pos + 1
            if not c.isspace():
                prev_sig = c
            pos += 1
        return pos

    pos = start + 1
    while pos < len(source):
        c = source[pos]
        if c == "\\":
            pos += 2
            continue
        if c == "`":
            return pos + 1
        if c == "$" and pos + 1 < len(source) and source[pos + 1] == "{":
            pos = skip_expr(pos + 2)
            continue
        pos += 1
    return pos


def _top_level_declarations(source: str) -> tuple[list[str], list[str]]:
    """Find declarations at brace depth zero, ignoring JS literals/comments."""
    consts: list[str] = []
    lets: list[str] = []
    depth = 0
    i = 0
    line_start = True
    quote = None
    comment = None
    regex = False
    regex_class = False
    prev_sig = ""
    while i < len(source):
        c = source[i]
        n = source[i + 1] if i + 1 < len(source) else ""
        if comment == "line":
            if c == "\n":
                comment = None
                line_start = True
            i += 1
            continue
        if comment == "block":
            if c == "*" and n == "/":
                comment = None
                i += 2
            else:
                line_start = c == "\n"
                i += 1
            continue
        if regex:
            if c == "\\":
                i += 2
                continue
            if c == "[":
                regex_class = True
            elif c == "]":
                regex_class = False
            elif c == "/" and not regex_class:
                regex = False
            i += 1
            continue
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            line_start = c == "\n"
            i += 1
            continue
        if c == "/" and n == "/":
            comment = "line"
            i += 2
            continue
        if c == "/" and n == "*":
            comment = "block"
            i += 2
            continue
        # A slash after an expression delimiter starts a regex literal.  This
        # prevents braces in patterns such as /\\{2,\\}/ from corrupting the
        # declaration depth used by the lexical bridge scanner.
        if c == "/" and prev_sig in "=([{,:;!&|?+-*%^~<>":
            regex = True
            regex_class = False
            i += 1
            continue
        if c == "`":
            i = _skip_template_literal(source, i)
            line_start = False
            prev_sig = "`"
            continue
        if c in "'\"":
            quote = c
            i += 1
            line_start = False
            continue
        if c == "{":
            depth += 1
            line_start = False
            prev_sig = c
            i += 1
            continue
        if c == "}":
            depth = max(0, depth - 1)
            line_start = False
            prev_sig = c
            i += 1
            continue
        if depth == 0 and line_start:
            match = re.match(r"(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=", source[i:])
            if match:
                kind = source[i : i + source[i:].find(" ")]
                line = source[i : source.find("\n", i) if source.find("\n", i) >= 0 else len(source)]
                rest = re.sub(r"^(?:const|let)\s+", "", line, count=1)
                names = [m.group(1) for m in re.finditer(r"(?:^|,)\s*([A-Za-z_$][\w$]*)\s*=", rest)]
                (consts if kind == "const" else lets).extend(names or [match.group(1)])
                i += match.end()
                line_start = False
                prev_sig = "="
                continue
        line_start = c == "\n"
        if not c.isspace():
            prev_sig = c
        i += 1
    return list(dict.fromkeys(consts)), list(dict.fromkeys(lets))


def generate_lexical_bridge(source: str) -> str:
    """Add the explicit bridge used by TAO_ACT for top-level const/let names."""
    source = _LEX_BLOCK.sub("", source)
    consts, lets = _top_level_declarations(source)
    consts = [n for n in consts if n not in {"TAO_ACT", "__taoLexHas", "__taoLexGet", "__taoLexSet"}]
    lets = [n for n in lets if n not in {"TAO_ACT", "__taoLexHas", "__taoLexGet", "__taoLexSet"}]
    names = consts + lets
    lines = [
        "/* TAO_LEX_START */",
        "const __taoLexHas = new Set(" + repr(names).replace("'", '"') + ");",
        "function __taoLexGet(n) { switch (n) {",
    ]
    lines.extend(f"  case {name!r}: return {name};" for name in names)
    lines.extend(["} }", "function __taoLexSet(n, v) { switch (n) {"])
    lines.extend(f"  case {name!r}: {name} = v; return true;" for name in lets)
    lines.extend(["} return false; }", "/* TAO_LEX_END */", ""])
    bridge = "\n".join(lines)
    marker = re.search(r"const TAO_ACT\s*=\s*", source)
    if not marker:
        raise RuntimeError("app.js must contain TAO_ACT before it can be built")
    return source[: marker.start()] + bridge + source[marker.start() :]


def render_page(
    src_html: str,
    css: str,
    js: str,
    vendor: str,
    *,
    include_vendor: bool = True,
    apply_lexical_bridge: bool = True,
) -> str:
    if apply_lexical_bridge:
        js = generate_lexical_bridge(js)
    result = src_html.replace("  /* CSS_PLACEHOLDER */\n", css)
    if include_vendor:
        result = result.replace(
            "/* GSAP_PLACEHOLDER */\n",
            "/* GSAP 3.13.0 (vendored; see vendor/GSAP-NOTICE.txt) */\n" + vendor + "\n",
        )
    else:
        # The application uses Web Animations API; the vendored GSAP file is
        # retained for the readable build but is intentionally absent from
        # the release bundle.
        result = result.replace("/* GSAP_PLACEHOLDER */\n", "")
    result = result.replace("/* JS_PLACEHOLDER */\n", js)
    if "PLACEHOLDER" in result:
        raise RuntimeError("web source contains an unresolved build placeholder")
    return with_csp_meta(result)


_BLOCK_RE = re.compile(r"<(script|style)(?P<attrs>[^>]*)>(?P<body>.*?)</\1\s*>", re.I | re.S)


def _minify_css(css: str) -> str:
    """Remove CSS comments and insignificant whitespace without reordering rules."""
    out: list[str] = []
    quote: str | None = None
    comment = False
    pending_space = False
    i = 0
    while i < len(css):
        c = css[i]
        n = css[i + 1] if i + 1 < len(css) else ""
        if comment:
            if c == "*" and n == "/":
                comment = False
                i += 2
                pending_space = True
            else:
                i += 1
            continue
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < len(css):
                out.append(css[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c == "/" and n == "*":
            comment = True
            i += 2
            continue
        if c in "'\"":
            if pending_space and out and out[-1] not in "{;:,>+~" and out[-1] != " ":
                out.append(" ")
            pending_space = False
            quote = c
            out.append(c)
            i += 1
            continue
        if c.isspace():
            pending_space = True
            i += 1
            continue
        if pending_space:
            # Preserve spaces only where CSS token separation matters.  Never
            # alter custom property values or calc() expressions beyond this.
            prev = out[-1] if out else ""
            if prev and prev not in "{;:,>+~" and c not in "};:,>+~":
                out.append(" ")
            pending_space = False
        if c in "{};:,>+~":
            while out and out[-1] == " ":
                out.pop()
        out.append(c)
        i += 1
    return "".join(out).strip()


def _minify_html_shell(html: str) -> str:
    """Strip comments/indentation outside script/style bodies only."""
    parts: list[str] = []
    pos = 0
    for match in _BLOCK_RE.finditer(html):
        outside = html[pos:match.start()]
        outside = re.sub(r"<!--(?!\[if).*?-->", "", outside, flags=re.S)
        outside = re.sub(r"\n[ \t]+", "\n", outside)
        parts.append(outside)
        parts.append(match.group(0))
        pos = match.end()
    outside = html[pos:]
    outside = re.sub(r"<!--(?!\[if).*?-->", "", outside, flags=re.S)
    outside = re.sub(r"\n[ \t]+", "\n", outside)
    parts.append(outside)
    return "".join(parts)


def _terser_minify(js: str) -> str:
    """Compress JS without mangling top-level or function/class names."""
    npx = shutil.which("npx")
    if not npx:
        raise RuntimeError("release build requires npx/terser")
    cmd = [
        npx, "--yes", "terser",
        "--compress",
        "--mangle", "toplevel=false,keep_fnames=true,keep_classnames=true",
        "--format", "comments=false",
    ]
    proc = subprocess.run(
        cmd, input=js, text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode:
        raise RuntimeError(f"terser failed: {proc.stderr.strip()}")
    return proc.stdout.strip() + "\n"


def _minify_inline_blocks(page: str) -> str:
    def replace(match: re.Match[str]) -> str:
        tag, attrs, body = match.group(1), match.group("attrs"), match.group("body")
        if tag.lower() == "style":
            body = _minify_css(body)
        elif "src=" not in attrs.lower():
            body = _terser_minify(body)
        return f"<{tag}{attrs}>{body}</{tag}>"
    return _BLOCK_RE.sub(replace, page)


def _release_metrics(page: str) -> list[dict[str, int | str]]:
    rows: list[dict[str, int | str]] = []
    for index, match in enumerate(re.finditer(r"<(script|style)\b[^>]*>(.*?)</\1\s*>", page, re.I | re.S), 1):
        rows.append({"kind": match.group(1).lower(), "index": index, "bytes": len(match.group(0).encode("utf-8"))})
    for index, match in enumerate(re.finditer(r"(?:src|href)=\"(data:[^\"]+)\"", page, re.I), 1):
        rows.append({"kind": "base64", "index": index, "bytes": len(match.group(1).encode("utf-8"))})
    for index, match in enumerate(re.finditer(r"<svg\\b[^>]*>.*?</svg>", page, re.I | re.S), 1):
        rows.append({"kind": "inline-svg", "index": index, "bytes": len(match.group(0).encode("utf-8"))})
    return sorted(rows, key=lambda row: int(row["bytes"]), reverse=True)


def _externalize_touch_icon(page: str, release_dir: Path, filename: str) -> str:
    """Move the small apple-touch PNG out of the HTML release shell."""
    pattern = re.compile(
        r'(<link\b[^>]*rel="apple-touch-icon"[^>]*href=")'
        r"data:image/png;base64,([^\"]+)(\"[^>]*>)",
        re.I,
    )
    match = pattern.search(page)
    if not match:
        return page
    target = release_dir / filename
    target.write_bytes(base64.b64decode(match.group(2)))
    return page[: match.start()] + match.group(1) + f"/release/{filename}" + match.group(3) + page[match.end() :]


def build_release() -> Path:
    """Build the compressed release page and its same-origin assets.

    The readable ``index.html`` remains untouched.  Release JavaScript and CSS
    are external same-origin assets so the page itself stays small while the
    browser still gets a normal CSP-protected application bundle.
    """
    base = Path(__file__).parent
    src_html = (base / "index.src.html").read_text(encoding="utf-8")
    css = _minify_css((base / "styles.css").read_text(encoding="utf-8"))
    js = _terser_minify(generate_lexical_bridge((base / "app.js").read_text(encoding="utf-8")))
    release_dir = base / "release"
    release_dir.mkdir(exist_ok=True)
    js_name = f"app.min.{__import__('hashlib').sha256(js.encode('utf-8')).hexdigest()[:8]}.js"
    css_name = f"styles.min.{__import__('hashlib').sha256(css.encode('utf-8')).hexdigest()[:8]}.css"
    touch_name = "tao-touch.pending.png"
    for pattern in ("app.min.*.js", "styles.min.*.css", "tao-touch.*.png", "app.min.js", "styles.min.css", "tao-touch.png"):
        for old in release_dir.glob(pattern):
            old.unlink()
    (release_dir / css_name).write_text(css, encoding="utf-8", newline="\n")
    (release_dir / js_name).write_text(js, encoding="utf-8", newline="\n")

    # Keep the shell markup and data-on* attributes in the page, but move the
    # large style/runtime bodies to same-origin assets.  The unused GSAP
    # placeholder is intentionally absent from the release artifact.
    page = src_html.replace(
        "<style>\n  /* CSS_PLACEHOLDER */\n</style>",
        f'<link rel="stylesheet" href="/release/{css_name}">',
    )
    page = page.replace(
        "<script>/* GSAP_PLACEHOLDER */\n/* JS_PLACEHOLDER */\n</script>",
        f'<script src="/release/{js_name}" defer></script>',
    )
    if "PLACEHOLDER" in page:
        raise RuntimeError("release source contains an unresolved build placeholder")
    page = _minify_inline_blocks(page)
    page = _minify_html_shell(page)
    page = _externalize_touch_icon(page, release_dir, touch_name)
    touch_bytes = (release_dir / touch_name).read_bytes()
    touch_hash = __import__('hashlib').sha256(touch_bytes).hexdigest()[:8]
    final_touch_name = f"tao-touch.{touch_hash}.png"
    (release_dir / touch_name).replace(release_dir / final_touch_name)
    page = page.replace(f"/release/{touch_name}", f"/release/{final_touch_name}")
    # Recompute CSP after every byte-changing step, including Terser output.
    page = with_csp_meta(page)
    out = base / "index.min.html"
    out.write_text(page, encoding="utf-8", newline="\n")
    metrics = _release_metrics(page)
    metrics.extend(
        {"kind": "asset", "path": f"release/{asset.name}", "bytes": asset.stat().st_size}
        for asset in release_dir.iterdir()
        if asset.is_file()
    )
    metrics = sorted(metrics, key=lambda row: int(row["bytes"]), reverse=True)
    page_gzip = len(__import__("gzip").compress(page.encode("utf-8"), compresslevel=9))
    print(json.dumps({
        "output": str(out),
        "bytes": out.stat().st_size,
        "gzip_bytes": page_gzip,
        "top10": metrics[:10],
    }, ensure_ascii=False, indent=2))
    if out.stat().st_size >= 500 * 1024:
        raise RuntimeError(f"release page exceeds 500 KB: {out.stat().st_size}")
    if page_gzip >= 150 * 1024:
        raise RuntimeError("release gzip exceeds 150 KB")
    return out

def build():
    base = Path(__file__).parent
    src_html = (base / "index.src.html").read_text(encoding="utf-8")
    css = (base / "styles.css").read_text(encoding="utf-8")
    js = (base / "app.js").read_text(encoding="utf-8")
    vendor = "\n".join(line.rstrip() for line in (base / "vendor" / "gsap-3.13.0.min.js").read_text(encoding="utf-8").splitlines()).strip()

    js = generate_lexical_bridge(js)
    (base / "app.js").write_text(js, encoding="utf-8")
    result = render_page(src_html, css, js, vendor)

    out = base / "index.html"
    out.write_text(result, encoding="utf-8")
    print(f"Build complete: {out} ({len(result):,} chars)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true", help="write index.min.html without the unused GSAP vendor")
    args = parser.parse_args()
    if args.release:
        build_release()
    else:
        build()
