import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tam.crypto import encrypt, decrypt, generate_master_key
from tam.db import Account, Database
from tam import toolbox

class AdaptiveTimingTests(unittest.TestCase):
    def test_learning_persists_and_manual_mode_is_exact(self):
        from tam.login_timing import LoginTiming
        db = Database(':memory:')
        self.addCleanup(db.conn.close)
        url = 'https://example.invalid/private/GetHTML?token=secret'
        for _ in range(3):
            timing = LoginTiming(db, url, 60, 1, True)
            timing.success(60, 0)
        timing = LoginTiming(db, url, 60, 1, True)
        self.assertLess(timing.delay, 60)
        self.assertGreaterEqual(timing.interval, 60)
        timing.success(115, 1)
        self.assertGreater(LoginTiming(db, url, 60, 1, True).delay, timing.delay)
        manual = LoginTiming(db, url, 23, 75, False)
        self.assertEqual((manual.delay, manual.interval), (23, 75))
        self.assertNotIn('secret', str(db.conn.execute('SELECT * FROM settings').fetchall()))
        self.assertEqual(LoginTiming(db, 'https://other.invalid/GetHTML', 40, 1, True).delay, 40)

    def test_corrupt_statistics_are_ignored(self):
        from tam.login_timing import LoginTiming
        db = SimpleNamespace(get_setting=lambda *args: '{broken')
        timing = LoginTiming(db, 'https://example.invalid/code', 30, 5, True)
        self.assertEqual((timing.delay, timing.interval), (30, 5))

class SavedTwofaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(':memory:')
        self.key = generate_master_key()
        self.client = SimpleNamespace(is_user_authorized=AsyncMock(return_value=True),
                                      edit_2fa=AsyncMock(), __call__=None)
        self.manager = SimpleNamespace(db=self.db, s=SimpleNamespace(master_key=self.key))
        @asynccontextmanager
        async def session(aid):
            yield self.client
        self.manager.session = session

    def add_account(self, acc):
        self.db.add_account(acc)
        if acc.twofa_enc:
            self.db.update(acc.id, twofa_enc=acc.twofa_enc)
        return acc

    async def asyncTearDown(self):
        self.db.conn.close()

    async def test_saved_password_overrides_batch_fallback_and_is_replaced(self):
        acc = self.add_account(Account(label='one', twofa_enc=encrypt(self.key, 'saved')))
        params = {'old':'fallback', 'new':'new-fixture'}
        fn = AsyncMock(return_value={'has_password':True})
        with patch.dict(toolbox.OPS, {'twofa':fn}):
            await toolbox.run_op(self.manager, acc.id, 'twofa', params)
        self.assertEqual(fn.call_args.args[1]['old'], 'saved')
        self.assertEqual(params['old'], 'fallback')
        self.assertEqual(decrypt(self.key, self.db.get(acc.id).twofa_enc), 'new-fixture')

    async def test_corrupt_saved_password_is_actionable_without_remote_mutation(self):
        acc = self.add_account(Account(label='bad', twofa_enc='broken'))
        fn = AsyncMock()
        with patch.dict(toolbox.OPS, {'twofa':fn}):
            with self.assertRaisesRegex(toolbox.ToolboxError, '已保存.*解密'):
                await toolbox.run_op(self.manager, acc.id, 'twofa', {'new':'new-fixture'})
        fn.assert_not_awaited()

    async def test_remote_change_failure_preserves_saved_password(self):
        acc = self.add_account(Account(label='remote-failure', twofa_enc=encrypt(self.key, 'old-fixture')))
        fn = AsyncMock(side_effect=RuntimeError('remote rejected'))
        with patch.dict(toolbox.OPS, {'twofa': fn}):
            with self.assertRaisesRegex(RuntimeError, 'remote rejected'):
                await toolbox.run_op(self.manager, acc.id, 'twofa', {'new':'new-fixture'})
        self.assertEqual(decrypt(self.key, self.db.get(acc.id).twofa_enc), 'old-fixture')

    async def test_export_metadata_reads_new_password_after_remote_success(self):
        from tam.manager import AccountManager
        import json
        from pathlib import Path
        from uuid import uuid4
        acc = self.add_account(Account(label='export-fixture', twofa_enc=encrypt(self.key, 'old-fixture')))
        with patch.dict(toolbox.OPS, {'twofa': AsyncMock(return_value={'has_password':True})}):
            await toolbox.run_op(self.manager, acc.id, 'twofa', {'new':'new-fixture'})
        manager = AccountManager.__new__(AccountManager)
        manager.db = self.db
        manager.s = SimpleNamespace(master_key=self.key, api_id=1, api_hash='fixture')
        manager._load_session = lambda acc: 'fixture'
        manager.string_to_session_file = lambda session, path: path
        # Normal inherited permissions avoid Windows sandbox's mode-0700 temp ACL issue.
        folder = Path(__file__).parent / ('export-fixture-' + uuid4().hex)
        folder.mkdir()
        try:
            pack = manager.export_account_pack(acc.id, str(folder))
            meta = json.loads(Path(pack['json']).read_text(encoding='utf-8'))
            self.assertEqual(meta['twofa'], 'new-fixture')
            self.assertEqual(meta['password'], 'new-fixture')
            self.assertEqual((Path(folder)/'2fa.txt').read_text(encoding='utf-8').strip(), 'new-fixture')
        finally:
            (folder / 'export-fixture.json').unlink(missing_ok=True)
            (folder / '2fa.txt').unlink(missing_ok=True)
            folder.rmdir()

    async def test_no_saved_password_uses_fallback(self):
        acc = self.add_account(Account(label='empty'))
        fn = AsyncMock(return_value={'has_password':True})
        with patch.dict(toolbox.OPS, {'twofa':fn}):
            await toolbox.run_op(self.manager, acc.id, 'twofa', {'old':'manual','new':'new-fixture'})
        self.assertEqual(fn.call_args.args[1]['old'], 'manual')

    async def test_stale_saved_password_ignored_when_server_has_no_twofa(self):
        client = AsyncMock()
        client.side_effect = [SimpleNamespace(has_password=False), SimpleNamespace(has_password=True)]
        result = await toolbox.op_twofa(client, {'old':'stale', 'new':'new-fixture', '_old_from_saved':True})
        client.edit_2fa.assert_awaited_once_with(new_password='new-fixture')
        self.assertTrue(result['has_password'])

class PollingTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_poll_respects_gethtml_floor_and_retry_after(self):
        from tam import codefetch
        from urllib.error import HTTPError
        clock = [0.0]
        reads = []
        async def sleep(seconds):
            clock[0] += seconds
        async def read(*args, **kwargs):
            reads.append(clock[0])
            if len(reads) == 1:
                raise HTTPError('https://example.invalid/code', 429, 'rate', {'Retry-After':'90'}, None)
            if len(reads) == 2:
                return None, None
            return '12345', 'fixture'
        with patch.object(codefetch.time, 'monotonic', lambda: clock[0]), patch.object(codefetch.asyncio, 'sleep', sleep), patch.object(codefetch, 'read_page', read):
            result = await codefetch.wait_for_code_details('https://retry.invalid/GetHTML', timeout=200, interval=1)
        self.assertEqual(result, ('12345', 'fixture'))
        self.assertEqual(reads, [0, 90, 150])

    async def test_cooldown_page_is_not_a_code(self):
        from tam import codefetch
        with patch.object(codefetch, 'fetch_text', return_value='请等待60秒后刷新，code 12345'):
            with self.assertRaises(codefetch.CodeSourceCooldown) as caught:
                await codefetch.read_page('https://example.invalid/GetHTML')
        self.assertGreaterEqual(caught.exception.seconds, 60)

if __name__ == '__main__':
    unittest.main()
