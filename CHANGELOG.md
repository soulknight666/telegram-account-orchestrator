# Changelog

All notable changes follow semantic versioning.

## [Unreleased]

### Added - Sprint 3 (2026-08-06)

- **Dependency locking**: `requirements.lock` pins all dependencies to exact versions
- **Unified exception system**: New `tam/exceptions.py` with standard exception classes (TAMError, AccountNotFoundError, SessionError, NetworkError, AuthError, ValidationError, ConfigError, ToolError) and `format_error()` helper
- **Concurrency control**: Added `asyncio.Lock` to Database and AccountManager for safe concurrent operations

### Added - Sprint 2 (2026-08-06)

- **Configurable LLM timeout**: `TAM_LLM_TIMEOUT` environment variable (10-600s, default 120s) controls all AI panel LLM calls
- **Frontend source split**: Separated `tam/web/index.html` (282KB) into `index.src.html` (65KB), `styles.css` (26KB), `app.js` (198KB) with `build.py` script for single-file deployment
- **Integration tests**: New `tests/test_integration.py` with 3 end-to-end tests covering account import, read-only tool execution, and AI tool call chains

### Fixed - Sprint 1 (2026-08-06)

- **Exception handling**: Converted 88 bare `except:` clauses to `except Exception:` with explanatory comments across GAF module (18 files)
- **Security**: Replaced `eval()` with `ast.literal_eval()` in `qingli.py` line 157
- **AI safety**: Added red warning text below AI confirmation toggles emphasizing irreversible operation risks
- **MASTER_KEY validation**: Added length check (≥32 chars) in `config.py` and `setup.py` to prevent encryption failures
- **Test compatibility**: Updated `test_ai_panel.py` mocks to accept `timeout` parameter

## [0.2.2] - 2026-08-02

### Fixed

- Stop treating optional `opentele` as a required Docker build dependency; the built-in tdata parser remains the default path.

## [0.2.1] - 2026-08-02

### Fixed

- Install pytest before GitHub Pages and Windows Release metadata tests.
- Build the Linux container in two stages so `tgcrypto` compiles without adding build tools to the runtime image.
- Apply the Python 3.13 opentele compatibility patch inside the container build.

## [0.2.0] - 2026-08-02

### Added

- Windows 10/11 x64 Tkinter dashboard for configuration, health checks, service control, Web console access, and runtime logs.
- PyInstaller portable distribution and Inno Setup installer definitions using the TAO application icon.
- Headless Linux terminal setup, loopback-only one-time Web setup, Docker Compose deployment, and hardened systemd service assets.
- GitHub Actions automation for Windows/Linux artifacts, GHCR images, SHA-256 checksums, GitHub Releases, and optional Authenticode signing.
- GitHub Pages project landing page with a stable Open Graph image for Telegram link previews.

### Changed

- Promoted the GUI executable and packaged Linux deployment paths as the primary release entry points.
- Bumped package metadata to `0.2.0` for the desktop and Linux distribution release.

## [0.1.0] - 2026-08-02

### Added

- Initial public release of Telegram Account Orchestrator (TAO).
- Web UI, REST API, CLI, Telegram Bot, and MCP interfaces.
- Telethon multi-account management and encrypted session storage.
- Telegram Desktop `tdata` import and account-package utilities.
- GAFBot third-party provenance and MIT license notices.
