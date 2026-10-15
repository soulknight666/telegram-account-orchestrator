from __future__ import annotations

import io
import json
import zipfile
import asyncio
from pathlib import Path

import pytest

from tam.mixed_import import MixedArchiveError, extract_mixed_zip, scan_mixed_tree


def _zip(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return out.getvalue()


def test_scans_pure_and_mixed_tree_with_sidecar(tmp_path: Path):
    root = tmp_path / "x"
    (root / "8613800000000" / "tdata").mkdir(parents=True)
    (root / "8613800000000" / "tdata" / "key_datas").write_bytes(b"k")
    (root / "nested").mkdir()
    (root / "nested" / "u.session").write_bytes(b"SQLite format 3")
    (root / "nested" / "u.json").write_text(
        json.dumps({"phone": "8613800000001", "app_id": 123, "twoFA": "***"}),
        encoding="utf-8",
    )
    (root / "readme.txt").write_text("ignored", encoding="utf-8")
    (root / ".DS_Store").write_bytes(b"ignored")

    result = scan_mixed_tree(root)
    assert [(x["kind"], x["phone"]) for x in result["items"]] == [
        ("tdata", "+8613800000000"), ("session", "+8613800000001")
    ]
    assert result["items"][1]["sidecar"]["app_id"] == 123
    assert result["skipped"] == [{
        "path": "readme.txt", "code": "import_unknown_format", "reason": "无法识别"
    }]


def test_nested_zip_expands_exactly_one_level(tmp_path: Path):
    inner = _zip({
        "phone/u.session": b"session",
        "phone/u.json": b'{"phone":"8613800000002"}',
        "phone/tdata/key_datas": b"key",
    })
    raw = _zip({"packs/inner.zip": inner})
    extracted = tmp_path / "extract"
    extract_mixed_zip(raw, extracted)
    result = scan_mixed_tree(extracted)
    assert {item["kind"] for item in result["items"]} == {"session", "tdata"}
    assert not any(p.suffix == ".zip" for p in extracted.rglob("*"))


def test_path_traversal_and_symlink_are_rejected(tmp_path: Path):
    with pytest.raises(MixedArchiveError):
        extract_mixed_zip(_zip({"../escape.session": b"x"}), tmp_path / "bad")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16) | 0xA000
        zf.writestr(info, b"../escape")
    with pytest.raises(MixedArchiveError):
        extract_mixed_zip(out.getvalue(), tmp_path / "symlink")


def test_zip_size_limit_is_configurable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("TAM_MIXED_MAX_EXTRACT_MB", "1")
    with pytest.raises(MixedArchiveError, match="解压总大小超过上限"):
        extract_mixed_zip(_zip({"large.bin": b"x" * (1024 * 1024 + 1)}), tmp_path / "large")


def test_corrupt_zip_is_rejected(tmp_path: Path):
    with pytest.raises(MixedArchiveError, match="有效的 ZIP"):
        extract_mixed_zip(b"not a zip", tmp_path / "corrupt")


def _multipart(file_bytes: bytes, *, fields: dict[str, str] | None = None,
               filename: str = "mixed.zip") -> tuple[bytes, str]:
    boundary = "----tam-test-boundary"
    parts: list[bytes] = []
    for key, value in (fields or {}).items():
        parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n").encode())
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: application/zip\r\n\r\n").encode() + file_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _request(body: bytes, content_type: str):
    from starlette.requests import Request

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({
        "type": "http", "method": "POST", "path": "/api/import/mixed",
        "headers": [(b"content-type", content_type.encode())],
        "query_string": b"", "server": ("test", 80), "client": ("test", 1),
        "scheme": "http", "http_version": "1.1", "root_path": "",
    }, receive)


def test_mixed_endpoint_dry_run_isomorphic_and_does_not_write(monkeypatch: pytest.MonkeyPatch):
    # Importing tam.api is intentionally delayed: its Settings object is process-global.
    monkeypatch.setenv("TAM_MASTER_KEY", "mixed-import-test-master-key-32!")
    from tam import api

    class FakeDB:
        def list(self):
            return []

        def update(self, *_args, **_kwargs):
            raise AssertionError("dry_run 不应写数据库")

    class FakeManager:
        async def import_tdata(self, **_kwargs):
            raise AssertionError("dry_run 不应导入")

        async def import_session_files(self, **_kwargs):
            raise AssertionError("dry_run 不应导入")

    monkeypatch.setattr(api, "db", FakeDB())
    monkeypatch.setattr(api, "manager", FakeManager())
    raw = _zip({
        "8613800000000/tdata/key_datas": b"key",
        "u.session": b"session",
        "u.json": b'{"phone":"8613800000001"}',
        "readme.txt": b"readme",
    })
    body, content_type = _multipart(raw)
    result = asyncio.run(api.import_mixed_zip(_request(body, content_type)))
    assert result["dry_run"] is True
    assert result["total"] == 2
    assert result["tdata"] == {"found": 1, "imported": 0, "failed": 0}
    assert result["session"] == {"found": 1, "imported": 0, "failed": 0}
    assert result["skipped"] == [{
        "path": "readme.txt", "code": "import_unknown_format", "reason": "无法识别"
    }]


def test_mixed_endpoint_imports_each_item_and_maps_duplicate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TAM_MASTER_KEY", "mixed-import-test-master-key-32!")
    from tam import api
    from tam.db import Account

    updates: list[tuple[tuple, dict]] = []

    class FakeDB:
        def list(self):
            return [Account(id=42, label="existing", phone="+8613800000001")]

        def update(self, *args, **kwargs):
            updates.append((args, kwargs))

    calls: list[str] = []

    class FakeManager:
        async def import_tdata(self, path, **kwargs):
            calls.append("tdata")
            return [{"ok": True, "id": 7, "user_id": 700}]

        async def import_session_files(self, path, **kwargs):
            calls.append("session")
            return [{"ok": True, "id": 8, "user_id": 800}]

    monkeypatch.setattr(api, "db", FakeDB())
    monkeypatch.setattr(api, "manager", FakeManager())
    raw = _zip({
        "one/tdata/key_datas": b"key",
        "u.session": b"session",
        "u.json": b'{"phone":"8613800000001"}',
    })
    body, content_type = _multipart(raw, fields={"dry_run": "false", "twofa": "new-secret"})
    result = asyncio.run(api.import_mixed_zip(_request(body, content_type)))
    assert calls == ["tdata"]  # session is detected as duplicate and skipped
    assert result["items"][0]["status"] == "imported"
    assert result["items"][1]["status"] == "duplicate"
    assert result["items"][1]["code"] == "import_duplicate"
    assert result["items"][1]["params"] == {"existing_id": 42}
    assert updates and updates[0][0] == (7,)


def test_mixed_endpoint_keeps_tdata_passcode_code_and_continues(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TAM_MASTER_KEY", "mixed-import-test-master-key-32!")
    from tam import api

    class FakeDB:
        def list(self):
            return []

    class FakeManager:
        async def import_tdata(self, path, **kwargs):
            return [{"ok": False, "code": "tdata_passcode", "error": "密码错误"}]

        async def import_session_files(self, path, **kwargs):
            return [{"ok": True, "id": 9, "user_id": 900}]

    monkeypatch.setattr(api, "db", FakeDB())
    monkeypatch.setattr(api, "manager", FakeManager())
    raw = _zip({"locked/tdata/key_datas": b"key", "ok.session": b"session"})
    body, content_type = _multipart(raw, fields={"dry_run": "false"})
    result = asyncio.run(api.import_mixed_zip(_request(body, content_type)))
    assert result["items"][0]["code"] == "tdata_passcode"
    assert result["items"][0]["status"] == "failed"
    assert result["items"][1]["status"] == "imported"
