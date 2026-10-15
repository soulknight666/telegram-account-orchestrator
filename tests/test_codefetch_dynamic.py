import asyncio

from tam import codefetch


def test_json_metadata_is_not_mistaken_for_a_login_code() -> None:
    raw = '{"success":true,"changed":true,"code_id":12345}'
    assert codefetch.extract_code(raw) is None


def test_dynamic_monitor_urls_stay_on_the_source_origin() -> None:
    source = "https://example.test/client-login/login?linkToken=test-token"
    page = """
    <script>
      const latest = "/client-login/latest-code?linkToken=";
      const restart = "/client-login/restart-monitor?linkToken=";
    </script>
    """

    assert hasattr(codefetch, "_monitor_urls")
    urls = codefetch._monitor_urls(source, page)

    assert urls == (
        "https://example.test/client-login/latest-code?linkToken=test-token",
        "https://example.test/client-login/restart-monitor?linkToken=test-token",
    )


def test_unrecognized_page_has_no_monitor_endpoints() -> None:
    assert hasattr(codefetch, "_monitor_urls")
    assert codefetch._monitor_urls("https://example.test/code", "<p>code</p>") is None


def test_prepare_code_source_starts_dynamic_monitor(monkeypatch) -> None:
    source = "https://example.test/client-login/login?linkToken=test-token"
    page = """
    <script>
      const latest = "/client-login/latest-code?linkToken=";
      const restart = "/client-login/restart-monitor?linkToken=";
    </script>
    """
    calls = []

    def fake_fetch(url, timeout=15.0, proxy=None, *, method="GET", headers=None):
        calls.append((url, method, headers or {}))
        if method == "POST":
            return '{"success":true,"poll_seconds":60}'
        return page

    monkeypatch.setattr(codefetch, "fetch_text", fake_fetch)
    assert hasattr(codefetch, "prepare_code_source")

    result = asyncio.run(codefetch.prepare_code_source(source))

    assert result["prepared"] is True
    assert calls[0][1] == "GET"
    assert calls[1][0].endswith("/client-login/restart-monitor?linkToken=test-token")
    assert calls[1][1] == "POST"
    assert calls[1][2]["X-Requested-With"] == "manual-start"


def test_provider_registry_detects_dynamic_and_generic_sources() -> None:
    assert hasattr(codefetch, "detect_provider")
    dynamic_page = """
    <script>
      const latest = "/client-login/latest-code?linkToken=";
      const restart = "/client-login/restart-monitor?linkToken=";
    </script>
    """

    dynamic = codefetch.detect_provider(
        "https://example.test/client-login/login?linkToken=test-token",
        dynamic_page,
    )
    generic = codefetch.detect_provider("https://example.test/code", '{"code":"12345"}')

    assert dynamic.name == "tghao"
    assert dynamic.requires_prepare is True
    assert generic.name == "generic"
    assert generic.requires_prepare is False


def test_probe_reports_capability_without_returning_the_code(monkeypatch) -> None:
    monkeypatch.setattr(
        codefetch,
        "fetch_text",
        lambda url, timeout=15.0, proxy=None, **kwargs: '{"code":"12345"}',
    )
    assert hasattr(codefetch, "probe_code_source")

    result = asyncio.run(codefetch.probe_code_source("https://example.test/code"))

    assert result == {
        "ok": True,
        "provider": "generic",
        "provider_label": "通用直链",
        "response_type": "json",
        "code_present": True,
        "requires_prepare": False,
    }
    assert "code" not in result


def test_provider_catalog_is_extensible() -> None:
    assert hasattr(codefetch, "list_providers")
    catalog = codefetch.list_providers()
    assert [item["name"] for item in catalog] == ["tghao", "generic"]
from tam.importer import parse_line
from tam.codefetch import extract_code

def test_feijige_phone_dash_delimiter_and_bracket_url():
    item = parse_line('+12025550123----[https://tgapi.example.test/uuid/GetHTML')
    assert item.phone == '+12025550123'
    assert item.code_url == 'https://tgapi.example.test/uuid/GetHTML'

def test_extract_code_from_feijige_input_value():
    html = '<input id="code" type="text" value="48210" readonly>'
    assert extract_code(html) == '48210'

def test_wait_for_code_defaults_to_fast_polling():
    import inspect
    from tam.codefetch import wait_for_code
    assert inspect.signature(wait_for_code).parameters['interval'].default <= 2.0


def test_input_attributes_can_be_reordered():
    assert extract_code('<input value="48210" readonly id="code">') == '48210'
    assert extract_code('<input value="123456" id="pass2fa">') is None
    assert extract_code('<input value=48210 name=otp>') == '48210'


def test_poll_ignores_old_input_code(monkeypatch):
    pages = iter(['<input id="code" value="11111">',
                  '<input value="22222" id="code">'])
    monkeypatch.setattr(codefetch, 'fetch_text', lambda *a, **k: next(pages))
    result = asyncio.run(codefetch.wait_for_code(
        'https://example.test/GetHTML', exclude='11111', interval=0))
    assert result == '22222'


def test_gethtml_prepare_does_not_request_page(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Preparation must not consume the refresh quota")

    monkeypatch.setattr(codefetch, "fetch_text", forbidden)
    result = asyncio.run(codefetch.prepare_code_source(
        "https://example.test/sample/GetHTML?sample=1"))
    assert result == {"prepared": False, "kind": "generic", "baseline": None,
                      "pre_read_skipped": True}
