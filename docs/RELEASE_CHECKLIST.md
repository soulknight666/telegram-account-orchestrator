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

- [x] `python -m compileall -q tam tests`
- [x] `python -m pytest -q tests`
- [x] `python -m pip check`
- [x] `git diff --check`
- [x] Package metadata reports `telegram-account-orchestrator` version `0.1.0`.

## GitHub metadata

- Description: `Self-hosted Telegram multi-account management with Web UI, CLI, Bot, MCP, Telethon, and Telegram Desktop tdata import.`
- Topics: `telegram`, `telegram-account-manager`, `telegram-multi-account`, `telethon`, `self-hosted`, `fastapi`, `telegram-bot`, `mcp`, `account-orchestration`
- Security reporting: GitHub Security Advisories
- Initial tag: `v0.1.0`
