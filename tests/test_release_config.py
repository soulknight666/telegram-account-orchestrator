from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tam.release_config import (
    ReleaseConfig,
    ensure_release_secrets,
    load_release_config,
    mask_secret,
    save_release_config,
    validate_release_config,
)


def test_load_release_config_parses_defaults_and_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "TAM_DEPLOY=server\n"
        "TAM_FRONTEND=both\n"
        "TAM_PORT=9001\n"
        "TAM_API_ID=12345\n"
        "TAM_NO_AUTH=0\n"
        "TAM_WORKERS=8\n",
        encoding="utf-8",
    )

    config = load_release_config(env)

    assert config.deploy == "server"
    assert config.frontend == "both"
    assert config.port == 9001
    assert config.api_id == "12345"
    assert config.workers == 8
    assert config.batch_concurrency == 3
    assert config.log_level == "info"


def test_ensure_release_secrets_is_idempotent() -> None:
    initial = ReleaseConfig(master_key="", web_token="")
    generated = ensure_release_secrets(initial)

    assert len(generated.master_key) >= 40
    assert len(generated.web_token) >= 24
    assert ensure_release_secrets(generated) == generated


def test_validation_reports_fields_and_server_rules() -> None:
    config = ReleaseConfig(
        deploy="server",
        frontend="bot",
        port=70000,
        no_auth=True,
        web_token="",
        bot_token="",
    )

    issues = validate_release_config(config)
    fields = {issue.field for issue in issues if issue.severity == "error"}

    assert {"port", "no_auth", "web_token", "bot_token"} <= fields


def test_local_web_defaults_are_valid_after_secret_generation() -> None:
    config = ensure_release_secrets(ReleaseConfig())
    assert not [i for i in validate_release_config(config) if i.severity == "error"]


def test_mask_secret_keeps_only_edges() -> None:
    assert mask_secret("") == ""
    assert mask_secret("short") == "*****"
    assert mask_secret("abcdefghijk") == "abcd***hijk"


def test_save_release_config_preserves_unknown_lines_and_creates_backup(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# keep this\nCUSTOM_VALUE=keep\nTAM_PORT=8848\n", encoding="utf-8")
    config = ensure_release_secrets(
        replace(ReleaseConfig(), port=9900, api_hash="hash-value", data_dir="./runtime-data")
    )

    save_release_config(config, env)

    text = env.read_text(encoding="utf-8")
    backup = env.with_suffix(env.suffix + ".bak")
    assert "# keep this" in text
    assert "CUSTOM_VALUE=keep" in text
    assert "TAM_PORT=9900" in text
    assert "TAM_API_HASH=hash-value" in text
    assert "TAM_DATA_DIR=./runtime-data" in text
    assert backup.read_text(encoding="utf-8") == "# keep this\nCUSTOM_VALUE=keep\nTAM_PORT=8848\n"

    loaded = load_release_config(env)
    assert loaded.port == 9900
    assert loaded.master_key == config.master_key
    assert loaded.web_token == config.web_token
