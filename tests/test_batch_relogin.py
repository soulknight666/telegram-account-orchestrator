from __future__ import annotations

from types import MethodType, SimpleNamespace

import pytest

from tam.crypto import encrypt, generate_master_key
from tam.db import Account
from tam.manager import AccountManager


class _FakeDb:
    def __init__(self, account: Account) -> None:
        self.account = account
        self.logs: list[tuple[object, ...]] = []

    def get(self, account_id: int) -> Account | None:
        return self.account if account_id == self.account.id else None

    def log(self, *args: object) -> None:
        self.logs.append(args)


@pytest.mark.asyncio
@pytest.mark.parametrize("delay,interval,expected,skip", [(0, 1, 0, False), (30, 60, 30, False), (90, 60, 90, False), (30, 60, 30, True)])
async def test_auto_login_uses_saved_twofa_when_password_is_empty(monkeypatch, delay, interval, expected, skip) -> None:
    key = generate_master_key()
    account = Account(
        id=7,
        label="fixture",
        phone="+10000000000",
        code_url="https://example.invalid/code",
        twofa_enc=encrypt(key, "saved-password"),
    )
    manager = AccountManager.__new__(AccountManager)
    manager.db = _FakeDb(account)
    manager.s = SimpleNamespace(default_proxy=None, master_key=key)

    async def fake_read_code(*args, **kwargs):
        return None

    events = []

    async def fake_prepare(*args, **kwargs):
        events.append("prepare")
        return {"prepared": False, "pre_read_skipped": skip}

    async def fake_wait(*args, **kwargs):
        events.append(("poll", kwargs["interval"]))
        return "12345", None

    async def fake_send(self, account_id: int):
        events.append("send")
        return {"ok": True}

    async def fake_sleep(seconds):
        events.append(("sleep", seconds))

    seen: dict[str, object] = {}

    async def fake_sign_in(self, account_id: int, code: str, password: str | None, **kwargs):
        seen.update(account_id=account_id, code=code, password=password)
        return {"ok": True}

    monkeypatch.setattr("tam.codefetch.read_code", fake_read_code)
    monkeypatch.setattr("tam.codefetch.prepare_code_source", fake_prepare)
    monkeypatch.setattr("tam.codefetch.wait_for_code_details", fake_wait)
    monkeypatch.setattr("tam.manager.asyncio.sleep", fake_sleep)
    manager.send_code = MethodType(fake_send, manager)
    manager.sign_in = MethodType(fake_sign_in, manager)

    result = await manager.auto_login(7, password=None, timeout=1, query_delay=delay, poll_interval=interval)

    assert result["ok"] is True
    assert seen == {"account_id": 7, "code": "12345", "password": "saved-password"}
    assert events == ["prepare", "send", ("sleep", expected), ("poll", interval)]


@pytest.mark.asyncio
async def test_logout_relogin_continues_after_remote_logout_error() -> None:
    account = Account(
        id=9,
        label="offline",
        phone="+10000000000",
        code_url="https://example.invalid/code",
    )
    manager = AccountManager.__new__(AccountManager)
    manager.db = _FakeDb(account)
    calls: list[tuple[object, ...]] = []

    async def fake_logout(self, account_id: int):
        calls.append(("logout", account_id))
        return {"ok": True, "remote_logged_out": False, "note": "local_after_error:TimeoutError"}

    async def fake_auto(self, account_id: int, password=None, timeout=120, **timing):
        assert timing == {"query_delay": 75, "poll_interval": 65}
        calls.append(("auto_login", account_id, password, timeout))
        return {"ok": True, "account_id": account_id}

    manager.logout = MethodType(fake_logout, manager)
    manager.auto_login = MethodType(fake_auto, manager)

    result = await manager.logout_and_auto_login(9, password="shared-2fa", timeout=45,
                                                query_delay=75, poll_interval=65)

    assert result["ok"] is True
    assert result["logout"]["remote_logged_out"] is False
    assert result["login"]["ok"] is True
    assert calls == [("logout", 9), ("auto_login", 9, "shared-2fa", 45)]


def test_batch_logout_relogin_defaults_to_three_workers() -> None:
    from tam.api import BatchLogoutReloginIn

    body = BatchLogoutReloginIn(account_ids=[1, 2, 3])
    assert body.concurrency == 3
