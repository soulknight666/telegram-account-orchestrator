"""Pure offline account pack encoding; no account DB access or network calls."""
import json
import sqlite3
import tempfile
from pathlib import Path
from .crypto import decrypt


def pack_metadata(settings, acc):
    safe = "".join(c for c in (acc.label or f"acc{acc.id}")
                   if c.isalnum() or c in "-_.") or f"acc{acc.id}"
    meta = {
        "app_id": settings.api_id,
        "app_hash": settings.api_hash,
        "sdk": acc.app_version or "",
        "device": acc.device_model or "",
        "app_version": acc.app_version or "",
        "system_version": "",
        "lang_code": acc.lang_code or "zh",
        "system_lang_pack": acc.lang_code or "zh",
        "user_id": acc.user_id,
        "phone": acc.phone or "",
        "username": acc.username or "",
        "session_file": safe,
        "label": acc.label,
        "id": acc.id,
        "has_2fa": acc.has_2fa,
    }
    twofa_plain = ""
    if getattr(acc, "twofa_enc", None):
        try:
            from .crypto import decrypt
            twofa_plain = decrypt(settings.master_key, acc.twofa_enc)
        except Exception:
            twofa_plain = ""
    meta["twofa"] = twofa_plain
    meta["password"] = twofa_plain
    return safe, meta, twofa_plain


def session_bytes(session_str):
    """Use Telethon's installed schema rather than duplicating its SQL."""
    from telethon.sessions import SQLiteSession, StringSession
    src = StringSession(session_str)
    if not src.auth_key:
        raise ValueError('missing_auth_key')
    dst = SQLiteSession()
    try:
        dst.set_dc(src.dc_id, src.server_address, src.port)
        dst.auth_key = src.auth_key
        dst.save()
        if hasattr(dst._conn, 'serialize'):
            return dst._conn.serialize()
        # Python 3.10 / SQLite builds without serialize retain compatibility.
        with tempfile.TemporaryDirectory(prefix='tao_session_') as folder:
            path = Path(folder) / 'account.session'
            conn = sqlite3.connect(path)
            try:
                dst._conn.backup(conn)
            finally:
                conn.close()
            return path.read_bytes()
    finally:
        # Telethon.close deliberately leaves :memory: connections open.
        if dst._conn is not None:
            dst._conn.close()
            dst._conn = None


def export_entries(settings, account, fmt):
    """Only one account's archive entries in each worker result."""
    if account is None or not account.session_enc:
        raise ValueError('missing_session')
    data = session_bytes(decrypt(settings.master_key, account.session_enc))
    if fmt == 'session':
        return [(f'{account.id}/account.session', data)]
    stem, meta, password = pack_metadata(settings, account)
    entries = [(f'{account.id}/{stem}.session', data),
               (f'{account.id}/{stem}.json', json.dumps(meta, ensure_ascii=False, indent=2).encode('utf-8'))]
    if password or account.has_2fa == 0:
        entries.append((f'{account.id}/2fa.txt', ((password or '无2FA密码。') + '\n').encode('utf-8')))
    return entries
