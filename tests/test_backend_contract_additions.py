"""前后端对接新增契约的脱机回归测试。"""
from __future__ import annotations

import sqlite3
import time

from tam.db import Account, Database
from tam.preview import build_preview
from tam.tasks import TaskStore


def _db(tmp_path):
    return Database(tmp_path / "tam.sqlite3")


def test_preview_contract_is_stable_and_does_not_execute(tmp_path):
    result = build_preview(
        [1, 2, 2, 3],
        runnable=[1, 3],
        reasons={2: ("not_authorized", "未登录")},
    )
    assert result == {
        "run": [1, 3],
        "skip": [{"id": 2, "reason_code": "session_invalid", "reason": "未登录"}],
    }


def test_account_check_fields_are_migrated_and_public(tmp_path):
    db = _db(tmp_path)
    acc = db.add_account(Account(label="check-fields", session_enc="encrypted"))
    db.update(acc.id, last_check_result="ok", last_check_error=None)
    public = db.get(acc.id).public()
    assert public["last_check_result"] == "ok"
    assert public["last_check_error"] is None


def test_incremental_account_changes_include_updates_and_deletes(tmp_path):
    db = _db(tmp_path)
    acc = db.add_account(Account(label="delta", session_enc="encrypted"))
    since = time.time()
    db.update(acc.id, status_note="changed")
    db.delete(acc.id)
    changes = db.changes_since(since)
    assert changes["deleted_ids"] == [acc.id]
    assert acc.id in changes["changed_ids"]


def test_task_target_contract_uses_detail_not_result_error():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = TaskStore(conn)
    task = store.create("toolbox", "批量工具", ["7"])
    row = store.targets(task.id)[0]
    assert {"account_id", "status", "detail"}.issubset(row)
    assert "result" not in row and "error" not in row


def test_toolbox_batch_request_contract_uses_account_ids_and_sync_result():
    from tam.api import ToolboxBatchIn
    body = ToolboxBatchIn(account_ids=[1, 2], params={"new": "***"}, concurrency=3)
    assert body.account_ids == [1, 2]
    assert body.params == {"new": "***"}
    assert body.concurrency == 3


def test_twofa_toolbox_preview_reuses_session_skip_rule():
    from tam.toolbox import preview_op_batch

    class FakeDB:
        def __init__(self):
            self.rows = {
                1: Account(id=1, label="ok", status="active", session_enc="x"),
                2: Account(id=2, label="dead", status="unauthorized", session_enc="x"),
            }

        def get(self, account_id):
            return self.rows.get(account_id)

    class FakeManager:
        db = FakeDB()

    result = preview_op_batch(FakeManager(), [1, 2, 3], "twofa", {"new": "***"})
    assert result["run"] == [1]
    assert result["skip"] == [
        {"id": 2, "reason_code": "session_invalid", "reason": "会话已失效"},
        {"id": 3, "reason_code": "not_found", "reason": "账号不存在"},
    ]
