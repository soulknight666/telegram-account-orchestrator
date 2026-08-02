from __future__ import annotations

from dataclasses import replace

from tam.launcher import (
    build_config_from_fields,
    display_fields,
    smoke_test_payload,
    web_console_url,
)
from tam.release_config import ReleaseConfig


def test_display_fields_masks_sensitive_values() -> None:
    config = replace(
        ReleaseConfig(),
        api_hash="0123456789abcdef",
        bot_token="123456789:telegram-bot-token",
        web_token="web-token-123456",
        master_key="master-key-123456",
    )

    masked = display_fields(config)
    visible = display_fields(config, reveal_secrets=True)

    assert masked["api_hash"] != config.api_hash
    assert masked["bot_token"] != config.bot_token
    assert visible["api_hash"] == config.api_hash
    assert visible["master_key"] == config.master_key


def test_build_config_from_fields_parses_numbers_and_preserves_masked_secrets() -> None:
    base = replace(ReleaseConfig(), api_hash="existing-hash", web_token="existing-token")
    fields = display_fields(base)
    fields.update({"port": "9900", "workers": "8", "batch_concurrency": "6", "no_auth": "0"})

    config = build_config_from_fields(fields, base)

    assert config.port == 9900
    assert config.workers == 8
    assert config.batch_concurrency == 6
    assert config.api_hash == "existing-hash"
    assert config.web_token == "existing-token"


def test_web_console_url_uses_loopback_for_wildcard_hosts() -> None:
    assert web_console_url(replace(ReleaseConfig(), host="0.0.0.0", port=9000)) == "http://127.0.0.1:9000"
    assert web_console_url(replace(ReleaseConfig(), host="192.168.1.2", port=8848)) == "http://192.168.1.2:8848"


def test_smoke_test_payload_has_packaging_contract() -> None:
    payload = smoke_test_payload()
    assert payload["app"] == "Telegram Account Orchestrator"
    assert payload["short_name"] == "TAO"
    assert payload["tkinter"] is True
    assert payload["icon_exists"] is True
