# TAO Release Distribution and GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows x64 GUI launcher and installer-ready package, headless Linux Docker/systemd deployment, automated GitHub Releases, and a stable GitHub Pages sharing page for Telegram previews.

**Architecture:** A UI-independent configuration service and process controller become the shared foundation. Tkinter provides the Windows dashboard, while Linux uses the same configuration service through a terminal command and an SSH-tunnel-only setup server. Packaging and release workflows assemble tested artifacts without modifying user configuration or data.

**Tech Stack:** Python 3.11+, Tkinter/ttk, PyInstaller, Inno Setup, FastAPI, Docker Compose, systemd, GitHub Actions, pytest.

---

### Task 1: Shared release configuration service

**Files:**
- Create: `tam/release_config.py`
- Create: `tests/test_release_config.py`
- Modify: `setup.py`

- [ ] **Step 1: Write failing tests for parsing, defaults, validation, secret generation, masking, backup, and atomic `.env` writes.**
- [ ] **Step 2: Run `python -m pytest -q tests/test_release_config.py` and verify the module import fails.**
- [ ] **Step 3: Implement `ReleaseConfig`, `ConfigIssue`, `load_release_config`, `validate_release_config`, `ensure_release_secrets`, `mask_secret`, and `save_release_config`.**
- [ ] **Step 4: Reuse the shared writer from `setup.py` without changing existing command-line behavior.**
- [ ] **Step 5: Run the focused tests and existing `tests/test_config.py`.**
- [ ] **Step 6: Commit with `feat: add shared release configuration service`.**

### Task 2: Runtime process controller

**Files:**
- Create: `tam/process_controller.py`
- Create: `tests/test_process_controller.py`

- [ ] **Step 1: Write failing tests for command construction, state transitions, log callbacks, stop behavior, and port availability.**
- [ ] **Step 2: Run the focused tests and verify they fail because the controller is missing.**
- [ ] **Step 3: Implement `RuntimeCommand`, `RuntimeState`, `ProcessController`, and `is_port_available`; use `CREATE_NO_WINDOW` on Windows.**
- [ ] **Step 4: Run focused tests and commit with `feat: add TAO runtime process controller`.**

### Task 3: Windows Tkinter dashboard launcher

**Files:**
- Create: `tam/launcher.py`
- Create: `tests/test_launcher.py`
- Modify: `pyproject.toml`
- Copy: `assets/branding/tao-source.png`

- [ ] **Step 1: Write failing tests for launcher field mapping, masked values, runtime arguments, smoke-test mode, and Web console URL generation.**
- [ ] **Step 2: Run the focused tests and verify the launcher API is absent.**
- [ ] **Step 3: Implement the dashboard with Overview, Basic Configuration, Advanced Options, and Runtime Log pages.**
- [ ] **Step 4: Implement `--runtime` and `--smoke-test` non-GUI paths so frozen builds can be verified in CI.**
- [ ] **Step 5: Add the `tao-launcher` console entry point and use the supplied image for window branding.**
- [ ] **Step 6: Run focused tests and commit with `feat: add Windows dashboard launcher`.**

### Task 4: Headless Linux setup and one-time local setup server

**Files:**
- Create: `tam/headless_setup.py`
- Create: `tests/test_headless_setup.py`
- Modify: `tam/cli.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing tests for terminal defaults, non-interactive overrides, one-time token expiry, loopback binding, and SSH tunnel instructions.**
- [ ] **Step 2: Run focused tests and verify they fail.**
- [ ] **Step 3: Implement `tao setup --headless` and an optional loopback-only Web setup server with a 15-minute one-time token.**
- [ ] **Step 4: Ensure terminal-only setup remains complete when the Web step is skipped.**
- [ ] **Step 5: Run focused tests and commit with `feat: add headless Linux setup workflow`.**

### Task 5: Linux Docker and systemd deployment assets

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `docker-compose.yml`
- Create: `deploy/install.sh`
- Create: `deploy/tao.service`
- Create: `tests/test_deployment_assets.py`
- Modify: `docs/DEPLOYMENT.md`

- [ ] **Step 1: Write failing static tests for non-root container execution, health checks, persistent mounts, systemd hardening, and required installation paths.**
- [ ] **Step 2: Implement the Docker image, Compose service, systemd unit, and idempotent `--docker`, `--systemd`, and `--upgrade` installer modes.**
- [ ] **Step 3: Run deployment tests and ShellCheck when available.**
- [ ] **Step 4: Commit with `build: add Linux deployment distributions`.**

### Task 6: Windows packaging definitions

**Files:**
- Create: `packaging/tao-launcher.spec`
- Create: `packaging/tao.iss`
- Create: `tools/build_branding.py`
- Create: `tools/verify_release_artifacts.py`
- Create: `tests/test_packaging_assets.py`
- Modify: `requirements-optional.txt`

- [ ] **Step 1: Write failing tests for expected PyInstaller data files, no-console launcher mode, icon targets, artifact names, and checksum verification.**
- [ ] **Step 2: Implement branding generation, PyInstaller `onedir` configuration, Inno Setup configuration, and artifact verification.**
- [ ] **Step 3: Build and smoke-test the Windows portable directory locally when the required tools are installed.**
- [ ] **Step 4: Commit with `build: add Windows release packaging`.**

### Task 7: Stable Telegram sharing page

**Files:**
- Create: `site/index.html`
- Create: `site/404.html`
- Copy: `docs/assets/preview.png`
- Create: `tests/test_social_preview.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing tests asserting fixed HTTPS Open Graph metadata, Raw image URL, canonical Pages URL, and repository/Release links.**
- [ ] **Step 2: Implement the branded GitHub Pages landing page and README sharing guidance.**
- [ ] **Step 3: Run focused tests and commit with `docs: add stable Telegram sharing page`.**

### Task 8: CI, Pages, and Release automation

**Files:**
- Create: `.github/workflows/pages.yml`
- Create: `.github/workflows/release.yml`
- Create: `tests/test_workflows.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/RELEASE_CHECKLIST.md`

- [ ] **Step 1: Write failing static tests for Pages deployment, Windows/Linux release jobs, GHCR publishing, optional Authenticode secrets, artifact scanning, and SHA-256 generation.**
- [ ] **Step 2: Implement Pages and tag-triggered Release workflows.**
- [ ] **Step 3: Extend CI with the new focused tests and packaging metadata checks.**
- [ ] **Step 4: Run workflow tests and commit with `ci: automate TAO pages and release builds`.**

### Task 9: Full verification and release handoff

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/RELEASE_CHECKLIST.md`

- [ ] **Step 1: Run `python -m compileall -q tam tests tools`.**
- [ ] **Step 2: Run the complete pytest suite outside the restricted temp sandbox when required.**
- [ ] **Step 3: Run `python tools/audit_public_release.py --tracked-only`.**
- [ ] **Step 4: Run `git diff --check` and verify a clean worktree.**
- [ ] **Step 5: Update the changelog and release checklist with artifact names and unsigned-build behavior.**
- [ ] **Step 6: Commit with `chore: prepare TAO desktop and Linux release`.**
- [ ] **Step 7: Push the feature branch, create one non-draft PR, wait for CI, and merge after all checks pass.**
