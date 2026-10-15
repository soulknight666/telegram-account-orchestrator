"""SQLite 存储层：账号、代理、操作审计日志。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    label         TEXT    NOT NULL UNIQUE,   -- 自定义别名
    phone         TEXT,
    user_id       INTEGER,
    username      TEXT,
    session_enc   TEXT,                      -- 加密后的 StringSession
    proxy         TEXT,                      -- socks5://user:pass@host:port
    code_url      TEXT,                      -- 发卡平台的取码链接（.../GetHTML）
    device_model  TEXT,
    app_version   TEXT,
    system_version TEXT,
    lang_code     TEXT DEFAULT 'zh',
    tags          TEXT DEFAULT '[]',
    status        TEXT DEFAULT 'new',        -- new|active|spam_block|spam_block_perm|frozen|unauthorized|restricted|banned|error
    status_note   TEXT,
    premium       INTEGER DEFAULT 0,         -- 是否 Telegram Premium
    locked        INTEGER DEFAULT 0,         -- 本地保护锁，锁定后禁止删除
    has_2fa       INTEGER,                   -- 1有二验 0无 NULL未知
    twofa_enc     TEXT,                        -- 二验密码密文（改二验时可选保存）
    spam_until    REAL,                      -- 临时 spam 限制解除时间戳
    last_warm_at  REAL,                      -- 上次养号时间
    auto_kick     INTEGER DEFAULT 1,         -- 是否参与自动清设备
    auto_kick_hours REAL,                    -- 该号自定义周期（小时），空=用全局
    auto_kick_loop INTEGER DEFAULT 1,        -- 1=踢成功后按周期循环；0=只踢一次
    login_at      REAL,                      -- 服务端会话真实创建时间（date_created，仅供参考）
    adopted_at    REAL,                      -- 本机接管时间（导入/登录成功那一刻）= 清设备计时起点
    last_kick_at  REAL,                      -- 上次成功清设备的时间
    kick_retry_at REAL,                      -- 清设备未成功时的下次重试时间
    last_check_at REAL,
    last_check_result TEXT,
    last_check_error TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS account_changes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    action     TEXT NOT NULL,
    changed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_account_changes_time
    ON account_changes(changed_at, id);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    action     TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    detail     TEXT,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_account ON audit_log(account_id, created_at DESC);

CREATE TABLE IF NOT EXISTS twofa_results (
    account_id INTEGER PRIMARY KEY,
    attempt TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);

-- 运行时可改的设置（比如清设备失败后的重试间隔），优先级高于 .env
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at REAL NOT NULL
);

-- 错误日志：服务端未捕获异常、HTTP 5xx、前端上报，便于排查与用户反馈
CREATE TABLE IF NOT EXISTS error_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    level      TEXT    NOT NULL DEFAULT 'error',  -- error|warn|info
    source     TEXT    NOT NULL DEFAULT 'server', -- server|client|system
    path       TEXT,
    message    TEXT    NOT NULL,
    traceback  TEXT,
    meta       TEXT,                              -- JSON 附加信息
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_error_events_created ON error_events(created_at DESC);
"""


@dataclass
class Account:
    id: int | None = None
    label: str = ""
    phone: str | None = None
    user_id: int | None = None
    username: str | None = None
    session_enc: str | None = None
    proxy: str | None = None
    code_url: str | None = None
    device_model: str | None = None
    app_version: str | None = None
    system_version: str | None = None
    lang_code: str = "zh"
    tags: list[str] = field(default_factory=list)
    status: str = "new"
    status_note: str | None = None
    premium: int = 0
    locked: int = 0
    has_2fa: int | None = None
    twofa_enc: str | None = None
    spam_until: float | None = None
    last_warm_at: float | None = None
    auto_kick: int = 1
    auto_kick_hours: float | None = None
    auto_kick_loop: int = 1
    login_at: float | None = None
    adopted_at: float | None = None
    last_kick_at: float | None = None
    kick_retry_at: float | None = None
    last_check_at: float | None = None
    last_check_result: str | None = None
    last_check_error: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Account":
        data = dict(row)
        data["tags"] = json.loads(data.get("tags") or "[]")
        return cls(**data)

    def public(self) -> dict[str, Any]:
        """对外输出，绝不包含 session / 二验密文。"""
        d = self.__dict__.copy()
        d.pop("session_enc", None)
        d.pop("twofa_enc", None)
        d["authorized"] = bool(self.session_enc)
        d["has_twofa_saved"] = bool(self.twofa_enc)
        return d


class Database:
    def __init__(self, path: Path | str) -> None:
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()
        self._lock = asyncio.Lock()  # 并发写保护

    def _migrate(self) -> None:
        """旧库升级：缺列则补列。"""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(accounts)")}
        for name, ddl in (("code_url", "TEXT"), ("premium", "INTEGER DEFAULT 0"),
                          ("spam_until", "REAL"), ("last_warm_at", "REAL"),
                          ("auto_kick", "INTEGER DEFAULT 1"), ("auto_kick_hours", "REAL"), ("auto_kick_loop", "INTEGER DEFAULT 1"), ("login_at", "REAL"),
                          ("locked", "INTEGER DEFAULT 0"),
                          ("last_kick_at", "REAL"), ("adopted_at", "REAL"),
                          ("kick_retry_at", "REAL"), ("has_2fa", "INTEGER"), ("twofa_enc", "TEXT"),
                          ("last_check_result", "TEXT"), ("last_check_error", "TEXT")):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE accounts ADD COLUMN {name} {ddl}")
        # 老库补 adopted_at：没有接管时间的，用已有的 login_at / created_at 当起点，
        # 否则升级后所有号会多等一个周期才清设备。
        if "adopted_at" not in cols:
            self.conn.execute(
                "UPDATE accounts SET adopted_at = COALESCE(login_at, created_at) "
                "WHERE adopted_at IS NULL AND session_enc IS NOT NULL"
            )

    # --- accounts ---
    def add_account(self, acc: Account) -> Account:
        now = time.time()
        acc.created_at = acc.created_at or now
        acc.updated_at = now
        cur = self.conn.execute(
            """INSERT INTO accounts (label, phone, proxy, code_url, device_model, app_version,
               system_version, lang_code, tags, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (acc.label, acc.phone, acc.proxy, acc.code_url, acc.device_model, acc.app_version,
             acc.system_version, acc.lang_code, json.dumps(acc.tags, ensure_ascii=False),
             acc.status, acc.created_at, acc.updated_at),
        )
        self.conn.commit()
        acc.id = cur.lastrowid
        self._record_change(acc.id, "upsert")
        return acc

    def add_accounts_bulk(self, accounts: list[Account], chunk_size: int = 500) -> list[Account]:
        """分块批量写入，减少万级导入事务次数。"""
        if not accounts: return []
        now = time.time()
        sql = "INSERT INTO accounts (label,phone,proxy,code_url,device_model,app_version,system_version,lang_code,tags,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
        for start in range(0, len(accounts), max(1, chunk_size)):
            chunk = accounts[start:start + max(1, chunk_size)]
            for a in chunk:
                a.created_at = a.created_at or now; a.updated_at = now
            self.conn.executemany(sql, [(a.label,a.phone,a.proxy,a.code_url,a.device_model,a.app_version,a.system_version,a.lang_code,json.dumps(a.tags,ensure_ascii=False),a.status,a.created_at,a.updated_at) for a in chunk])
            for a in chunk:
                row = self.conn.execute("SELECT id FROM accounts WHERE label=?", (a.label,)).fetchone()
                a.id = row[0] if row else None
            self.conn.executemany(
                "INSERT INTO account_changes(account_id, action, changed_at) VALUES (?,?,?)",
                [(a.id, "upsert", time.time()) for a in chunk if a.id is not None],
            )
            self.conn.commit()
        return accounts

    def update(self, account_id: int, **fields: Any) -> None:
        if not fields:
            return
        if "tags" in fields and isinstance(fields["tags"], list):
            fields["tags"] = json.dumps(fields["tags"], ensure_ascii=False)
        fields["updated_at"] = time.time()
        sets = ", ".join(f"{k}=?" for k in fields)
        cur = self.conn.execute(
            f"UPDATE accounts SET {sets} WHERE id=?", (*fields.values(), account_id)
        )
        if cur.rowcount:
            self.conn.execute(
                "INSERT INTO account_changes(account_id, action, changed_at) VALUES (?,?,?)",
                (account_id, "upsert", time.time()),
            )
        self.conn.commit()

    def delete(self, account_id: int) -> None:
        cur = self.conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        if cur.rowcount:
            self.conn.execute(
                "INSERT INTO account_changes(account_id, action, changed_at) VALUES (?,?,?)",
                (account_id, "delete", time.time()),
            )
        self.conn.commit()

    def _record_change(self, account_id: int | None, action: str) -> None:
        if account_id is None:
            return
        self.conn.execute(
            "INSERT INTO account_changes(account_id, action, changed_at) VALUES (?,?,?)",
            (int(account_id), action, time.time()),
        )
        self.conn.commit()

    def changes_since(self, updated_since: float) -> dict[str, list[int]]:
        """返回游标之后发生过变化的账号与已删除账号。"""
        rows = self.conn.execute(
            "SELECT account_id, action FROM account_changes "
            "WHERE changed_at > ? ORDER BY changed_at, id",
            (float(updated_since),),
        ).fetchall()
        changed: list[int] = []
        deleted: list[int] = []
        for row in rows:
            target = changed if row["action"] != "delete" else deleted
            if row["account_id"] not in target:
                target.append(int(row["account_id"]))
        return {"changed_ids": changed, "deleted_ids": deleted}

    def get(self, account_id: int) -> Account | None:
        row = self.conn.execute(
            "SELECT * FROM accounts WHERE id=?", (account_id,)
        ).fetchone()
        return Account.from_row(row) if row else None

    def get_by_label(self, label: str) -> Account | None:
        row = self.conn.execute(
            "SELECT * FROM accounts WHERE label=?", (label,)
        ).fetchone()
        return Account.from_row(row) if row else None

    def list(self, status: str | None = None, tag: str | None = None) -> list[Account]:
        sql, params = "SELECT * FROM accounts", []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY id"
        rows = self.conn.execute(sql, params).fetchall()
        accounts = [Account.from_row(r) for r in rows]
        if tag:
            accounts = [a for a in accounts if tag in a.tags]
        return accounts

    # --- audit ---
    def log(self, account_id: int | None, action: str, ok: bool, detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO audit_log (account_id, action, ok, detail, created_at) VALUES (?,?,?,?,?)",
            (account_id, action, int(ok), detail[:2000], time.time()),
        )
        self.conn.commit()

    # --- settings（运行时可改的键值）---
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return default if row is None or row["value"] is None else str(row["value"])

    def set_setting(self, key: str, value: str | None) -> None:
        """value 传 None 则删除该项，回到 .env / 默认值。"""
        if value is None:
            self.conn.execute("DELETE FROM settings WHERE key=?", (key,))
        else:
            self.conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, str(value), time.time()),
            )
        self.conn.commit()

    def logs(self, account_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if account_id is None:
            rows: Iterable[sqlite3.Row] = self.conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            )
        else:
            rows = self.conn.execute(
                "SELECT * FROM audit_log WHERE account_id=? ORDER BY id DESC LIMIT ?",
                (account_id, limit),
            )
        return [dict(r) for r in rows]

    def begin_twofa_result(self, account_id: int) -> str:
        import uuid
        attempt = uuid.uuid4().hex
        with self.conn:
            self.conn.execute('INSERT INTO twofa_results VALUES (?,?,?,?,?) '
                'ON CONFLICT(account_id) DO UPDATE SET attempt=excluded.attempt, '
                'status=excluded.status,detail=excluded.detail,updated_at=excluded.updated_at',
                (account_id, attempt, 'running', '正在修改2FA', time.time()))
        return attempt

    def finish_twofa_result(self, account_id: int, attempt: str, status: str, detail: str) -> None:
        with self.conn:
            self.conn.execute('UPDATE twofa_results SET status=?,detail=?,updated_at=? '
                              'WHERE account_id=? AND attempt=?',
                              (status, detail, time.time(), account_id, attempt))

    def twofa_results(self) -> dict[int, dict[str, Any]]:
        """Latest change outcome; no password/session/raw exception in public metadata.

        Include historical one-stop checkpoints so existing batches can be filtered
        without sending codes or changing passwords again.
        """
        out = {r['account_id']: {k:r[k] for k in ('status','detail','updated_at')}
               for r in self.conn.execute('SELECT * FROM twofa_results')}
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if {'one_stop_items','tasks','task_targets'} <= tables:
            rows = self.conn.execute('''
                WITH latest AS (
                    SELECT i.account_id, MAX(i.task_id) tid
                    FROM one_stop_items i JOIN tasks t ON t.id=i.task_id
                    WHERE json_extract(t.params,'$.change_password')=1
                    GROUP BY i.account_id)
                SELECT i.account_id,i.phase,t.id task_id,t.created_at,t.status task_status
                FROM latest l JOIN one_stop_items i ON i.task_id=l.tid AND i.account_id=l.account_id
                JOIN tasks t ON t.id=i.task_id
            ''')
            for r in rows:
                aid = r['account_id']
                current = out.get(aid)
                if current and current['updated_at'] >= r['created_at']:
                    current['task_id'] = r['task_id']
                    continue
                phase = r['phase']
                if phase == 'ready': status,detail='success','2FA修改并保存成功'
                elif phase in ('changing','uncertain'):
                    status,detail=('running','正在修改2FA') if phase=='changing' and r['task_status'] in ('running','stopping') else ('uncertain','改密结果待核对，暂不导出')
                elif r['task_status'] in ('pending','running','stopping'):
                    status,detail='running','等待登录及修改2FA'
                else: status,detail='failed','本批未完成改密，请查看一条龙结果清单'
                out[aid]={'status':status,'detail':detail,'updated_at':r['created_at'],'task_id':r['task_id']}
        return out

    def add_error(
        self,
        message: str,
        *,
        level: str = "error",
        source: str = "server",
        path: str | None = None,
        traceback: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> int:
        """写入一条错误事件，返回 id。"""
        cur = self.conn.execute(
            "INSERT INTO error_events (level, source, path, message, traceback, meta, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                (level or "error")[:16],
                (source or "server")[:16],
                (path or "")[:500] or None,
                (message or "")[:4000],
                (traceback or "")[:20000] or None,
                json.dumps(meta, ensure_ascii=False)[:8000] if meta else None,
                time.time(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_errors(
        self,
        limit: int = 100,
        source: str | None = None,
        level: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM error_events"
        clauses: list[str] = []
        params: list[Any] = []
        if source:
            clauses.append("source=?")
            params.append(source)
        if level:
            clauses.append("level=?")
            params.append(level)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        rows = self.conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("meta"):
                try:
                    d["meta"] = json.loads(d["meta"])
                except Exception:
                    pass
            out.append(d)
        return out

    def clear_errors(self, older_than: float | None = None) -> int:
        """清空错误日志。older_than 为 unix 秒时只删更早的。"""
        if older_than is not None:
            cur = self.conn.execute(
                "DELETE FROM error_events WHERE created_at < ?", (float(older_than),)
            )
        else:
            cur = self.conn.execute("DELETE FROM error_events")
        self.conn.commit()
        return int(cur.rowcount or 0)

    def error_stats(self) -> dict[str, Any]:
        total = self.conn.execute("SELECT COUNT(*) AS n FROM error_events").fetchone()["n"]
        by_src = {
            row["source"]: row["n"]
            for row in self.conn.execute(
                "SELECT source, COUNT(*) AS n FROM error_events GROUP BY source"
            )
        }
        recent = self.conn.execute(
            "SELECT created_at FROM error_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "total": int(total or 0),
            "by_source": by_src,
            "latest_at": float(recent["created_at"]) if recent else None,
        }
