import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from tam import toolbox

class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_twofa_does_not_repeat_session_authorization(self):
        client = SimpleNamespace(is_user_authorized=AsyncMock())
        @asynccontextmanager
        async def session(aid):
            yield client
        mgr = SimpleNamespace(session=session)
        with patch.object(toolbox, '_run_saved_twofa', AsyncMock(return_value={'has_password':True})):
            await toolbox.run_op(mgr, 1, 'twofa', {'new':'fixture'})
        client.is_user_authorized.assert_not_awaited()

    async def test_operation_runs_before_disconnect(self):
        opened = False
        @asynccontextmanager
        async def session(aid):
            nonlocal opened
            opened = True
            yield SimpleNamespace(is_user_authorized=AsyncMock(return_value=True))
            opened = False
        async def operation(client, params):
            self.assertTrue(opened)
            return {}
        with patch.dict(toolbox.OPS, {'twofa_status': operation}):
            await toolbox.run_op(SimpleNamespace(session=session), 1, 'twofa_status', {})

    async def test_twofa_batch_has_no_random_stagger(self):
        mgr = SimpleNamespace(db=SimpleNamespace(get=lambda aid: SimpleNamespace(status='active',session_enc='fixture')), run_batch=AsyncMock(return_value=[]))
        await toolbox.run_op_batch(mgr, [1,1], 'twofa', {'new':'fixture'}, concurrency=12)
        self.assertEqual(mgr.run_batch.call_args.args[0], [1])
        self.assertFalse(mgr.run_batch.call_args.kwargs['stagger'])

if __name__ == '__main__': unittest.main()
