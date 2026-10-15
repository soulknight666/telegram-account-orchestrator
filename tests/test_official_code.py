import unittest
import ast
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI, Depends, HTTPException, Response
from fastapi.testclient import TestClient

from tam.manager import AccountManager


class OfficialCodeTests(unittest.IsolatedAsyncioTestCase):
    def manager(self, messages=(), error=None):
        client = SimpleNamespace(get_messages=AsyncMock(return_value=messages))
        self.exited = False

        @asynccontextmanager
        async def session(account_id):
            self.assertEqual(account_id, 42)
            if error:
                raise error
            try:
                yield client
            finally:
                self.exited = True

        mgr = AccountManager.__new__(AccountManager)
        mgr.session = session
        mgr._pending = {}  # No pending login: use the saved authorized session.
        mgr.db = SimpleNamespace(log=Mock())
        return mgr, client

    def message(self, text, seconds=0, **kw):
        return SimpleNamespace(message=text, date=datetime.now(timezone.utc)-timedelta(seconds=seconds),
                               id=9, sender_id=777000, out=False, **kw)

    async def test_saved_session_reads_code_without_sending_or_logging_it(self):
        mgr, client = self.manager([self.message('Login code: 54321. Do not share this code.')])
        result = await mgr.read_official_code(42)
        self.assertEqual(result['code'], '54321')
        self.assertIsNotNone(result['date'])
        client.get_messages.assert_awaited_once_with(777000, limit=10)
        mgr.db.log.assert_not_called()
        self.assertTrue(self.exited)

    async def test_ignores_old_messages_and_unrelated_numbers(self):
        # Stay just beyond the one-hour freshness boundary; exact-boundary
        # timestamps can round differently across Windows/Python versions.
        mgr, _ = self.manager([self.message('Device version: 12345'),
                               self.message('Login code: 54321', seconds=3605)])
        self.assertIsNone((await mgr.read_official_code(42))['code'])

    async def test_chinese_code(self):
        mgr, _ = self.manager([self.message('登录验证码：12345。请勿泄露。')])
        self.assertEqual((await mgr.read_official_code(42))['code'], '12345')

    async def test_indonesian_login_code(self):
        mgr, _ = self.manager([self.message('Kode masuk Anda: 54321 Jangan pernah memberikan kode ini ke siapapun!')])
        self.assertEqual((await mgr.read_official_code(42))['code'], '54321')

    async def test_no_session_does_not_start_login(self):
        mgr, client = self.manager(error=RuntimeError('会话已失效'))
        with self.assertRaisesRegex(RuntimeError, '会话已失效'):
            await mgr.read_official_code(42)
        client.get_messages.assert_not_awaited()

    def test_entry_not_in_login_modal(self):
        web = Path(__file__).resolve().parents[1] / 'tam' / 'web'
        html = (web / 'index.src.html').read_text(encoding='utf-8')
        js = (web / 'app.js').read_text(encoding='utf-8')
        self.assertNotIn('lReadOfficialBtn', html)
        self.assertIn('openOfficialCode(${a.id})', js)
        self.assertIn('id="mOfficialCode"', html)

    def test_api_uses_saved_session_service_and_disables_cache(self):
        path = Path(__file__).resolve().parents[1] / 'tam' / 'api.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == 'official_login_code')
        app = FastAPI()
        mgr = SimpleNamespace(read_official_code=AsyncMock(return_value={'code': None}))
        db = SimpleNamespace(get=Mock(return_value=object()))
        env = dict(app=app, Depends=Depends, auth=lambda: None, HTTPException=HTTPException,
                   Response=Response, Any=object, db=db, manager=mgr, _login_busy=lambda _: False)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), env)
        with TestClient(app) as client:
            for route in ('official-code', 'login/official-code'):
                result = client.get(f'/api/accounts/42/{route}')
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.headers['cache-control'], 'no-store')
            mgr.read_official_code.assert_awaited_with(42)
            db.get.return_value = None
            self.assertEqual(client.get('/api/accounts/42/official-code').status_code, 404)
