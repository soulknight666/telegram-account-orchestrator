from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_dockerfile_runs_as_non_root_with_healthcheck() -> None:
    text = _read("Dockerfile")
    assert "FROM python:3.13-slim AS builder" in text
    assert "build-essential" in text
    assert "COPY --from=builder /opt/venv /opt/venv" in text
    assert "/opt/venv/bin/python -m tam.cli fix-opentele" in text
    assert "USER tao" in text
    assert "HEALTHCHECK" in text
    assert "python -m tam.run" in text
    assert "/data" in text and "/config" in text


def test_compose_persists_config_and_data() -> None:
    text = _read("docker-compose.yml")
    assert "ghcr.io/soulknight666/telegram-account-orchestrator" in text
    assert "./config:/config" in text
    assert "./data:/data" in text
    assert "8848:8848" in text
    assert "unless-stopped" in text


def test_systemd_unit_uses_expected_paths_and_hardening() -> None:
    text = _read("deploy/tao.service")
    assert "User=tao" in text
    assert "EnvironmentFile=/etc/tao/tao.env" in text
    assert "WorkingDirectory=/opt/tao" in text
    assert "NoNewPrivileges=true" in text
    assert "ProtectSystem=strict" in text
    assert "ReadWritePaths=/var/lib/tao" in text


def test_install_script_has_all_supported_modes() -> None:
    text = _read("deploy/install.sh")
    assert "--docker" in text
    assert "--systemd" in text
    assert "--upgrade" in text
    assert "python3-venv" in text
    assert "tao setup --headless" in text
    assert "/opt/tao" in text
    assert "/etc/tao" in text
    assert "/var/lib/tao" in text
