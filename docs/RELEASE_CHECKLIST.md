# TAO Release Checklist

## Repository

- [x] Project name is Telegram Account Orchestrator (TAO).
- [x] Repository slug is `telegram-account-orchestrator`.
- [x] Default branch target is `main`.
- [x] MIT License names `soulknight666`.
- [x] GAFBot provenance and MIT notice are included.

## Public-data audit

- [x] `.env`, sessions, `tdata`, databases, proxy lists, ZIP jobs, keys, and local indexes are ignored.
- [x] Public examples use reserved phone numbers and `example.invalid` endpoints.
- [x] `python tools/audit_public_release.py --tracked-only` passes.

## Verification

- [x] `python -m compileall -q tam tests tools`
- [x] `python -m pytest -q tests`
- [x] `python -m pip check`
- [x] `git diff --check`
- [x] Package and installer metadata report version `0.2.0`.

## Distribution artifacts

- `TAO-Windows-x64-Portable.zip`
- `TAO-Windows-x64-Setup.exe`
- `TAO-Linux-x64.tar.gz`
- `SHA256SUMS.txt`
- Container image: `ghcr.io/soulknight666/telegram-account-orchestrator`

## Signing behavior

- Release CI signs the Windows launcher and installer when `SIGNING_CERTIFICATE_BASE64` and `SIGNING_CERTIFICATE_PASSWORD` are configured.
- Without those repository secrets, CI publishes unsigned Windows artifacts; first download may still show Windows SmartScreen because no trusted publisher certificate is attached.
- SHA-256 checksums are generated for every downloadable artifact regardless of signing status.

## Telegram sharing

- Stable Pages URL: `https://soulknight666.github.io/telegram-account-orchestrator/`
- Open Graph image: fixed Raw URL under `docs/assets/preview.png`, without expiring signature parameters.

## GitHub metadata

- Description: `Self-hosted Telegram multi-account management with Web UI, CLI, Bot, MCP, Telethon, and Telegram Desktop tdata import.`
- Topics: `telegram`, `telegram-account-manager`, `telegram-multi-account`, `telethon`, `self-hosted`, `fastapi`, `telegram-bot`, `mcp`, `account-orchestration`
- Security reporting: GitHub Security Advisories
- Current release tag: `v0.2.0`
