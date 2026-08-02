"""重试间隔可自定义（秒/分/时）的测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tam import autokick
from tam.config import parse_duration
from tam.db import Database


class _S:
    def __init__(self, kick_retry_s=3600.0):
        self.kick_retry_s = kick_retry_s
        self.auto_kick_hours = 24.0


class _Mgr:
    """只需要 s + db 两个属性就能跑 autokick 的读配置逻辑。"""

    def __init__(self, db, kick_retry_s=3600.0):
        self.db, self.s = db, _S(kick_retry_s)


class TestParseDuration(unittest.TestCase):
    def test_units(self):
        self.assertEqual(parse_duration("45s", 0), 45.0)
        self.assertEqual(parse_duration("10m", 0), 600.0)
        self.assertEqual(parse_duration("2h", 0), 7200.0)
        self.assertEqual(parse_duration("1.5h", 0), 5400.0)
        self.assertEqual(parse_duration("1h30m", 0), 5400.0)
        self.assertEqual(parse_duration("1d", 0), 86400.0)

    def test_chinese_and_long_units(self):
        self.assertEqual(parse_duration("30秒", 0), 30.0)
        self.assertEqual(parse_duration("5分钟", 0), 300.0)
        self.assertEqual(parse_duration("2小时", 0), 7200.0)
        self.assertEqual(parse_duration("90sec", 0), 90.0)
        self.assertEqual(parse_duration("15 min", 0), 900.0)

    def test_plain_number_is_seconds(self):
        self.assertEqual(parse_duration("90", 0), 90.0)
        self.assertEqual(parse_duration(120, 0), 120.0)

    def test_garbage_falls_back(self):
        for bad in ("", "   ", None, "abc", "-5", 0):
            self.assertEqual(parse_duration(bad, 42.0), 42.0, bad)


class TestRetrySetting(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.mgr = _Mgr(self.db)

    def test_default_comes_from_config(self):
        secs, src = autokick.retry_source(_Mgr(self.db, kick_retry_s=1800.0))
        self.assertEqual(secs, 1800.0)
        self.assertEqual(src, "env")

    def test_web_setting_overrides_config(self):
        out = autokick.set_retry(self.mgr, "10m")
        self.assertEqual(out["retry_after_s"], 600.0)
        self.assertEqual(out["retry_source"], "web")
        self.assertEqual(out["retry_after_text"], "10 分钟")
        self.assertEqual(autokick.retry_after(self.mgr), 600.0)

    def test_seconds_and_hours(self):
        self.assertEqual(autokick.set_retry(self.mgr, "45s")["retry_after_s"], 45.0)
        self.assertEqual(autokick.set_retry(self.mgr, "2h")["retry_after_s"], 7200.0)

    def test_clear_restores_config(self):
        autokick.set_retry(self.mgr, "30s")
        out = autokick.set_retry(self.mgr, "")
        self.assertEqual(out["retry_after_s"], 3600.0)
        self.assertEqual(out["retry_source"], "env")

    def test_bad_value_rejected(self):
        with self.assertRaises(ValueError):
            autokick.set_retry(self.mgr, "一会儿")
        with self.assertRaises(ValueError):
            autokick.set_retry(self.mgr, "1s")      # 低于 10 秒下限
        # 报错后不应该把旧值弄脏
        self.assertEqual(autokick.retry_after(self.mgr), 3600.0)

    def test_call_override_wins_for_one_run(self):
        autokick.set_retry(self.mgr, "10m")
        self.assertEqual(autokick.retry_after(self.mgr, "30s"), 30.0)
        self.assertEqual(autokick.retry_after(self.mgr), 600.0)   # 不落盘

    def test_human_gap(self):
        self.assertEqual(autokick.human_gap(45), "45 秒")
        self.assertEqual(autokick.human_gap(600), "10 分钟")
        self.assertEqual(autokick.human_gap(7200), "2 小时")


if __name__ == "__main__":
    unittest.main()
