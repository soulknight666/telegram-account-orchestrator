"""Disk-backed ZIP, bounded offline encoding pool, one DB/ZIP writer."""
import json
import os
import shutil
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .db import Account, Database
from .export_encoding import export_entries


def _encode(settings, account, fmt):
    try:
        return export_entries(settings, account, fmt), None
    except Exception as exc:
        # Keep decrypted values and exception text out of failure reports.
        return None, type(exc).__name__


def build_export(settings, ids, fmt):
    if fmt not in ('pack', 'session'):
        raise ValueError('unsupported_export_format')
    export_root = os.getenv('TAM_EXPORT_DIR', '').strip()
    if export_root:
        Path(export_root).mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='tao_export_', dir=export_root or None))
    database = None
    try:
        database = Database(settings.db_path)
        output = folder / 'accounts-export.zip'
        ok = failed = 0
        report = folder / 'failures.jsonl'
        ordered = list(dict.fromkeys(ids))
        # Each scrypt consumes ~32 MiB. Never fan out to account count/CPU count.
        workers = min(4, os.cpu_count() or 1)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='tao-export') as pool, \
                report.open('w', encoding='utf-8') as errors, zipfile.ZipFile(
                    output, 'w', zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
            for offset in range(0, len(ordered), 64):
                chunk = ordered[offset:offset + 64]
                placeholders = ','.join('?' for _ in chunk)
                accounts = {r['id']: Account.from_row(r) for r in database.conn.execute(
                    f'SELECT * FROM accounts WHERE id IN ({placeholders})', chunk).fetchall()}
                # One short DB snapshot per chunk; threads never use this connection.
                results = pool.map(lambda aid: _encode(settings, accounts.get(aid), fmt), chunk)
                audit = []
                for aid, (entries, error) in zip(chunk, results):
                    if error:
                        failed += 1
                        errors.write(json.dumps({'id': aid, 'error': error}) + '\n')
                        continue
                    # ZIP write errors invalidate the archive, not just the account.
                    for name, data in entries:
                        archive.writestr(name, data)
                    ok += 1
                    audit.append((aid, 'export_session_file' if fmt == 'session' else 'export_session_pack',
                                  1, 'batch_export', time.time()))
                # Never hold a write transaction during crypto/compression.
                if audit:
                    with database.conn:
                        database.conn.executemany(
                            'INSERT INTO audit_log (account_id,action,ok,detail,created_at) VALUES (?,?,?,?,?)', audit)
            archive.writestr('summary.json', json.dumps({'ok': ok, 'failed': failed}))
            errors.flush()
            archive.write(report, 'failures.jsonl')
        report.unlink()
        return folder, output, ok, failed
    except BaseException:
        shutil.rmtree(folder)
        raise
    finally:
        if database is not None:
            database.conn.close()
