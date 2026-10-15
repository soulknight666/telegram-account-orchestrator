"""Deterministic CSP helpers for the single-file TAO web bundle."""

from __future__ import annotations

import base64
import hashlib
import re


_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_CSP_META = re.compile(r"<meta\s+http-equiv=[\"']Content-Security-Policy[\"'][^>]*>\s*", re.IGNORECASE)


def _lf(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _inline_scripts(html: str) -> list[str]:
    scripts: list[str] = []
    for attrs, body in _SCRIPT.findall(_lf(html)):
        if not re.search(r"\bsrc\s*=", attrs, re.IGNORECASE):
            scripts.append(body)
    return scripts


def script_hashes(html: str) -> list[str]:
    return [
        "'sha256-" + base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode("ascii") + "'"
        for body in _inline_scripts(html)
    ]


def content_security_policy(html: str) -> str:
    hashes = " ".join(script_hashes(html))
    script_src = "'self'" + ((" " + hashes) if hashes else "")
    return "; ".join(
        [
            "default-src 'self'",
            "script-src " + script_src,
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com data:",
            "img-src 'self' data: blob:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-src 'none'",
            "worker-src 'self' blob:",
        ]
    )


def with_csp_meta(html: str) -> str:
    """Insert one CSP meta after charset; repeated calls are idempotent."""
    normalized = _lf(html)
    normalized = _CSP_META.sub("", normalized)
    policy = content_security_policy(normalized)
    meta = f'<meta http-equiv="Content-Security-Policy" content="{policy}" />'
    match = re.search(r"<meta\s+charset=[^>]+>", normalized, re.IGNORECASE)
    if match:
        suffix = normalized[match.end() :].lstrip("\n")
        return normalized[: match.end()] + "\n" + meta + "\n" + suffix
    return re.sub(r"<head\b[^>]*>", lambda m: m.group(0) + "\n" + meta, normalized, count=1, flags=re.IGNORECASE)
