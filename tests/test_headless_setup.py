from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tam.headless_setup import (
    OneTimeSetupToken,
    apply_setup_values,
    build_ssh_tunnel_command,
    setup_bind_address,
    write_headless_config,
)
from tam.release_config import ReleaseConfig, load_release_config


def test_one_time_token_expires_and_is_consumed() -> None:
    session = OneTimeSetupToken.create(now=100.0, lifetime=900.0, token="known-token")
    assert session.verify("known-token", now=999.0)
    assert not session.verify("known-token", now=1001.0)
    assert session.consume("known-token", now=500.0)
    assert not session.verify("known-token", now=501.0)


def test_setup_server_always_binds_loopback() -> None:
    assert setup_bind_address(8849) == ("127.0.0.1", 8849)
    with pytest.raises(ValueError):
        setup_bind_address(70000)


def test_ssh_tunnel_command_contains_loopback_mapping() -> None:
    command = build_ssh_tunnel_command("deploy", "example.com", 8849)
    assert command == "ssh -L 8849:127.0.0.1:8849 deploy@example.com"


def test_apply_setup_values_parses_headless_overrides() -> None:
    config = apply_setup_values(
        ReleaseConfig(),
        {
            "deploy": "server",
            "frontend": "web",
            "port": "9000",
            "api_id": "12345",
            "no_auth": "0",
            "data_dir": "/var/lib/tao",
        },
    )
    assert config.deploy == "server"
    assert config.port == 9000
    assert config.api_id == "12345"
    assert config.data_dir == "/var/lib/tao"


def test_write_headless_config_generates_required_secrets(tmp_path: Path) -> None:
    env = tmp_path / "tao.env"
    config = replace(ReleaseConfig(), deploy="server", data_dir="/var/lib/tao")

    saved = write_headless_config(config, env)

    assert saved.master_key
    assert saved.web_token
    loaded = load_release_config(env)
    assert loaded.deploy == "server"
    assert loaded.data_dir == "/var/lib/tao"
