import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from tam.tasks import TaskStore, TaskRunner


@pytest.mark.asyncio
async def test_login_task_reservation_stop_and_secret_redaction(monkeypatch):
    from tam import api
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = TaskStore(conn)
    runner = TaskRunner(store)
    gate = asyncio.Event()
    started = asyncio.Event()
    calls = []

    async def login(aid, **kwargs):
        calls.append(aid)
        kwargs["progress"]("waiting: 60")
        started.set()
        await gate.wait()

    monkeypatch.setattr(api, "tasks", store)
    monkeypatch.setattr(api, "runner", runner)
    monkeypatch.setattr(api, "_login_reservations", {})
    monkeypatch.setattr(api, "manager", SimpleNamespace(auto_login=login))
    monkeypatch.setattr(api, "db", SimpleNamespace(get=lambda aid: SimpleNamespace(
        phone="fixture", code_url="https://example.test/GetHTML", status="new", session_enc=None)))
    result = await api.create_login_task(api.LoginTaskIn(account_ids=[1, 2], concurrency=1,
                                                        password="secret-fixture"))
    tid = result["task"]["id"]
    await started.wait()
    assert "secret-fixture" not in str(store.get(tid).public())
    store.flush_progress()  # production coalesces updates for up to 500 ms
    assert store.targets(tid)[0]["detail"] == "waiting: 60"
    with pytest.raises(api.HTTPException) as exc:
        await api.create_login_task(api.LoginTaskIn(account_ids=[1]))
    assert exc.value.status_code == 409
    await runner.stop(tid)
    gate.set()
    while runner.is_running(tid):
        await asyncio.sleep(0)
    assert calls == [1]
    assert store.get(tid).skip_count == 1
    conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized", [False, True])
async def test_retry_selects_only_failed_accounts(monkeypatch, authorized):
    from tam import api
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = TaskStore(conn)
    runner = TaskRunner(store)
    old = store.create("auto_login", "fixture", ["1", "2"])
    targets = store.targets(old.id)
    store.mark_target(targets[0]["id"], "ok")
    store.mark_target(targets[1]["id"], "fail")
    store.set_status(old.id, "done")
    calls = []
    async def login(aid, **kwargs):
        calls.append(aid)
    verified = []
    async def verify(aid):
        verified.append(aid)
        return authorized
    monkeypatch.setattr(api, "tasks", store)
    monkeypatch.setattr(api, "runner", runner)
    monkeypatch.setattr(api, "_login_reservations", {})
    monkeypatch.setattr(api, "manager", SimpleNamespace(auto_login=login, login_session_authorized=verify))
    monkeypatch.setattr(api, "db", SimpleNamespace(get=lambda aid: SimpleNamespace(
        phone="fixture", code_url="https://example.test/GetHTML", status="active", session_enc="fixture")))
    result = await api.create_login_task(api.LoginTaskIn(retry_task_id=old.id))
    while runner.is_running(result["task"]["id"]):
        await asyncio.sleep(0)
    assert verified == [2]
    assert calls == ([] if authorized else [2])
    conn.close()


@pytest.mark.asyncio
async def test_legacy_batch_rejects_queued_login_before_any_action(monkeypatch):
    from tam import api
    from unittest.mock import AsyncMock
    run = AsyncMock()
    monkeypatch.setattr(api, "manager", SimpleNamespace(run_batch=run))
    monkeypatch.setattr(api, "runner", SimpleNamespace(is_running=lambda tid: True))
    monkeypatch.setattr(api, "_login_reservations", {1: 10})
    monkeypatch.setattr(api, "_legacy_login_reservations", set())
    with pytest.raises(api.HTTPException) as exc:
        await api.batch_logout_relogin(api.BatchLogoutReloginIn(account_ids=[1]))
    assert exc.value.status_code == 409
    run.assert_not_awaited()


def test_login_errors_are_actionable_without_secret_echo():
    from tam.api import _login_failure_message
    message = _login_failure_message(RuntimeError("需要两步验证 https://example.test/private?token=secret"))
    assert "两步验证" in message
    assert "secret" not in message
    assert "https" not in message
