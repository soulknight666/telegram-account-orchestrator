from __future__ import annotations

import asyncio
import io
import zipfile

import pytest


def _zip(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return out.getvalue()


def _multipart(file_bytes: bytes, *, fields: dict[str, str] | None = None,
               filename: str = "tdata.zip") -> tuple[bytes, str]:
    boundary = "----tam-tdata-inspect"
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
        "type": "http", "method": "POST", "path": "/api/import/tdata/inspect",
        "headers": [(b"content-type", content_type.encode())],
        "query_string": b"", "server": ("test", 80), "client": ("test", 1),
        "scheme": "http", "http_version": "1.1", "root_path": "",
    }, receive)


def test_upload_tdata_inspect_is_read_only_and_maps_duplicate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TAM_MASTER_KEY", "tdata-inspect-test-master-key-32!")
    from tam import api, tdata
    from tam.db import Account

    class FakeDB:
        def list(self):
            return [Account(id=42, label="existing", phone="+8613800000000", user_id=99)]

        def update(self, *_args, **_kwargs):
            raise AssertionError("体检接口不应写数据库")

    class FakeManager:
        async def import_tdata(self, **_kwargs):
            raise AssertionError("体检接口不应调用导入")

    monkeypatch.setattr(api, "db", FakeDB())
    monkeypatch.setattr(api, "manager", FakeManager())
    monkeypatch.setattr(tdata, "inspect_tdata", lambda _path, _password: {
        "path": _path, "ok": True, "error": None,
        "steps": [{"name": "解出本地主密钥", "ok": True, "detail": "无本地密码"}],
        "accounts": [{"user_id": 99, "main_dc": 2, "dcs": [2]}],
    })
    raw = _zip({"8613800000000/tdata/key_datas": b"fixture"})
    body, content_type = _multipart(raw, fields={"debug": "true"})

    result = asyncio.run(api.inspect_tdata_upload(_request(body, content_type)))
    row = result["items"][0]
    assert result["total"] == 1
    assert row["path"] == "8613800000000/tdata"
    assert row["phone"] == "+8613800000000"
    assert row["dc"] == 2
    assert row["needs_password"] is False
    assert row["existing"] is True
    assert row["status"] == "duplicate"
    assert row["code"] == "import_duplicate"
    assert row["params"] == {"existing_id": 42}
    assert "debug" in row


def test_upload_tdata_inspect_reports_passcode_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TAM_MASTER_KEY", "tdata-inspect-test-master-key-32!")
    from tam import api, tdata

    class FakeDB:
        def list(self):
            return []

    monkeypatch.setattr(api, "db", FakeDB())
    monkeypatch.setattr(tdata, "inspect_tdata", lambda _path, _password: {
        "path": _path, "ok": False,
        "error": "该 tdata 设了本地密码（passcode）",
        "steps": [{"name": "解出本地主密钥", "ok": False, "detail": "该 tdata 设了本地密码"}],
        "accounts": [],
    })
    raw = _zip({"locked/tdata/key_datas": b"fixture"})
    body, content_type = _multipart(raw, fields={"tdata_passcode": "wrong", "debug": "true"})

    result = asyncio.run(api.inspect_tdata_upload(_request(body, content_type)))
    row = result["items"][0]
    assert row["status"] == "failed"
    assert row["code"] == "tdata_passcode"
    assert row["needs_password"] is True
    assert row["phone"] is None
    assert "debug" in row


def test_upload_tdata_inspect_bad_archive_uses_machine_code(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TAM_MASTER_KEY", "tdata-inspect-test-master-key-32!")
    from tam import api
    from tam.errors import ApiError

    body, content_type = _multipart(b"not-a-zip", filename="broken.zip")
    with pytest.raises(ApiError) as exc:
        asyncio.run(api.inspect_tdata_upload(_request(body, content_type)))
    assert exc.value.code == "import_bad_archive"
