import asyncio
import unittest
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from telethon.crypto import AuthKey
from telethon.sessions import StringSession
from tam.manager import AccountManager


class ExportTdataTests(unittest.IsolatedAsyncioTestCase):
    async def test_constructor_receives_memory_session_and_proxy(self):
        root = Path(__file__).parent / ('tdata-test-' + uuid4().hex)
        root.mkdir()
        self.addCleanup(shutil.rmtree, root)
        session = StringSession()
        session.set_dc(2, '149.154.167.51', 443)
        session.auth_key = AuthKey(bytes(range(256)))
        account = SimpleNamespace(id=1, label='fixture', proxy=None)
        mgr = AccountManager.__new__(AccountManager)
        mgr.s = SimpleNamespace(api_id=0, api_hash='', default_proxy=None)
        mgr.db = SimpleNamespace(get=Mock(return_value=account), log=Mock())
        mgr._load_session = Mock(return_value=session.save())
        def construct(value, **kwargs):
            self.assertIsInstance(value, StringSession)
            self.assertIn('proxy', kwargs)
            return client
        def save(path):
            Path(path).mkdir()
            return True
        desktop = SimpleNamespace(SaveTData=Mock(side_effect=save))
        client = SimpleNamespace(connect=AsyncMock(), is_user_authorized=AsyncMock(return_value=True),
                                 ToTDesktop=AsyncMock(return_value=desktop), disconnect=AsyncMock())
        report = SimpleNamespace(ok=True, accounts=[SimpleNamespace(main_dc=2,
                                      auth_keys={2:session.auth_key.key})])
        with patch('opentele.tl.TelegramClient', side_effect=construct), \
             patch('tam.tdata_native.read_tdata', return_value=report):
            result = await mgr.export_tdata(1, str(root))
        self.assertTrue(result['ok'])
        client.disconnect.assert_awaited_once()

    async def test_transient_incomplete_read_retries_connection(self):
        root = Path(__file__).parent / ('tdata-retry-' + uuid4().hex)
        root.mkdir()
        self.addCleanup(shutil.rmtree, root)
        session = StringSession()
        session.set_dc(2, '149.154.167.51', 443)
        session.auth_key = AuthKey(bytes(range(256)))
        account = SimpleNamespace(id=2, label='retry', proxy=None)
        mgr = AccountManager.__new__(AccountManager)
        mgr.s = SimpleNamespace(api_id=0, api_hash='', default_proxy=None)
        mgr.db = SimpleNamespace(get=Mock(return_value=account), log=Mock())
        mgr._load_session = Mock(return_value=session.save())
        desktop = SimpleNamespace(SaveTData=Mock(side_effect=lambda p: (Path(p).mkdir(), True)[1]))
        client = SimpleNamespace(
            connect=AsyncMock(side_effect=[asyncio.IncompleteReadError(b'', 8), None]),
            is_user_authorized=AsyncMock(return_value=True),
            ToTDesktop=AsyncMock(return_value=desktop), disconnect=AsyncMock(),
        )
        report = SimpleNamespace(ok=True, accounts=[SimpleNamespace(
            main_dc=2, auth_keys={2: session.auth_key.key})])
        with patch('opentele.tl.TelegramClient', return_value=client), \
             patch('tam.tdata_native.read_tdata', return_value=report), \
             patch('tam.manager.asyncio.sleep', new=AsyncMock()):
            result = await mgr.export_tdata(2, str(root))
        self.assertTrue(result['ok'])
        self.assertEqual(client.connect.await_count, 2)
