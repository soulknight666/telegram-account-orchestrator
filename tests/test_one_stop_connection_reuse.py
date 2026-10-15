import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tam.manager import AccountManager, PendingLogin


def manager_fixture():
    mgr = AccountManager.__new__(AccountManager)
    account = SimpleNamespace(id=1, phone='+15550000001', session_enc='encrypted')
    mgr.s = SimpleNamespace(master_key='fixture')
    mgr.db = SimpleNamespace(get=lambda aid: account, update=Mock(), log=Mock())
    mgr._save_session = Mock()
    mgr._load_session = Mock(return_value='session')
    lock = asyncio.Lock()
    mgr._lock = lambda aid: lock
    mgr.limiter = SimpleNamespace(wait=AsyncMock())
    client = SimpleNamespace(
        connect=AsyncMock(), disconnect=AsyncMock(), is_connected=lambda: True,
        sign_in=AsyncMock(), get_me=AsyncMock(return_value=SimpleNamespace(
            id=1, username='fixture', phone='15550000001')),
        is_user_authorized=AsyncMock(return_value=True),
        session=SimpleNamespace(save=lambda: 'session'))
    mgr._build_client = Mock(return_value=client)
    mgr._pending = {1: PendingLogin(1, account.phone, 'hash', client, 0)}
    return mgr, client, lock


@pytest.mark.asyncio
async def test_new_login_and_twofa_share_live_client_across_wait_for_tasks():
    mgr, client, lock = manager_fixture()
    async with mgr.reuse_login_client(1):
        await asyncio.wait_for(mgr.sign_in(1, '12345'), 1)
        async def modify():
            async with mgr.session(1) as reused:
                assert reused is client
                assert lock.locked()
        await asyncio.wait_for(modify(), 1)
        mgr._build_client.assert_not_called()
        client.disconnect.assert_not_awaited()
    client.disconnect.assert_awaited_once()
    mgr._save_session.assert_called_once()
    assert not lock.locked()


@pytest.mark.asyncio
async def test_existing_session_probe_is_reused_and_exception_releases_it():
    mgr, client, lock = manager_fixture()
    with pytest.raises(RuntimeError):
        async with mgr.reuse_login_client(1):
            assert await asyncio.wait_for(mgr.login_session_authorized(1), 1)
            async with mgr.session(1) as reused:
                assert reused is client
                raise RuntimeError('fixture')
    assert mgr._build_client.call_count == 1
    client.connect.assert_awaited_once()
    client.disconnect.assert_awaited_once()
    assert not lock.locked()


@pytest.mark.asyncio
async def test_regular_login_does_not_retain_client():
    mgr, client, _ = manager_fixture()
    await mgr.sign_in(1, '12345')
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_lease_releases_lock():
    mgr, client, lock = manager_fixture()
    entered = asyncio.Event()
    async def work():
        async with mgr.reuse_login_client(1):
            await mgr.sign_in(1, '12345')
            entered.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(work())
    try:
        await asyncio.wait_for(entered.wait(), 1)
    except TimeoutError:
        await task
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.locked()
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_workflow_uses_lease_and_finishes_without_reconnect():
    import sqlite3
    from tam.one_stop import OneStopWorkflow
    from tam.tasks import TaskStore, TaskRunner
    mgr, client, lock = manager_fixture()
    mgr.db.get(1).session_enc = None
    mgr.db.get(1).code_url = 'https://example.invalid/GetHTML'
    mgr.auto_login = AsyncMock(side_effect=lambda *a, **kw: mgr.sign_in(1, '12345'))
    async def login(*args, **kwargs):
        return await mgr.sign_in(1, '12345')
    mgr.auto_login.side_effect = login
    async def change(manager, aid, *args):
        async with manager.session(aid) as used:
            assert used is client
            return {'has_password': True}
    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
    try:
        store = TaskStore(conn); runner = TaskRunner(store)
        flow = OneStopWorkflow(store, runner, mgr, change, lambda e: type(e).__name__)
        task = flow.create([1], concurrency=1, new_password='fixture', fmt='pack')
        flow.start(task.id)
        while runner.is_running(task.id):
            await asyncio.sleep(0.001)
        assert flow.export_ids(task.id) == [1]
        assert store.get(task.id).ok_count == 1
        mgr._build_client.assert_not_called()
        client.disconnect.assert_awaited_once()
        assert not lock.locked()
    finally:
        conn.close()
