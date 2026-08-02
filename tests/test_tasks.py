"""任务系统与线索库纯逻辑测试（内存 SQLite，不联网、不碰 Telethon）。"""
import asyncio
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tam.leads import LeadStore
from tam.tasks import (DONE, FAILED, PENDING, STOPPED, TERMINAL, TaskRunner,
                       TaskStore)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _store() -> TaskStore:
    return TaskStore(_conn())


def test_create_and_read() -> None:
    st = _store()
    t = st.create("send", "测试群发", ["@a", "@b", "@c"], params={"html": True})
    assert t.total == 3 and t.status == PENDING and t.params["html"] is True
    assert [r["target"] for r in st.targets(t.id)] == ["@a", "@b", "@c"]
    assert st.get(t.id).title == "测试群发"
    assert st.list()[0].id == t.id
    assert t.public()["status_cn"] == "排队中"


def test_counts_and_percent() -> None:
    st = _store()
    t = st.create("send", "x", ["1", "2", "3", "4"])
    rows = st.targets(t.id)
    st.mark_target(rows[0]["id"], "ok", "已发", account_id=7)
    st.mark_target(rows[1]["id"], "fail", "FloodWait 300 秒")
    cur = st.get(t.id).public()
    assert cur["ok_count"] == 1 and cur["fail_count"] == 1
    assert cur["done_count"] == 2 and cur["percent"] == 50.0
    bad = st.targets(t.id, status="fail")[0]
    assert bad["detail"].startswith("FloodWait") and bad["status_cn"] == "失败"
    assert st.targets(t.id, status="ok")[0]["account_id"] == 7


def test_runner_all_ok_keeps_order() -> None:
    st = _store()
    t = st.create("send", "x", ["a", "b", "c"])
    seen = []

    async def handler(row):
        seen.append(row["target"])
        return {"detail": "message_id=1", "account_id": 5}

    res = asyncio.run(TaskRunner(st).run(t.id, handler))
    assert res["status"] == DONE and res["ok_count"] == 3 and res["percent"] == 100.0
    assert seen == ["a", "b", "c"], "单并发必须保持发送顺序"
    assert st.targets(t.id)[0]["account_id"] == 5


def test_runner_accepts_plain_string_detail() -> None:
    st = _store()
    t = st.create("collect_speakers", "x", ["@g"])

    async def handler(row):
        return "采集到 12 人"

    res = asyncio.run(TaskRunner(st).run(t.id, handler))
    assert res["status"] == DONE
    assert st.targets(t.id)[0]["detail"] == "采集到 12 人"


def test_runner_records_failure_reason() -> None:
    st = _store()
    t = st.create("send", "x", ["a", "b"])

    async def handler(row):
        if row["target"] == "b":
            raise ValueError("对方限制了私信")
        return {"detail": "ok"}

    res = asyncio.run(TaskRunner(st).run(t.id, handler))
    assert res["status"] == DONE and res["ok_count"] == 1 and res["fail_count"] == 1
    bad = st.targets(t.id, status="fail")[0]
    assert "ValueError" in bad["detail"] and "限制" in bad["detail"]


def test_all_failed_marks_task_failed() -> None:
    st = _store()
    t = st.create("send", "x", ["a", "b"])

    async def handler(row):
        raise RuntimeError("boom")

    res = asyncio.run(TaskRunner(st).run(t.id, handler))
    assert res["status"] == FAILED and res["status_cn"] == "已失败"


def test_stop_skips_remaining() -> None:
    st = _store()
    t = st.create("send", "x", [str(i) for i in range(10)])
    r = TaskRunner(st)

    async def handler(row):
        if row["target"] == "2":
            await r.stop(t.id)  # 第 3 个目标处理完后停止
        return {"detail": "ok"}

    res = asyncio.run(r.run(t.id, handler))
    assert res["status"] == STOPPED and res["status_cn"] == "已停止"
    assert res["ok_count"] == 3, "停止后不得再发待发送内容"
    assert res["skip_count"] == 7
    assert st.targets(t.id, status="skipped")[0]["detail"] == "任务已停止"


def test_stop_finished_task_is_noop() -> None:
    st = _store()
    t = st.create("send", "x", ["a"])
    st.set_status(t.id, DONE)
    assert st.request_stop(t.id) is False
    assert st.get(t.id).status in TERMINAL


def test_missing_task_returns_error() -> None:
    st = _store()

    async def handler(row):
        return "x"

    res = asyncio.run(TaskRunner(st).run(999, handler))
    assert res["ok"] is False


def test_cleanup_keeps_recent_and_running() -> None:
    st = _store()
    for i in range(8):
        t = st.create("send", f"t{i}", ["a"])
        st.set_status(t.id, DONE)
    live = st.create("send", "live", ["a"])
    assert st.cleanup(keep=3) == 5
    ids = [x.id for x in st.list()]
    assert live.id in ids and len(ids) == 4


def test_delete_removes_targets() -> None:
    st = _store()
    t = st.create("send", "x", ["a", "b"])
    st.delete(t.id)
    assert st.get(t.id) is None and st.targets(t.id) == []


def test_concurrent_run_finishes_all() -> None:
    st = _store()
    t = st.create("send", "x", [str(i) for i in range(20)])

    async def handler(row):
        await asyncio.sleep(0)
        return {"detail": "ok"}

    res = asyncio.run(TaskRunner(st).run(t.id, handler, concurrency=5))
    assert res["status"] == DONE and res["ok_count"] == 20


def test_leads_dedupe_and_filters() -> None:
    ls = LeadStore(_conn())
    now = time.time()
    rows = [
        {"user_id": 1, "username": "alice", "name": "Alice", "msg_count": 3, "last_msg_at": now},
        {"user_id": 2, "username": None, "name": "Bob", "msg_count": 1, "last_msg_at": now - 10},
    ]
    assert ls.upsert_many(rows, source="群A", tags=["测试"]) == {"added": 2, "updated": 0}
    assert ls.upsert_many(rows, source="群A") == {"added": 0, "updated": 2}
    assert ls.targets(source="群A") == ["@alice", "2"]
    assert ls.list(has_username=True)[0]["username"] == "alice"
    assert ls.sources()[0]["item_count"] == 2
    assert ls.list(source="群A")[0]["tags"] == ["测试"]
    assert ls.clear("群A") == 2 and ls.list() == []


def test_leads_time_filter() -> None:
    ls = LeadStore(_conn())
    now = time.time()
    ls.upsert_many([
        {"user_id": 1, "username": "new", "last_msg_at": now},
        {"user_id": 2, "username": "old", "last_msg_at": now - 30 * 86400},
    ], source="g")
    assert ls.targets(source="g", since=now - 7 * 86400) == ["@new"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
    print("test_tasks 全部通过")
