"""spintax 解析器单元测试（纯逻辑，不联网）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tam.spintax import SpintaxError, count, expand, validate, variants


def test_plain() -> None:
    assert expand("你好") == "你好"
    assert count("你好") == 1


def test_choice() -> None:
    got = {expand("{你好|在吗}") for _ in range(50)}
    assert got == {"你好", "在吗"}
    assert count("{你好|在吗}") == 2


def test_nested_count() -> None:
    t = "{你好|嗨}{，|~}{在吗|忙吗|{吃了吗|睡了吗}}"
    assert count(t) == 2 * 2 * 4
    all_v = variants(t, limit=100)
    assert len(all_v) == 16
    assert len(set(all_v)) == 16


def test_empty_branch() -> None:
    assert count("a{|b}") == 2
    assert set(variants("a{|b}", limit=10)) == {"a", "ab"}


def test_escape() -> None:
    assert expand(r"\{not a spin\}") == "{not a spin}"
    assert count(r"\{a\|b\}") == 1
    assert expand(r"a\\b") == "a\\b"


def test_lone_close_brace_is_literal() -> None:
    # 消息里的孤立 } 不应报错
    assert expand("a}b") == "a}b"


def test_bad_syntax() -> None:
    for bad in ("{a|b", "{a|{b}", "{a|b{c"):
        try:
            count(bad)
        except SpintaxError:
            continue
        raise AssertionError(f"应报错：{bad}")


def test_validate() -> None:
    ok = validate("{你好|嗨|hey|hi|hello}")
    assert ok["ok"] and ok["variants"] == 5 and len(ok["preview"]) <= 5
    assert not ok.get("warning")

    few = validate("{a|b}")
    assert few["ok"] and few["warning"]

    bad = validate("{a|b")
    assert bad["ok"] is False and bad["error"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
    print("test_spintax 全部通过")
