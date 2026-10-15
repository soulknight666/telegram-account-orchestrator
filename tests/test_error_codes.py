"""统一错误码的纯函数与批量结果契约测试。"""
from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace
import pytest

from tam.errors import ApiError, error_payload, telethon_error_to_code
from tam.manager import AccountManager
from tam.tasks import TaskRunner, TaskStore


def test_api_error_keeps_detail_and_optional_fields():
    err = ApiError("session_invalid", "会话已失效", status=401,
                   params={"account_id": 12})
    assert err.status == 401
    assert error_payload(err) == {
        "detail": "会话已失效", "code": "session_invalid",
        "params": {"account_id": 12},
    }


def test_flood_wait_has_retry_after_only_when_available():
    class FloodWaitError(Exception):
        seconds = 37

    payload = error_payload(FloodWaitError("A FLOOD_WAIT_37 occurred"))
    assert payload["code"] == "flood_wait"
    assert payload["retry_after"] == 37
    assert "detail" in payload


def test_api_error_preserves_contextual_params_without_secrets():
    payload = error_payload(ApiError("proxy_error", "代理认证失败", status=502,
                                     params={"proxy_id": 8}))
    assert payload["params"] == {"proxy_id": 8}
    assert "password" not in payload


def test_unknown_exception_gets_safe_machine_code():
    assert error_payload(RuntimeError("secret-token-should-not-be-a-code"),
                         detail="服务器内部错误")["code"] == "internal_error"


def test_http_status_defaults_are_machine_readable():
    from fastapi import HTTPException
    assert error_payload(HTTPException(401, "unauthorized"), status=401)["code"] == "auth_required"
    assert error_payload(HTTPException(409, "重复提交"), status=409)["code"] == "conflict"
    assert error_payload(HTTPException(422, [{"msg": "bad"}]), status=422)["code"] == "validation_error"
    assert error_payload(HTTPException(403, "无权访问"), status=403)["code"] == "http_403"


@pytest.mark.parametrize("code", [
    "account_deleted", "account_restricted", "account_locked", "code_link_missing",
    "import_bad_archive", "import_unknown_format", "import_duplicate", "tdata_passcode",
    "op_unknown", "op_param_missing", "task_cancelled", "peer_invalid",
    "privacy_restricted", "dc_migrate",
])
def test_declared_business_codes_are_serializable(code):
    payload = error_payload(ApiError(code, "fixture", params={"account_id": 1}))
    assert payload["code"] == code
    assert payload["detail"] == "fixture"


def test_telethon_mapping_covers_session_and_twofa():
    for name, expected in {
        "AuthKeyUnregisteredError": "session_invalid",
        "UserDeactivatedBanError": "account_banned",
        "SessionPasswordNeededError": "twofa_required",
        "PasswordHashInvalidError": "twofa_wrong",
        "PhoneCodeExpiredError": "code_timeout",
    }.items():
        exc = type(name, (Exception,), {})()
        assert telethon_error_to_code(exc) == expected


def test_task_targets_add_code_without_removing_detail():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = TaskStore(conn)
    task = store.create("x", "测试", ["7"])
    store.mark_target(task.id and 1, "fail", "会话已失效", code="session_invalid")
    row = store.targets(task.id)[0]
    assert row["detail"] == "会话已失效"
    assert row["code"] == "session_invalid"
    assert "result" not in row and "error" not in row


def test_task_runner_records_code_for_failed_handler():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = TaskStore(conn)
    task = store.create("x", "测试", ["7"])

    async def handler(_row):
        raise ApiError("proxy_error", "代理连接失败", status=502,
                       params={"proxy_id": 4})

    asyncio.run(TaskRunner(store).run(task.id, handler, concurrency=1))
    row = store.targets(task.id)[0]
    assert row["status"] == "fail"
    assert row["detail"] == "ApiError: 代理连接失败"
    assert row["code"] == "proxy_error"


def test_manager_batch_failure_has_code_without_touching_success_shape():
    class Dummy:
        s = SimpleNamespace(action_min_delay=0, action_max_delay=0)

        def __init__(self):
            self._batch_gate = asyncio.Lock()

        async def task(self, aid):
            if aid == 2:
                raise ApiError("session_invalid", "会话已失效", status=401)
            return {"value": aid}

    async def run():
        dummy = Dummy()
        result = await AccountManager.run_batch(dummy, [1, 2], dummy.task,
                                                concurrency=2, stagger=False)
        assert result[0] == {"account_id": 1, "ok": True, "result": {"value": 1}}
        assert result[1]["code"] == "session_invalid"
        assert result[1]["error"] == "ApiError('会话已失效')"

    asyncio.run(run())


def test_http_error_handler_adds_code_and_keeps_detail():
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from tam.api import http_error

    async def run():
        response = await http_error(None, StarletteHTTPException(404, "账号不存在"))
        assert response.status_code == 404
        assert response.body.decode() == '{"detail":"账号不存在","code":"not_found"}'

    asyncio.run(run())


def test_http_readonly_error_uses_readonly_code():
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from tam.api import http_error

    async def run():
        response = await http_error(None, StarletteHTTPException(403, "当前是只读模式"))
        assert b'"code":"readonly_mode"' in response.body
        assert "当前是只读模式" in response.body.decode()

    asyncio.run(run())


def test_validation_error_keeps_fastapi_detail_array():
    from fastapi.exceptions import RequestValidationError
    from tam.api import validation_error

    async def run():
        exc = RequestValidationError([{"loc": ("body", "ids"), "msg": "Field required", "type": "missing"}])
        response = await validation_error(None, exc)
        assert response.status_code == 422
        assert b'"code":"validation_error"' in response.body
        assert b'"detail"' in response.body

    asyncio.run(run())
