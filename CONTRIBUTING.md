# Contributing to TAO

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev,bot]"
```

## Verification

```bash
python -m compileall -q tam tests
python -m pytest -q tests
python tools/audit_public_release.py --tracked-only
```

Keep changes focused, add or update tests, and do not commit account sessions, `tdata`, databases, `.env`, bot tokens, proxy credentials, or generated ZIP jobs. Pull requests should explain the user-visible change and include the verification commands run.
