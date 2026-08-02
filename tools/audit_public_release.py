#!/usr/bin/env python3
"""Audit a TAO checkout for files and credential patterns unsafe to publish."""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_NAMES = {".env", "proxy.txt"}
BLOCKED_SUFFIXES = {".session", ".db", ".sqlite", ".sqlite3", ".zip", ".pem", ".key"}
SKIP_DIRS = {".git", ".codegraph", ".worktrees", ".venv", "__pycache__"}
PATTERNS = {
    "telegram_bot_token": re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "live_code_endpoint": re.compile(r"https?://tgapi\.puonl\.com\b", re.I),
}


def tracked_files() -> list[Path]:
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / item.decode("utf-8") for item in out.split(b"\0") if item]


def checkout_files() -> list[Path]:
    return [p for p in ROOT.rglob("*") if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracked-only", action="store_true")
    args = parser.parse_args()
    findings: list[str] = []
    for path in tracked_files() if args.tracked_only else checkout_files():
        rel = path.relative_to(ROOT)
        if path.name in BLOCKED_NAMES or path.suffix.lower() in BLOCKED_SUFFIXES or "tdata" in {p.lower() for p in rel.parts}:
            findings.append(f"blocked_path {rel}")
            continue
        if path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{name} {rel}:{line}")
    if findings:
        print("Public-release audit failed:")
        print("\n".join(sorted(set(findings))))
        return 1
    print("Public-release audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
