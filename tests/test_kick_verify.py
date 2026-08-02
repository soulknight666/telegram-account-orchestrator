"""接管计时 + 踢出成功校验的回归测试。

两件事必须制住：
1. 计时起点是本机接管时间 adopted_at，不是服务端的会话创建时间；
2. 踢完要回拉会话列表核对，没踢干净就不能算成功。
"""
from __future__ import annotations

import unittest

from tam import autokick
from tam.db import Account
from tam.manager import AccountManager

NOW = 1_700_000_000.0
H = 3600.0


def acc(**kw) -> Account:
    base = dict(id=1, label="a", status="active", session_enc="x", auto_kick=1,
                created_at=NOW - 100 * 24 * H, updated_at=NOW)
    base.update(kw)
    return Account(**base)


class TestAdoptedAtBase(unittest.TestCase):
    def test_adopted_at_wins_over_server_login_at(self):
        """tdata 导入的老会话：会话已存在 30 天，但本机刚接管 1 小时。"""
        p = autokick.plan(acc(login_at=NOW - 30 * 24 * H, adopted_at=NOW - H), 24.0, NOW)
        self.assertEqual(p["state"], "waiting")
        self.assertEqual(p["base_from"], "adopted_at")
        self.assertAlmostEqual(p["due_at"], NOW - H + 24 * H)
        self.assertFalse(p["retrying"])

    def test_due_after_24h_since_adoption(self):
        p = autokick.plan(acc(login_at=NOW - 30 * 24 * H, adopted_at=NOW - 25 * H), 24.0, NOW)
        self.assertEqual(p["state"], "due")
        self.assertAlmostEqual(p["overdue_s"], H, places=3)

    def test_login_at_only_falls_back(self):
        """老库还没 adopted_at 时不能直接变成 no_base。"""
        p = autokick.plan(acc(login_at=NOW - 25 * H), 24.0, NOW)
        self.assertEqual(p["base_from"], "login_at")
        self.assertEqual(p["state"], "due")

    def test_last_kick_at_still_wins(self):
        p = autokick.plan(acc(adopted_at=NOW - 100 * H, last_kick_at=NOW - 2 * H), 24.0, NOW)
        self.assertEqual(p["base_from"], "last_kick_at")
        self.assertEqual(p["state"], "waiting")


class TestRetrySchedule(unittest.TestCase):
    def test_retry_at_overrides_and_is_marked(self):
        p = autokick.plan(acc(adopted_at=NOW - 100 * H, last_kick_at=NOW - 50 * H,
                              kick_retry_at=NOW + 0.5 * H), 24.0, NOW)
        self.assertEqual(p["state"], "waiting")
        self.assertTrue(p["retrying"])
        self.assertEqual(p["base_from"], "kick_retry_at")
        self.assertAlmostEqual(p["due_at"], NOW + 0.5 * H)

    def test_retry_due_when_reached(self):
        a = acc(adopted_at=NOW - 100 * H, last_kick_at=NOW - H, kick_retry_at=NOW - 60)
        p = autokick.plan(a, 24.0, NOW)
        self.assertEqual(p["state"], "due")
        self.assertTrue(p["retrying"])
        self.assertIn(1, autokick.due_ids([a], 24.0))   # 刚踢过也不影响重试

    def test_retry_gap_is_shorter_than_a_full_cycle(self):
        self.assertLess(autokick.RETRY_AFTER, 24 * H)


class _A:
    """假的 Authorization 对象。"""

    def __init__(self, h, current=False, model="iPhone"):
        self.hash, self.current = h, current
        self.app_name, self.device_model = "Telegram", model
        self.platform, self.country = "iOS", "US"


class TestKickReport(unittest.TestCase):
    def test_others_excludes_current(self):
        o = AccountManager._others([_A(0, current=True), _A(11), _A(22)])
        self.assertEqual(set(o), {11, 22})

    def test_all_gone_is_verified(self):
        before = AccountManager._others([_A(0, current=True), _A(11), _A(22)])
        rep = AccountManager._kick_report(before, {})
        self.assertTrue(rep["verified"])
        self.assertEqual(len(rep["removed"]), 2)
        self.assertEqual(rep["after_others"], 0)

    def test_survivor_fails_verification(self):
        before = AccountManager._others([_A(11), _A(22)])
        after = AccountManager._others([_A(22)])
        rep = AccountManager._kick_report(before, after)
        self.assertFalse(rep["verified"])
        self.assertEqual(rep["after_others"], 1)
        self.assertEqual(len(rep["left"]), 1)
        self.assertEqual(rep["reappeared"], [])

    def test_relogin_detected_as_reappeared(self):
        """旧会话踢掉了，但对方拿着密码又登了个新的——不能算成功。"""
        before = AccountManager._others([_A(11)])
        after = AccountManager._others([_A(99, model="Desktop")])
        rep = AccountManager._kick_report(before, after)
        self.assertFalse(rep["verified"])
        self.assertEqual(len(rep["removed"]), 1)
        self.assertEqual(len(rep["reappeared"]), 1)


if __name__ == "__main__":
    unittest.main()
