# TAO Architecture

## Entry points

- `tam.cli`: command-line management and MCP startup.
- `tam.run`: deployment selector for Web, Bot, or both frontends.
- `tam.api`: FastAPI application and Web UI routes.
- `tam.bot`: Telegram Bot frontend.
- `tam.mcp_server`: MCP stdio transport over the shared tool registry.

## Core

- `tam.config.Settings` loads environment configuration.
- `tam.db.Database` stores accounts, encrypted session material, settings, tasks, leads, and audit records in SQLite.
- `tam.manager.AccountManager` owns Telethon clients, account locks, login/import flows, health checks, messaging, and device-session operations.
- `tam.tools` exposes schema-validated read/write/destructive operations to CLI, HTTP, and MCP callers.

## Boundaries

Public account responses exclude encrypted sessions and saved two-factor secrets. Human-only login and session import/export operations are not exposed through the Agent tool registry. `tam/gaf/` is the separately attributed GAFBot-derived integration area described in `docs/THIRD_PARTY.md`.
