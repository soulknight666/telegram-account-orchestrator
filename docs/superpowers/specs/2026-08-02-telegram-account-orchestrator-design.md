# Telegram Account Orchestrator Open-Source Release Design

> **Status:** Approved design
>
> **Repository:** `soulknight666/telegram-account-orchestrator`

## Goal

Prepare the current Telegram account management project for a public GitHub release under the project identity **Telegram Account Orchestrator (TAO)** while preserving the existing feature set and the internal `tam` Python package name.

## Product identity and SEO

- Repository slug: `telegram-account-orchestrator`
- Display name: `Telegram Account Orchestrator (TAO)`
- Internal Python package: `tam` (kept unchanged to avoid unnecessary import and startup breakage)
- Primary description: `Self-hosted Telegram multi-account management with Web UI, CLI, Bot, MCP, Telethon, and Telegram Desktop tdata import.`
- Recommended topics: `telegram`, `telegram-account-manager`, `telegram-multi-account`, `telethon`, `self-hosted`, `fastapi`, `telegram-bot`, `mcp`, `account-orchestration`

The README title, first paragraph, HTML page title, package metadata, and GitHub description will use the same wording so that the exact search terms remain discoverable while the unique `orchestrator` name distinguishes the project.

## Scope

The public release keeps the current functional surface:

- Web control panel and REST API
- CLI and startup scripts
- Telegram Bot frontend
- MCP / AI tool interface
- Telethon account/session management
- Telegram Desktop `tdata` import
- ZIP tools and the existing `tam/gaf/` feature set
- Payment/VIP integration code, clearly labeled as optional and configuration-dependent

The release does not perform a broad architectural rewrite. Changes stay focused on repository hygiene, packaging, documentation, provenance, configuration safety, and testability.

## Repository structure

The整理 pass will establish a normal Git repository and add:

- `LICENSE` — MIT License, copyright `2026 soulknight666`
- `CONTRIBUTING.md` — local setup, test commands, change expectations
- `SECURITY.md` — secret handling, session data handling, and vulnerability reporting
- `CHANGELOG.md` — initial release notes and future versioning format
- `.github/workflows/ci.yml` — compile, unit-test, and import checks
- `pyproject.toml` — package metadata, supported Python versions, test configuration, and optional dependencies
- `docs/` — focused architecture, deployment, and GAFBot provenance notes

Runtime data, local credentials, session files, proxy lists, databases, ZIP jobs, caches, and CodeGraph data will remain ignored and will not enter the public history.

## Third-party provenance

The existing `tam/gaf/` code is retained as a separately identified derived area. `NOTICE.GAFBot` remains in the repository and will be reviewed for completeness against the upstream MIT notice. The README will link to the upstream GAFBot project and describe the affected directory without implying that the entire TAO project is authored by GAFBot.

## Security and privacy release gate

Before the first public commit, the project will be scanned for:

- real Telegram API credentials, bot tokens, web tokens, passwords, private keys, and proxy credentials;
- `.env`, `.session`, `tdata`, database, ZIP, and account-list artifacts;
- sample phone numbers and live code-fetch endpoints that should be replaced with inert examples;
- hard-coded deployment URLs and payment credentials;
- generated test directories and caches.

The release will document that users must supply their own Telegram API credentials and must keep session material local.

## Testing and verification

The release process will first make the test command deterministic on Windows and CI, including the missing temporary-directory fixture names and async test dependency/configuration. Verification gates are:

1. `python -m compileall -q tam tests`
2. `python -m pytest -q tests`
3. package/import smoke checks through the documented CLI entry points
4. Git status and tracked-file audit confirming no runtime data or secrets are included

Known runtime behavior changes are avoided unless required to make packaging and tests reliable.

## Release sequence

1. Create the Git repository and baseline commit.
2. Add packaging, license, provenance, security, and CI files.
3. Remove or replace sensitive examples and expand ignore rules.
4. Update README and developer documentation to the TAO identity.
5. Repair the test harness and run the verification gates.
6. Review the final tracked tree, commit the release candidate, and prepare the GitHub repository metadata.

