from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_pyinstaller_spec_uses_windowed_onedir_and_assets() -> None:
    text = _read("packaging/tao-launcher.spec")
    assert "ROOT = Path(SPECPATH).parent\n" in text
    assert "console=False" in text
    assert "TAO-Launcher" in text
    assert "tam/web" in text
    assert "tam/assets" in text
    assert "tao.ico" in text
    assert "COLLECT" in text


def test_inno_setup_uses_expected_artifact_name_and_icon() -> None:
    text = _read("packaging/tao.iss")
    assert "TAO-Windows-x64-Setup" in text
    assert "TAO-Launcher.exe" in text
    assert "SetupIconFile" in text
    assert "ArchitecturesAllowed=x64compatible" in text


def test_branding_builder_declares_required_icon_sizes() -> None:
    text = _read("tools/build_branding.py")
    for size in (16, 24, 32, 48, 64, 128, 256):
        assert str(size) in text
    assert "ImageOps.fit" in text


def test_artifact_verifier_knows_release_contract() -> None:
    text = _read("tools/verify_release_artifacts.py")
    assert "TAO-Windows-x64-Portable.zip" in text
    assert "TAO-Windows-x64-Setup.exe" in text
    assert "TAO-Linux-x64.tar.gz" in text
    assert "SHA256SUMS.txt" in text


def test_package_and_installer_versions_match() -> None:
    project = tomllib.loads(_read("pyproject.toml"))
    installer = _read("packaging/tao.iss")
    match = re.search(r'#define AppVersion "([^"]+)"', installer)

    assert match is not None
    assert project["project"]["version"] == match.group(1)


def test_local_release_build_outputs_are_ignored() -> None:
    ignored = set(_read(".gitignore").splitlines())

    assert "build/" in ignored
    assert "dist/" in ignored
    assert "release/" in ignored
