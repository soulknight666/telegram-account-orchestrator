"""Offline packaged smoke test; synthetic credentials, isolated database."""
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace


def run(report_path):
    import tkinter as tk
    from telethon.sessions import StringSession, SQLiteSession
    from telethon.crypto import AuthKey
    from .crypto import generate_master_key, encrypt
    from .db import Database, Account
    from .export_batch import build_export
    import zipfile

    root = tk.Tk()
    root.withdraw()
    root.update()
    root.destroy()
    with tempfile.TemporaryDirectory(prefix='tao-selftest-') as temp:
        settings = SimpleNamespace(db_path=Path(temp) / 'test.db', master_key=generate_master_key(),
                                   api_id=12345, api_hash='fixture')
        db = Database(settings.db_path)
        try:
            source = StringSession()
            source.set_dc(2, '149.154.167.51', 443)
            source.auth_key = AuthKey(bytes(range(256)))
            account = db.add_account(Account(label='selftest'))
            db.update(account.id, session_enc=encrypt(settings.master_key, source.save()),
                      has_2fa=1, twofa_enc=encrypt(settings.master_key, 'fixture-new'))
            folder, output, ok, failed = build_export(settings, [account.id], 'pack')
            try:
                with zipfile.ZipFile(output) as z:
                    assert z.testzip() is None
                    meta = json.loads(z.read(f'{account.id}/selftest.json'))
                    assert meta['twofa'] == 'fixture-new'
                    path = Path(temp) / 'roundtrip.session'
                    path.write_bytes(z.read(f'{account.id}/selftest.session'))
                session = SQLiteSession(str(path))
                try:
                    assert StringSession.save(session) == source.save()
                finally:
                    session.close()
                report_path.write_text(json.dumps({'tk': True, 'export_ok': ok, 'failed': failed,
                    'export_directory': str(output.parent.parent), 'session_roundtrip': True,
                    'twofa': 'verified', 'frozen': True}), encoding='utf-8')
            finally:
                shutil.rmtree(folder)
        finally:
            db.conn.close()
