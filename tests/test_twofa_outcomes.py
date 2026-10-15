from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from tam.db import Database, Account
from tam import toolbox


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,expected', [('ok','success'),('wrong','failed'),('timeout','uncertain')])
async def test_change_result_is_persistent_and_separate_from_online_status(tmp_path, monkeypatch, kind, expected):
    db=Database(tmp_path/'fixture.db')
    acc=db.add_account(Account(label='fixture',status='active'))
    @asynccontextmanager
    async def session(aid):
        yield object()
    async def change(*args):
        if kind=='timeout': raise TimeoutError('private-value')
        if kind=='wrong':
            from telethon.errors import PasswordHashInvalidError
            raise PasswordHashInvalidError(None)
        return {'has_password':True}
    monkeypatch.setattr(toolbox,'_run_saved_twofa',change)
    manager=SimpleNamespace(db=db,session=session)
    try:
        if kind=='ok': await toolbox.run_op(manager,acc.id,'twofa',{'new':'private-password'})
        else:
            with pytest.raises(Exception):
                await toolbox.run_op(manager,acc.id,'twofa',{'new':'private-password'})
        result=db.twofa_results()[acc.id]
        assert result['status']==expected
        assert 'private' not in str(result)
        assert db.get(acc.id).status=='active'
        again=Database(db.path) if hasattr(db,'path') else Database(tmp_path/'fixture.db')
        try: assert again.twofa_results()[acc.id]['status']==expected
        finally: again.conn.close()
    finally: db.conn.close()


def test_new_attempt_clears_success_and_stale_completion_cannot_overwrite(tmp_path):
    db=Database(tmp_path/'fixture.db')
    try:
        a=db.begin_twofa_result(1)
        db.finish_twofa_result(1,a,'success','已修改')
        b=db.begin_twofa_result(1)
        assert db.twofa_results()[1]['status']=='running'
        db.finish_twofa_result(1,a,'success','旧结果')
        db.finish_twofa_result(1,b,'failed','二验错误')
        assert db.twofa_results()[1]['status']=='failed'
    finally: db.conn.close()


def test_old_one_stop_results_visible_without_replaying_password_changes(tmp_path):
    from tam.one_stop import OneStopWorkflow
    from tam.tasks import TaskStore, TaskRunner
    db=Database(tmp_path/'history.db')
    try:
        ids=[db.add_account(Account(label=str(i),phone=str(i),code_url='https://example.invalid')).id for i in range(3)]
        store=TaskStore(db.conn)
        manager=SimpleNamespace(db=db,s=SimpleNamespace(master_key='fixture'))
        flow=OneStopWorkflow(store,TaskRunner(store),manager,AsyncMock(),str)
        task=flow.create(ids,concurrency=1,new_password='secret',fmt='pack')
        flow.phase(task.id,ids[0],'ready')
        flow.phase(task.id,ids[1],'uncertain')
        flow.phase(task.id,ids[2],'login')
        store.set_status(task.id,'done')
        result=db.twofa_results()
        assert [result[i]['status'] for i in ids]==['success','uncertain','failed']
        new=flow.create([ids[0]],concurrency=1,new_password='new-secret',fmt='pack')
        assert db.twofa_results()[ids[0]]['status']=='running'
        assert db.twofa_results()[ids[0]]['task_id']==new.id
        assert 'secret' not in str(result)
    finally: db.conn.close()
