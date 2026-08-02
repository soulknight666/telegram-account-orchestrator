from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_pages_workflow_deploys_static_site_with_oidc_permissions() -> None:
    content = workflow("pages.yml")

    assert "python -m pip install pytest" in content
    assert content.index("python -m pip install pytest") < content.index(
        "python -m pytest -q tests/test_social_preview.py"
    )
    assert "actions/configure-pages@v5" in content
    assert "actions/upload-pages-artifact@v3" in content
    assert "actions/deploy-pages@v4" in content
    assert "path: site" in content
    assert "pages: write" in content
    assert "id-token: write" in content
    assert "github-pages" in content


def test_release_workflow_builds_all_public_artifacts() -> None:
    content = workflow("release.yml")

    assert "windows-latest" in content
    assert "ubuntu-latest" in content
    assert '".[bot,dev,release]"' in content
    assert "packaging/tao-launcher.spec" in content
    assert "packaging/tao.iss" in content
    assert "python -m tam.cli fix-opentele" in content
    assert content.index("python -m tam.cli fix-opentele") < content.index("pyinstaller --clean")
    assert "TAO-Windows-x64-Portable.zip" in content
    assert "TAO-Windows-x64-Setup.exe" in content
    assert "TAO-Linux-x64.tar.gz" in content
    assert "SHA256SUMS.txt" in content
    assert "tools/verify_release_artifacts.py" in content


def test_release_workflow_scans_signs_publishes_and_releases() -> None:
    content = workflow("release.yml")

    assert "gitleaks/gitleaks-action@v2" in content
    assert "SIGNING_CERTIFICATE_BASE64" in content
    assert "SIGNING_CERTIFICATE_PASSWORD" in content
    assert "signtool" in content.lower()
    assert "docker/build-push-action@v6" in content
    assert "ghcr.io/soulknight666/telegram-account-orchestrator" in content
    assert "softprops/action-gh-release@v2" in content


def test_ci_compiles_tools_and_checks_release_metadata() -> None:
    content = workflow("ci.yml")

    assert "python -m compileall -q tam tests tools" in content
    assert "tests/test_social_preview.py" in content
    assert "tests/test_workflows.py" in content
    assert "tests/test_packaging_assets.py" in content
