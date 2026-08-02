"""自动清设备的纯逻辑测试（不联网）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tam.autokick import due_ids, next_due_at
from tam.db import Account

NOW = 1_700_000_000.0
H = 3600.0


def acc(**kw) -> Account:
    base = dict(id=1, label="a", session_enc="x", status="active",
                created_at=NOW - 100 * H, auto_kick=1)
    base.update(kw)
    return Account(**base)


def test_not_due_before_24h() -> None:
    a = acc(login_at=NOW - 10 * H)
    assert next_due_at(a, 24) == NOW - 10 * H + 24 * H
    assert due_ids([a], 24, NOW) == []


def test_due_after_24h() -> None:
    a = acc(login_at=NOW - 25 * H)
    assert due_ids([a], 24, NOW) == [1]


def test_last_kick_wins_over_login() -> None:
    a = acc(login_at=NOW - 100 * H, last_kick_at=NOW - 2 * H)
    assert due_ids([a], 24, NOW) == []


def test_disabled_globally() -> None:
    a = acc(login_at=NOW - 100 * H)
    assert next_due_at(a, 0) is None
    assert due_ids([a], 0, NOW) == []


def test_disabled_per_account() -> None:
    a = acc(login_at=NOW - 100 * H, auto_kick=0)
    assert due_ids([a], 24, NOW) == []


def test_skip_no_session_and_bad_status() -> None:
    no_sess = acc(session_enc=None, login_at=NOW - 100 * H)
    banned = acc(status="banned", login_at=NOW - 100 * H)
    unauth = acc(status="unauthorized", login_at=NOW - 100 * H)
    assert due_ids([no_sess, banned, unauth], 24, NOW) == []


def test_fallback_to_created_at() -> None:
    """老库升级上来没有 login_at，应该退回 created_at，而不是永不触发。"""
    a = acc(login_at=None, created_at=NOW - 48 * H)
    assert due_ids([a], 24, NOW) == [1]


def test_custom_hours() -> None:
    a = acc(login_at=NOW - 5 * H)
    assert due_ids([a], 4, NOW) == [1]
    assert due_ids([a], 6, NOW) == []


def test_real_clock_default() -> None:
    a = acc(login_at=time.time() - 30 * H)
    assert due_ids([a], 24) == [1]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
    print("test_autokick 全部通过")
