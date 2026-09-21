from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "tam" / "web"


def test_login_detail_recovers_and_refreshes_accounts():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    block = js[js.index("let loginDetailTimer ="):js.index("async function stopTask(")]
    harness = r'''
const assert = require('node:assert/strict');
let timer, delay, refreshed = 0, requests = 0, logPinned;
const element = {style:{}, textContent:''};
const $ = () => element;
const TASK_KIND_CN = {}, fmt = String;
const clearTimeout = () => {};
const setTimeout = (fn, ms) => {timer = fn; delay = ms; return 1;};
const refreshLoginAccounts = async () => {refreshed++;};
const api = async () => {
  requests++;
  if (requests === 1) throw Error('offline');
  return {id:1, kind:'auto_login', live:requests === 2, targets:[]};
};
'''
    checks = r'''
(async () => {
  await taskDetail(1);
  assert.equal(delay, 3000);
  await timer();
  assert.equal(delay, 1000);
  await timer();
  assert.equal(refreshed, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
'''
    subprocess.run([node, "-e", harness + block + checks], check=True, capture_output=True, text=True)


def test_import_preview_accepts_dash_separated_code_urls() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    javascript = (WEB / "app.js").read_text(encoding="utf-8")
    parser = javascript[javascript.index("function cleanImportPart("):
                        javascript.index("function previewImport(")]
    subprocess.run([node, "-e", parser + r'''
const assert = require('node:assert/strict');
const url = 'https://example.test/sample/GetHTML';
for (const value of [url, '[' + url, '[' + url + ']']) {
  const result = parseLines('+12025550123----' + value);
  assert.deepEqual(result, {ok: [{phone: '+12025550123', url, label: null}], bad: []});
}
'''], check=True, capture_output=True, text=True)


def test_batch_auto_login_uses_configured_concurrency() -> None:
    javascript = (WEB / "app.js").read_text(encoding="utf-8")
    body = javascript.split("if (kind === 'auto') {", 1)[1].split("if (kind === 'relogin')", 1)[0]
    assert "const concurrency = batchConc(3)" in body
    assert "submitLoginTask({account_ids:ids" in body
    assert "...timing, concurrency}" in body
    assert "runSerial" not in body


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
    assert "'/api/tasks/login'" in javascript


def test_batch_login_collects_twofa_and_supports_logout_relogin() -> None:
    source = (WEB / "index.src.html").read_text(encoding="utf-8")
    javascript = (WEB / "app.js").read_text(encoding="utf-8")

    assert 'onclick="bulk(\'relogin\')"' in source
    assert "统一两步验证密码" in javascript
    assert "password: true" in javascript
    assert "relogin:true" in javascript
    assert "password:password || null" in javascript
    assert "const concurrency = batchConc(3)" in javascript
    assert "concurrency:concurrency" in javascript


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


def test_hot_reload_waits_for_a_new_process_instance() -> None:
    javascript = (WEB / "app.js").read_text(encoding="utf-8")

    assert "async function waitForRestart(previousInstanceId)" in javascript
    assert "'/api/system/status'" in javascript
    assert "status.instance_id !== previousInstanceId" in javascript
    assert "fetch('/api/stats'" not in re.search(
        r"async function hotReload\(\) \{(?P<body>.*?)\n\}\n\nasync function waitForRestart",
        javascript,
        re.S,
    ).group("body")


def test_layout_shell_contract_is_present() -> None:
    source = (WEB / "index.src.html").read_text(encoding="utf-8")
    assert 'id="appShell"' in source
    assert 'id="sideNav"' in source
    assert 'id="workspace"' in source
    assert 'id="accountDetailDrawer"' in source
    assert 'data-nav="accounts"' in source
    assert 'data-nav="tasks"' in source
    assert source.count("/* CSS_PLACEHOLDER */") == 1
    assert source.count("/* JS_PLACEHOLDER */") == 1
