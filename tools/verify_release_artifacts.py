"""Validate the public release artifact contract and generate SHA-256 sums."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

ARTIFACTS = (
    "TAO-Windows-x64-Portable.zip",
    "TAO-Windows-x64-Setup.exe",
    "TAO-Linux-x64.tar.gz",
)
CHECKSUM_FILE = "SHA256SUMS.txt"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifacts(directory: Path, *, write_checksums: bool = False) -> list[str]:
    missing = [name for name in ARTIFACTS if not (directory / name).is_file()]
    if missing:
        raise FileNotFoundError("missing release artifacts: " + ", ".join(missing))
    lines = [f"{sha256(directory / name)}  {name}" for name in ARTIFACTS]
    if write_checksums:
        (directory / CHECKSUM_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, nargs="?", default=Path("release"))
    parser.add_argument("--write-checksums", action="store_true")
    args = parser.parse_args()
    for line in verify_artifacts(args.directory, write_checksums=args.write_checksums):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
