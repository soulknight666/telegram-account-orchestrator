import json
import shutil
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from telethon.crypto import AuthKey
from telethon.sessions import SQLiteSession, StringSession

from tam.crypto import encrypt, generate_master_key
from tam.db import Account, Database
from tam.export_batch import build_export
from tam.manager import AccountManager


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / ('export-test-' + uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        # Inherit normal workspace ACLs in Windows test sandboxes.
        def mkdtemp(suffix='', prefix='tmp', dir=None):
            folder = Path(dir or self.root) / ((prefix or 'tmp') + uuid4().hex + (suffix or ''))
            folder.mkdir()
            return str(folder)
        self.temp = patch('tempfile.mkdtemp', mkdtemp)
        self.temp.start()
        self.addCleanup(self.temp.stop)
        self.s = SimpleNamespace(db_path=self.root / 'test.db', master_key=generate_master_key(),
                                 api_id=123, api_hash='fixture', global_rate=10)
        self.db = Database(self.s.db_path)
        self.addCleanup(self.db.conn.close)
        session = StringSession()
        session.set_dc(2, '149.154.167.51', 443)
        session.auth_key = AuthKey(bytes(range(256)))
        self.session = session.save()
        self.enc = encrypt(self.s.master_key, self.session)
        self.password = encrypt(self.s.master_key, 'latest-fixture')

    def add(self, label, **kw):
        a = self.db.add_account(Account(label=label))
        self.db.update(a.id, session_enc=self.enc, **kw)
        return a.id

    def test_pack_avoids_per_account_disk_export_and_preserves_latest_password(self):
        aid = self.add('../同名', twofa_enc=self.password, has_2fa=1)
        with patch.object(AccountManager, 'export_account_pack', side_effect=AssertionError('slow path')):
            folder, path, ok, failed = build_export(self.s, [aid, aid], 'pack')
        self.assertEqual((ok, failed), (1, 0))
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            meta = json.loads(z.read(next(n for n in names if n.endswith('.json') and n != 'summary.json')))
            self.assertEqual((meta['twofa'], meta['password']), ('latest-fixture', 'latest-fixture'))
            self.assertEqual(z.read(f'{aid}/2fa.txt').decode().strip(), 'latest-fixture')
            self.assertEqual(json.loads(z.read('summary.json')), {'ok': 1, 'failed': 0})
            session_file = self.root / 'roundtrip.session'
            session_file.write_bytes(z.read(next(n for n in names if n.endswith('.session'))))
        dst = SQLiteSession(str(session_file))
        try:
            self.assertEqual(StringSession.save(dst), self.session)
        finally:
            dst.close()
        self.assertEqual([p.name for p in folder.iterdir()], ['accounts-export.zip'])
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM audit_log').fetchone()[0], 1)

    def test_session_format_skips_invalid_rows_and_deduplicates(self):
        aid = self.add('valid')
        bad = self.add('broken')
        self.db.update(bad, session_enc='corrupt')
        _, path, ok, failed = build_export(self.s, [bad, aid, 999, aid], 'session')
        self.assertEqual((ok, failed), (1, 2))
        with zipfile.ZipFile(path) as z:
            self.assertEqual(set(z.namelist()), {f'{aid}/account.session', 'summary.json', 'failures.jsonl'})
            errors = [json.loads(s) for s in z.read('failures.jsonl').splitlines()]
            self.assertEqual([r['id'] for r in errors], [bad, 999])

    def test_no_password_marker_and_duplicate_labels_stay_in_account_folders(self):
        a = self.add('a/b', has_2fa=0)
        b = self.add('ab', has_2fa=None)
        _, path, ok, failed = build_export(self.s, [a, b], 'pack')
        self.assertEqual((ok, failed), (2, 0))
        with zipfile.ZipFile(path) as z:
            self.assertIn(f'{a}/ab.session', z.namelist())
            self.assertIn(f'{b}/ab.session', z.namelist())
            self.assertIn(f'{a}/2fa.txt', z.namelist())
            self.assertNotIn(f'{b}/2fa.txt', z.namelist())


if __name__ == '__main__':
    unittest.main()
