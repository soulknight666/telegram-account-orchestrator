"""线索库：存放采集到的活跃发言人，供后续筛选与群发使用。"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    username    TEXT,
    name        TEXT,
    source      TEXT,                 -- 来源群组
    msg_count   INTEGER DEFAULT 0,    -- 采集窗口内发言条数
    last_msg_at REAL,                 -- 最后一条发言时间
    tags        TEXT DEFAULT '[]',
    created_at  REAL NOT NULL,
    UNIQUE(user_id, source)
);

CREATE INDEX IF NOT EXISTS idx_leads_source ON leads(source, last_msg_at DESC);

-- 采集窗口内扫到的原始发言，用于还原对话上下文（可选开启）。
-- 注意：存的是“采集时扫到的那一段”，不是群的完整历史。
CREATE TABLE IF NOT EXISTS lead_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id      INTEGER,
    user_id     INTEGER NOT NULL,
    source      TEXT,                 -- 来源群组，与 leads.source 对应
    text        TEXT,
    kind        TEXT,                 -- text / photo / document / …
    reply_to    INTEGER,              -- 回复的消息 id，靠它串出对话线
    date        REAL,
    created_at  REAL NOT NULL,
    UNIQUE(source, msg_id)
);

CREATE INDEX IF NOT EXISTS idx_lead_msgs_user ON lead_messages(user_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_lead_msgs_source ON lead_messages(source, date DESC);
"""


class LeadStore:
    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def upsert_many(self, rows: Iterable[dict[str, Any]], source: str,
                    tags: list[str] | None = None) -> dict[str, int]:
        now = time.time()
        added = updated = 0
        tag_json = json.dumps(tags or [], ensure_ascii=False)
        for r in rows:
            uid = int(r.get("user_id") or 0)
            if not uid:
                continue
            cur = self.conn.execute(
                "SELECT id FROM leads WHERE user_id=? AND source=?", (uid, source)
            ).fetchone()
            if cur:
                self.conn.execute(
                    "UPDATE leads SET username=?, name=?, msg_count=?, last_msg_at=? WHERE id=?",
                    (r.get("username"), r.get("name"), int(r.get("msg_count") or 0),
                     r.get("last_msg_at"), int(cur["id"])),
                )
                updated += 1
            else:
                self.conn.execute(
                    "INSERT INTO leads (user_id, username, name, source, msg_count,"
                    " last_msg_at, tags, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (uid, r.get("username"), r.get("name"), source,
                     int(r.get("msg_count") or 0), r.get("last_msg_at"), tag_json, now),
                )
                added += 1
        self.conn.commit()
        return {"added": added, "updated": updated}

    def list(self, source: str | None = None, since: float | None = None,
             has_username: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        sql = "SELECT * FROM leads WHERE 1=1"
        params: list[Any] = []
        if source:
            sql += " AND source=?"
            params.append(source)
        if since:
            sql += " AND last_msg_at >= ?"
            params.append(since)
        if has_username:
            sql += " AND username IS NOT NULL AND username != ''"
        sql += " ORDER BY last_msg_at DESC LIMIT ?"
        params.append(limit)
        out = []
        for r in self.conn.execute(sql, params):
            d = dict(r)
            d["tags"] = json.loads(d.get("tags") or "[]")
            out.append(d)
        return out

    def sources(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT source, COUNT(*) AS item_count, MAX(last_msg_at) AS latest"
            " FROM leads GROUP BY source ORDER BY item_count DESC"
        )]

    def targets(self, source: str | None = None, since: float | None = None,
                limit: int = 500) -> list[str]:
        """转成可直接群发的目标：有用户名用 @username，否则用数字 ID。"""
        out = []
        for r in self.list(source=source, since=since, limit=limit):
            out.append(f"@{r['username']}" if r.get("username") else str(r["user_id"]))
        return out

    def add_messages(self, rows: Iterable[dict[str, Any]], source: str) -> dict[str, int]:
        """写入采集到的原始发言。同一群的同一条消息重复采集不会重复入库。"""
        now = time.time()
        added = 0
        for r in rows:
            uid = int(r.get("user_id") or 0)
            if not uid:
                continue
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO lead_messages"
                " (msg_id, user_id, source, text, kind, reply_to, date, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (r.get("msg_id"), uid, source, r.get("text"), r.get("kind"),
                 r.get("reply_to"), r.get("date"), now),
            )
            added += cur.rowcount or 0
        self.conn.commit()
        return {"added": added}

    def messages(self, user_id: int | None = None, source: str | None = None,
                 limit: int = 200, ascending: bool = True) -> list[dict[str, Any]]:
        """取对话记录。默认正序，方便直接当聊天记录读。"""
        sql = "SELECT * FROM lead_messages WHERE 1=1"
        params: list[Any] = []
        if user_id:
            sql += " AND user_id=?"
            params.append(int(user_id))
        if source:
            sql += " AND source=?"
            params.append(source)
        # 先按时间倒序取最新 limit 条，再按需转正序，避免截断掉最新的对话
        sql += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        rows = [dict(r) for r in self.conn.execute(sql, params)]
        if ascending:
            rows.reverse()
        return rows

    def message_stats(self, source: str | None = None) -> dict[str, Any]:
        sql = "SELECT COUNT(*) AS total, COUNT(DISTINCT user_id) AS speakers FROM lead_messages"
        params: list[Any] = []
        if source:
            sql += " WHERE source=?"
            params.append(source)
        row = self.conn.execute(sql, params).fetchone()
        return {"total": row["total"] or 0, "speakers": row["speakers"] or 0}

    def clear(self, source: str | None = None) -> int:
        """清线索时连带清掉它的对话记录，不然会留一堆孤儿消息。"""
        if source:
            self.conn.execute("DELETE FROM lead_messages WHERE source=?", (source,))
            cur = self.conn.execute("DELETE FROM leads WHERE source=?", (source,))
        else:
            self.conn.execute("DELETE FROM lead_messages")
            cur = self.conn.execute("DELETE FROM leads")
        self.conn.commit()
        return cur.rowcount or 0
