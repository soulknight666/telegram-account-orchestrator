import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
from tam.codefetch import extract_twofa
from tam.manager import AccountManager

class PageTwofaRegression(unittest.TestCase):
    def test_named_fields(self):
        for raw in ['<input id="pass2fa" value="fixture-pass">', '<input name="text" id="pass2fa" value="fixture-pass">']:
            with self.subTest(raw=raw):
                self.assertEqual(extract_twofa(raw), 'fixture-pass')
    def test_numeric_password(self):
        self.assertEqual(extract_twofa('{"twofa":"123456"}'), '123456')
        self.assertEqual(extract_twofa('<input id="twofa" value="12345">'), '12345')
    def test_ordinary_code_is_not_password(self):
        self.assertIsNone(extract_twofa('{"code":"12345"}'))
        self.assertIsNone(extract_twofa('<input id="code" value="12345">'))
    def test_auto_login_submits_page_password_once(self):
        async def run():
            m = AccountManager.__new__(AccountManager)
            m.s = SimpleNamespace(default_proxy=None)
            m.db = Mock()
            m.db.get.return_value = SimpleNamespace(code_url='https://example.invalid/GetHTML', proxy=None, twofa_enc=None)
            m.send_code = AsyncMock()
            m.sign_in = AsyncMock(return_value={'ok':True})
            with patch('tam.codefetch.prepare_code_source', AsyncMock(return_value={'pre_read_skipped':True})), patch('tam.codefetch.wait_for_code_details', AsyncMock(return_value=('12345','fixture-pass'))):
                await m._auto_login_impl(1, None, 2, 0, 1, None)
            m.sign_in.assert_awaited_once_with(1, '12345', 'fixture-pass', retain_invalid_code=True)
        asyncio.run(run())

if __name__ == '__main__':
    unittest.main()
