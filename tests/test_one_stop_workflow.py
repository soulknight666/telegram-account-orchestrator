import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tam.tasks import TaskRunner, TaskStore


def fixture():
    from tam.one_stop import OneStopWorkflow
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    store = TaskStore(conn)
    runner = TaskRunner(store)
    accounts = {i: SimpleNamespace(id=i, phone=str(i), code_url='https://example.invalid/code',
                                  session_enc=None, status='new') for i in range(1, 1202)}
    def signed(aid, **kw):
        accounts[aid].session_enc = 'fixture'
        return {'ok': True}
    mgr = SimpleNamespace(s=SimpleNamespace(master_key='fixture-key'),
        db=SimpleNamespace(get=accounts.get, update=lambda *a, **k: None),
        auto_login=AsyncMock(side_effect=signed), login_session_authorized=AsyncMock(return_value=True))
    change = AsyncMock(return_value={'has_password': True})
    flow = OneStopWorkflow(store, runner, mgr, change, lambda e: 'classified failure')
    return flow, mgr, change


async def finish(flow, tid):
    while flow.runner.is_running(tid):
        await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_persistent_ready_results_do_not_truncate_at_500():
    flow, mgr, change = fixture()
    t = flow.create(list(range(1, 1202)), concurrency=32, new_password='', fmt='session')
    flow.start(t.id)
    await finish(flow, t.id)
    assert len(flow.export_ids(t.id)) == 1201
    assert flow.snapshot(t.id)['ok_count'] == 1201
    assert len(flow.results(t.id, 500, 500)) == 500
    assert flow.results(t.id, 500, 1000)[-1]['account_id'] == 1201
    change.assert_not_awaited()
    flow.start(t.id)
    await finish(flow, t.id)
    assert mgr.auto_login.await_count == 1201


@pytest.mark.asyncio
async def test_stage_protocol_existing_session_and_failed_2fa():
    flow, mgr, change = fixture()
    mgr.db.get(1).session_enc = 'existing'
    mgr.auto_login.side_effect = lambda aid, **kw: {'need_password': True} if aid == 3 else {'ok': True}
    change.side_effect = lambda *a: {'has_password': False}
    t = flow.create([1, 2, 3], concurrency=2, new_password='new-secret', fmt='pack')
    assert 'new-secret' not in str(t.public())
    flow.start(t.id)
    await finish(flow, t.id)
    assert flow.export_ids(t.id) == []
    assert {c.args[0] for c in mgr.auto_login.await_args_list} == {2, 3}
    assert change.await_count == 2
    assert flow.snapshot(t.id)['fail_count'] == 3
    assert all(r['detail'] for r in flow.results(t.id))


@pytest.mark.asyncio
async def test_resume_keeps_ready_accounts_and_does_not_repeat_uncertain_change():
    flow, mgr, change = fixture()
    t = flow.create([1, 2, 3], concurrency=1, new_password='secret-new', fmt='pack')
    flow.phase(t.id, 1, 'ready')
    flow.phase(t.id, 2, 'changing')
    flow.phase(t.id, 3, 'logged_in')
    mgr.db.get(3).session_enc = 'existing'
    flow.start(t.id)
    await finish(flow, t.id)
    assert flow.export_ids(t.id) == [1, 3]
    assert change.await_count == 1
    assert change.await_args.args[1] == 3
    flow.start(t.id)
    await finish(flow, t.id)
    assert change.await_count == 1
    assert next(x for x in flow.results(t.id) if x['account_id'] == 2)['phase'] == 'uncertain'


def test_backend_validation_rejects_invalid_options():
    flow, _, _ = fixture()
    for conc in (0, 33, -1):
        with pytest.raises(ValueError):
            flow.create([1], concurrency=conc, new_password='', fmt='pack')
    with pytest.raises(ValueError):
        flow.create([99999], concurrency=1, new_password='', fmt='pack')


@pytest.mark.parametrize('raises', [True, False])
def test_spawn_failure_rolls_back_claim_without_secret_echo(raises):
    flow, mgr, change = fixture()
    task = flow.create([1], concurrency=1, new_password='', fmt='pack')
    def spawn(*a, **kw):
        if raises:
            raise RuntimeError('fixture-private-value')
        return False
    flow.runner.spawn = spawn
    with pytest.raises(ValueError):
        flow.start(task.id)
    state = flow.snapshot(task.id)
    assert state['status'] == 'failed'
    assert not state['live']
    assert 'fixture-private-value' not in str(state)


@pytest.mark.asyncio
async def test_second_dispatcher_startup_does_not_interrupt_live_task(tmp_path):
    from tam.one_stop import OneStopWorkflow
    flow, mgr, change = fixture()
    connections = [sqlite3.connect(tmp_path/'shared.db') for _ in range(2)]
    services = []
    for conn in connections:
        conn.row_factory = sqlite3.Row
        store=TaskStore(conn)
        services.append(OneStopWorkflow(store, TaskRunner(store), mgr, change, lambda e:'fixture error'))
    a,b=services
    gate=asyncio.Event()
    async def login(*args,**kw):
        await gate.wait()
        return {'ok':True}
    mgr.auto_login.side_effect=login
    t=a.create([1],concurrency=1,new_password='',fmt='session')
    a.start(t.id)
    try:
        b.interrupt_on_startup()
        assert b.snapshot(t.id)['live']
        with pytest.raises(ValueError):
            b.start(t.id)
        assert a.store.get(t.id).status == 'running'
    finally:
        gate.set()
        await finish(a,t.id)
        for conn in connections: conn.close()


@pytest.mark.asyncio
async def test_structured_errors_preserve_known_good_authorization():
    flow,mgr,change=fixture()
    from unittest.mock import Mock
    from urllib.error import URLError
    mgr.db.update=Mock()
    mgr.auto_login.side_effect=URLError('https://private.invalid/token')
    t=flow.create([1],concurrency=1,new_password='',fmt='pack')
    flow.start(t.id); await finish(flow,t.id)
    row=flow.results(t.id)[0]
    assert row['error_code']=='NETWORK_ERROR'
    assert row['retryable']==1
    assert 'private' not in str(row)
    assert 'status' not in mgr.db.update.call_args.kwargs


@pytest.mark.asyncio
async def test_timeout_during_mutation_stays_uncertain_and_other_accounts_continue():
    flow, mgr, change = fixture()
    flow.change_timeout = 0.01
    async def slow_change(manager, aid, *args):
        if aid == 1:
            await asyncio.sleep(30)
        return {'has_password': True}
    change.side_effect = slow_change
    t=flow.create([1,2],concurrency=2,new_password='fixture',fmt='pack')
    flow.start(t.id)
    await asyncio.wait_for(finish(flow,t.id), 2)
    assert flow.export_ids(t.id) == [2]
    assert flow.phase(t.id,1) == 'uncertain'


@pytest.mark.asyncio
async def test_restart_retains_checkpoints_and_password_private():
    flow, mgr, change = fixture()
    task=flow.create([1,2],concurrency=2,new_password='fixture-new',fmt='pack')
    flow.phase(task.id,1,'ready')
    flow.phase(task.id,2,'changing')
    flow.store.set_status(task.id,'running')
    flow.interrupt_on_startup()
    assert flow.snapshot(task.id)['status'] == 'failed'
    flow.start(task.id)
    await finish(flow,task.id)
    assert flow.export_ids(task.id) == [1]
    change.assert_not_awaited()
    assert 'fixture-new' not in str(flow.snapshot(task.id))


@pytest.mark.asyncio
async def test_api_contract_exports_all_ready_rows_and_rejects_busy(monkeypatch):
    from tam import api
    flow, mgr, change = fixture()
    monkeypatch.setattr(api, 'tasks', flow.store)
    monkeypatch.setattr(api, 'runner', flow.runner)
    monkeypatch.setattr(api, 'manager', mgr)
    monkeypatch.setattr(api, 'db', SimpleNamespace(conn=flow.conn))
    monkeypatch.setattr(api, '_one_stop_service', flow)
    monkeypatch.setattr(api, '_login_reservations', {})
    monkeypatch.setattr(api, '_legacy_login_reservations', set())
    monkeypatch.setattr(api, 'settings', SimpleNamespace(readonly=False))
    export = AsyncMock(return_value='zip-fixture')
    monkeypatch.setattr(api, 'export_accounts_batch', export)
    result = await api.create_one_stop(api.OneStopIn(account_ids=list(range(1,1202))))
    with pytest.raises(api.HTTPException) as busy:
        await api.create_one_stop(api.OneStopIn(account_ids=[1]))
    assert busy.value.status_code == 409
    await finish(flow, result['id'])
    state = await api.one_stop_status(result['id'], limit=500, offset=1000)
    assert len(state['targets']) == 201
    assert state['ok_count'] == 1201
    await api.export_one_stop(result['id'])
    assert len(export.await_args.args[0].ids) == 1201
    assert 'password_enc' not in str(state)


@pytest.mark.asyncio
async def test_zero_success_export_is_an_error(monkeypatch, tmp_path):
    from tam import api, export_batch
    folder=tmp_path/'empty-export'
    folder.mkdir()
    archive=folder/'accounts-export.zip'
    archive.write_bytes(b'fixture')
    monkeypatch.setattr(export_batch,'build_export',lambda *a:(folder,archive,0,1))
    monkeypatch.setattr(api,'_export_lock',asyncio.Lock())
    with pytest.raises(api.HTTPException) as exc:
        await api.export_accounts_batch(api.BatchExportIn(ids=[1]))
    assert exc.value.status_code == 400
    assert not folder.exists()
