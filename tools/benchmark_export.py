"""Offline export benchmark; synthetic credentials only, no Telegram connections.
Run from repo root: python tools/benchmark_export.py --count 128
Optional --baseline points at a saved previous tam/export_batch.py.
"""
import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telethon.crypto import AuthKey
from telethon.sessions import StringSession
from tam.crypto import encrypt, generate_master_key
from tam.db import Database
from tam.export_batch import build_export


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--count', type=int, default=128)
    ap.add_argument('--baseline', type=Path)
    args = ap.parse_args()
    if args.count < 1:
        ap.error('count must be positive')
    root = Path(__file__).resolve().parents[1] / 'tmp' / ('export-bench-' + uuid4().hex)
    root.mkdir(parents=True)
    def mkdtemp(suffix=None, prefix=None, dir=None):
        p = Path(dir or root) / ((prefix or 'tmp') + uuid4().hex + (suffix or ''))
        p.mkdir()
        return str(p)
    db = None
    try:
        s = SimpleNamespace(db_path=root / 'fixture.db', master_key=generate_master_key(), api_id=123, api_hash='fixture', global_rate=10)
        src = StringSession()
        src.set_dc(2, '149.154.167.51', 443)
        src.auth_key = AuthKey(bytes(range(256)))
        # Unique salts per row, encryption setup excluded from export timings.
        db = Database(s.db_path)
        rows = [(f'fixture-{i}', encrypt(s.master_key, src.save()), encrypt(s.master_key, f'fixture-{i}'), time.time(), time.time()) for i in range(args.count)]
        with db.conn:
            db.conn.executemany('INSERT INTO accounts (label,session_enc,twofa_enc,created_at,updated_at) VALUES (?,?,?,?,?)', rows)
        ids = list(range(1, args.count + 1))
        builds = []
        if args.baseline:
            spec = importlib.util.spec_from_file_location('tam.export_baseline_bench', args.baseline)
            old = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(old)
            builds.append(('before', old.build_export))
        builds.append(('after', build_export))
        for label, fn in builds:
            with patch.object(tempfile, 'mkdtemp', mkdtemp):
                start = time.perf_counter()
                folder, path, ok, failed = fn(s, ids, 'pack')
                elapsed = time.perf_counter() - start
            with zipfile.ZipFile(path) as z:
                assert z.testzip() is None
                assert json.loads(z.read('summary.json')) == {'ok': args.count, 'failed': 0}
            print(json.dumps({'version': label, 'count': args.count, 'seconds': round(elapsed, 3), 'accounts_per_second': round(ok / elapsed, 2), 'zip_bytes': path.stat().st_size, 'ok': ok, 'failed': failed}), flush=True)
            shutil.rmtree(folder)
    finally:
        if db:
            db.conn.close()
        shutil.rmtree(root)


if __name__ == '__main__':
    main()
