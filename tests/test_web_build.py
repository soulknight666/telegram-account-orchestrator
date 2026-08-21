from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "tam" / "web"


def test_web_sources_have_single_complete_javascript_entrypoint() -> None:
    source = (WEB / "index.src.html").read_text(encoding="utf-8")
    javascript = (WEB / "app.js").read_text(encoding="utf-8")

    assert source.count("/* JS_PLACEHOLDER */") == 1
    assert "JS_PLACEHOLDER_1" not in source
    assert "JS_PLACEHOLDER_2" not in source
    assert javascript.lstrip().startswith("const $ =")
    assert "</script>" not in javascript
    assert "</body>" not in javascript


def test_generated_web_page_contains_complete_sources() -> None:
    source = (WEB / "index.src.html").read_text(encoding="utf-8")
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    javascript = (WEB / "app.js").read_text(encoding="utf-8")
    generated = (WEB / "index.html").read_text(encoding="utf-8")

    expected = source.replace("  /* CSS_PLACEHOLDER */\n", css)
    expected = expected.replace("/* JS_PLACEHOLDER */\n", javascript)

    assert generated == expected
    assert "CSS_PLACEHOLDER" not in generated
    assert "JS_PLACEHOLDER" not in generated


def test_web_javascript_is_valid() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    subprocess.run(
        [node, "--check", str(WEB / "app.js")],
        check=True,
        capture_output=True,
        text=True,
    )


def test_login_button_only_opens_the_login_dialog() -> None:
    javascript = (WEB / "app.js").read_text(encoding="utf-8")
    match = re.search(
        r"async function startLogin\(id\) \{(?P<body>.*?)\n\}",
        javascript,
        re.S,
    )
    assert match is not None
    assert "/login/code" not in match.group("body")


def test_login_dialog_owns_manual_and_automatic_code_actions() -> None:
    source = (WEB / "index.src.html").read_text(encoding="utf-8")
    javascript = (WEB / "app.js").read_text(encoding="utf-8")

    assert 'id="lSendBtn"' in source
    assert 'id="lAutoBtn"' in source
    assert "onclick=\"autoLoginFromModal()\"" in source
    assert "onclick=\"autoLogin(${a.id})\"" not in javascript
    assert "`/api/accounts/${id}/login/auto`" in javascript


def test_import_preview_normalizes_markdown_table_cells() -> None:
    javascript = (WEB / "app.js").read_text(encoding="utf-8")

    assert "function cleanImportPart" in javascript
    assert "isMarkdownTableMeta" in javascript
    assert "digitCount >= 7 && digitCount <= 15" in javascript


def test_code_source_probe_is_available_in_account_and_import_forms() -> None:
    source = (WEB / "index.src.html").read_text(encoding="utf-8")
    javascript = (WEB / "app.js").read_text(encoding="utf-8")

    assert 'id="aCodeTest"' in source
    assert 'id="iCodeTest"' in source
    assert 'id="eCodeTest"' in javascript
    assert "async function testCodeSource" in javascript
    assert "/api/code-sources/probe" in javascript
