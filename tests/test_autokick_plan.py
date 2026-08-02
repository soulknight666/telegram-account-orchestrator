"""回归测试：“下次自动清设备”莫名其妙变成 32.8 天前。

两个根因：
1) 后端 SKIP_STATUS（banned/unauthorized/frozen/spam_block_perm）永不执行，前端却照旧
   公式 base + hours*3600 算一个到期时间，于是越拖越远，显示成 "32.8 天前"。
2) 服务端 date_created 返回 0 / 未来时间时被无条件写进 login_at。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tam.autokick import next_due_at, plan, status
from tam.db import Account

NOW = 1_700_000_000.0
H = 3600.0


def acc(**kw) -> Account:
    base = dict(id=1, label="a", session_enc="x", status="active",
                created_at=NOW - 100 * H, auto_kick=1)
    base.update(kw)
    return Account(**base)


def test_waiting_has_future_due() -> None:
    p = plan(acc(login_at=NOW - 10 * H), 24, NOW)
    assert p["state"] == "waiting" and p["due_at"] > NOW and p["overdue_s"] == 0.0


def test_due_reports_overdue() -> None:
    p = plan(acc(login_at=NOW - 25 * H), 24, NOW)
    assert p["state"] == "due" and round(p["overdue_s"]) == 3600


def test_skipped_status_has_no_due_at() -> None:
    """核心回归：被跳过的号不能再给出任何到期时间。"""
    for st in ("banned", "unauthorized", "frozen", "spam_block_perm"):
        a = acc(status=st, login_at=NOW - 800 * H)   # 旧公式会算出 ≈32.8 天前
        p = plan(a, 24, NOW)
        assert p["state"] == "skipped", st
        assert p["due_at"] is None, st
        assert next_due_at(a, 24, NOW) is None, st


def test_off_and_no_session_and_disabled() -> None:
    assert plan(acc(auto_kick=0, login_at=NOW), 24, NOW)["state"] == "off"
    assert plan(acc(session_enc=None, login_at=NOW), 24, NOW)["state"] == "no_session"
    assert plan(acc(login_at=NOW), 0, NOW)["state"] == "disabled"


def test_base_priority_and_source() -> None:
    p = plan(acc(login_at=NOW - 100 * H, last_kick_at=NOW - 2 * H), 24, NOW)
    assert p["base_from"] == "last_kick_at" and p["state"] == "waiting"
    p2 = plan(acc(login_at=None, created_at=NOW - 48 * H), 24, NOW)
    assert p2["base_from"] == "created_at" and p2["state"] == "due"


class _FakeDB:
    def __init__(self, accounts):
        self._a = accounts

    def list(self):
        return self._a


class _FakeMgr:
    class _S:
        auto_kick_hours = 24.0

    def __init__(self, accounts):
        self.s = self._S()
        self.db = _FakeDB(accounts)


def test_status_excludes_skipped_and_reports_clock() -> None:
    now = time.time()
    banned = acc(id=1, label="banned", status="banned", login_at=now - 800 * H)
    live = acc(id=2, label="live", login_at=now - 2 * H)
    overdue = acc(id=3, label="overdue", login_at=now - 30 * H)
    st = status(_FakeMgr([banned, live, overdue]))
    assert st["watched"] == 2            # 被封的那个不再计入
    assert st["due_now"] == 1
    assert 5 * H < st["max_overdue_s"] < 7 * H
    assert abs(st["server_now"] - now) < 5
    assert st["next_at"] > now


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
    print("test_autokick_plan 全部通过")
