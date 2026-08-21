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
