"""任务系统：把“对一批目标逐个执行”的活变成可观测、可停止的任务。

设计要点：
- 只依赖 sqlite 连接，不碰 telethon，可脱机单测；
- 每个目标一行记录，失败原因落库，方便在 WebUI 逐条查；
- 停止是“协商式”：置 STOPPING 后不再发待发送内容，剩余目标标为已跳过。
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    params TEXT DEFAULT '{}',
    status TEXT NOT NULL,
    total INTEGER DEFAULT 0,
    note TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS task_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    target TEXT NOT NULL,
    account_id INTEGER,
    status TEXT NOT NULL,
    detail TEXT,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_targets_task ON task_targets(task_id, seq);
"""

PENDING = "pending"
RUNNING = "running"
STOPPING = "stopping"
STOPPED = "stopped"
DONE = "done"
FAILED = "failed"

TERMINAL = {DONE, FAILED, STOPPED}

TASK_CN = {
    PENDING: "排队中",
    RUNNING: "运行中",
    STOPPING: "正在停止",
    STOPPED: "已停止",
    DONE: "已完成",
    FAILED: "已失败",
}

TARGET_CN = {
    "pending": "待处理",
    "running": "处理中",
    "ok": "成功",
    "fail": "失败",
    "skipped": "已跳过",
}


@dataclass
class Task:
    id: int = 0
    kind: str = ""
    title: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    status: str = PENDING
    total: int = 0
    note: str | None = None
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    ok_count: int = 0
    fail_count: int = 0
    skip_count: int = 0

    @property
    def done_count(self) -> int:
        return self.ok_count + self.fail_count + self.skip_count

    @property
    def percent(self) -> float:
        if not self.total:
            return 0.0
        return round(self.done_count * 100 / self.total, 1)

    @property
    def status_cn(self) -> str:
        return TASK_CN.get(self.status, self.status)

    def public(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items()}
        d["done_count"] = self.done_count
        d["percent"] = self.percent
        d["status_cn"] = self.status_cn
        return d


Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | str | None]]


class TaskStore:
    """任务持久化。"""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- 写 ---
    def create(self, kind: str, title: str, targets: list[str],
               params: dict[str, Any] | None = None) -> Task:
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO tasks (kind, title, params, status, total, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (kind, title, json.dumps(params or {}, ensure_ascii=False),
             PENDING, len(targets), now),
        )
        task_id = int(cur.lastrowid)
        self.conn.executemany(
            "INSERT INTO task_targets (task_id, seq, target, status, updated_at)"
            " VALUES (?,?,?,?,?)",
            [(task_id, i, str(t), "pending", now) for i, t in enumerate(targets)],
        )
        self.conn.commit()
        task = self.get(task_id)
        assert task is not None
        return task

    def mark_target(self, target_id: int, status: str, detail: str = "",
                    account_id: int | None = None) -> None:
        if account_id is None:
            self.conn.execute(
                "UPDATE task_targets SET status=?, detail=?, updated_at=? WHERE id=?",
                (status, (detail or "")[:1000], time.time(), target_id),
            )
        else:
            self.conn.execute(
                "UPDATE task_targets SET status=?, detail=?, account_id=?, updated_at=?"
                " WHERE id=?",
                (status, (detail or "")[:1000], account_id, time.time(), target_id),
            )
        self.conn.commit()

    def set_status(self, task_id: int, status: str, note: str | None = None) -> None:
        fields: dict[str, Any] = {"status": status}
        if note is not None:
            fields["note"] = note
        if status == RUNNING:
            fields["started_at"] = time.time()
        if status in TERMINAL:
            fields["finished_at"] = time.time()
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE tasks SET {sets} WHERE id=?",
                          (*fields.values(), task_id))
        self.conn.commit()

    def request_stop(self, task_id: int) -> bool:
        """请求停止；已结束的任务返回 False。"""
        t = self.get(task_id)
        if t is None or t.status in TERMINAL:
            return False
        self.set_status(task_id, STOPPING)
        return True

    def delete(self, task_id: int) -> None:
        self.conn.execute("DELETE FROM task_targets WHERE task_id=?", (task_id,))
        self.conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        self.conn.commit()

    def cleanup(self, keep: int = 50) -> int:
        """只保留最近 keep 个已结束的任务，未结束的一律保留。返回删除数。"""
        marks = ",".join("?" * len(TERMINAL))
        rows = self.conn.execute(
            f"SELECT id FROM tasks WHERE status IN ({marks}) ORDER BY id DESC",
            tuple(sorted(TERMINAL)),
        ).fetchall()
        victims = [r["id"] for r in rows[keep:]]
        for tid in victims:
            self.delete(tid)
        return len(victims)

    # --- 读 ---
    def _counts(self, task_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) c FROM task_targets WHERE task_id=? GROUP BY status",
            (task_id,),
        )
        return {r["status"]: r["c"] for r in rows}

    def _hydrate(self, row: Any) -> Task:
        d = dict(row)
        d["params"] = json.loads(d.get("params") or "{}")
        task = Task(**d)
        c = self._counts(task.id)
        task.ok_count = c.get("ok", 0)
        task.fail_count = c.get("fail", 0)
        task.skip_count = c.get("skipped", 0)
        return task

    def get(self, task_id: int) -> Task | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._hydrate(row) if row else None

    def list(self, limit: int = 50, status: str | None = None) -> list[Task]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._hydrate(r) for r in rows]

    def targets(self, task_id: int, status: str | None = None,
                limit: int = 500) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM task_targets WHERE task_id=? AND status=?"
                " ORDER BY seq LIMIT ?", (task_id, status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM task_targets WHERE task_id=? ORDER BY seq LIMIT ?",
                (task_id, limit),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["status_cn"] = TARGET_CN.get(d["status"], d["status"])
            out.append(d)
        return out

    def pending(self, task_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM task_targets WHERE task_id=? AND status IN ('pending','running')"
            " ORDER BY seq", (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]


class TaskRunner:
    """执行器：跑一个任务的所有待处理目标，支持并发度、间隔与随时停止。"""

    def __init__(self, store: TaskStore) -> None:
        self.store = store
        self._live: dict[int, asyncio.Task[Any]] = {}

    def is_running(self, task_id: int) -> bool:
        t = self._live.get(task_id)
        return bool(t and not t.done())

    async def stop(self, task_id: int) -> bool:
        return self.store.request_stop(task_id)

    def _stopping(self, task_id: int) -> bool:
        t = self.store.get(task_id)
        return bool(t and t.status in (STOPPING, STOPPED))

    async def run(self, task_id: int, handler: Handler,
                  concurrency: int = 1, delay: float = 0.0,
                  target_timeout: float | None = 180.0) -> dict[str, Any]:
        """执行任务。target_timeout：单目标秒数，超时记失败并继续（防假死）。"""
        task = self.store.get(task_id)
        if task is None:
            return {"ok": False, "error": "任务不存在"}
        self.store.set_status(task_id, RUNNING)
        rows = self.store.pending(task_id)
        sem = asyncio.Semaphore(max(1, concurrency))
        stopped = False
        tmo = float(target_timeout) if target_timeout and target_timeout > 0 else None

        async def one(row: dict[str, Any], idx: int) -> None:
            nonlocal stopped
            async with sem:
                if self._stopping(task_id):
                    stopped = True
                    return
                if delay and idx:
                    await asyncio.sleep(delay * random.uniform(0.8, 1.2))
                self.store.mark_target(row["id"], "running")
                try:
                    if tmo:
                        res = await asyncio.wait_for(handler(row), timeout=tmo)
                    else:
                        res = await handler(row)
                except asyncio.TimeoutError:
                    self.store.mark_target(
                        row["id"], "fail",
                        f"单号超时（{int(tmo or 0)}s）已跳过，请检查代理/网络后重试",
                    )
                    return
                except Exception as e:  # noqa: BLE001
                    self.store.mark_target(
                        row["id"], "fail", f"{type(e).__name__}: {e}")
                    return
                if isinstance(res, dict):
                    detail = str(res.get("detail") or "")
                    aid = res.get("account_id")
                else:
                    detail, aid = str(res or ""), None
                self.store.mark_target(row["id"], "ok", detail, account_id=aid)

        try:
            if concurrency <= 1:
                for i, row in enumerate(rows):
                    if self._stopping(task_id):
                        stopped = True
                        break
                    await one(row, i)
            else:
                await asyncio.gather(*(one(r, i) for i, r in enumerate(rows)))
        except Exception:  # noqa: BLE001
            # 兜底：不让整任务无终态
            cur = self.store.get(task_id)
            if cur and cur.status not in TERMINAL:
                self.store.set_status(task_id, FAILED, "执行器异常中断")
            raise

        left = self.store.pending(task_id)
        if left or stopped or self._stopping(task_id):
            for r in left:
                self.store.mark_target(r["id"], "skipped", "任务已停止")
            self.store.set_status(task_id, STOPPED)
        else:
            cur = self.store.get(task_id)
            all_failed = bool(cur and cur.total and cur.fail_count == cur.total)
            self.store.set_status(task_id, FAILED if all_failed else DONE)

        final = self.store.get(task_id)
        return final.public() if final else {"ok": False}

    def spawn(self, task_id: int, handler: Handler,
              concurrency: int = 1, delay: float = 0.0,
              target_timeout: float | None = 180.0) -> bool:
        """后台跑任务，立即返回。异常会写入任务失败状态并清理 live。"""
        if self.is_running(task_id):
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()

        async def _wrap() -> None:
            try:
                await self.run(
                    task_id, handler,
                    concurrency=concurrency, delay=delay,
                    target_timeout=target_timeout,
                )
            except Exception as exc:  # noqa: BLE001
                try:
                    self.store.set_status(
                        task_id, FAILED, f"后台任务异常: {type(exc).__name__}: {exc}"
                    )
                except Exception:  # noqa: BLE001
                    pass
            finally:
                self._live.pop(task_id, None)

        self._live[task_id] = loop.create_task(_wrap())
        return True
