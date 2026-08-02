# Changelog

All notable changes follow semantic versioning.

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
