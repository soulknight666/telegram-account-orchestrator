"""代理体检纯逻辑测试（不联网）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tam import proxycheck


def test_parse() -> None:
    p = proxycheck.parse("socks5://user:pass@1.2.3.4:1080")
    assert p["scheme"] == "socks5" and p["host"] == "1.2.3.4"
    assert p["port"] == 1080 and p["username"] == "user" and p["password"] == "pass"

    p2 = proxycheck.parse("http://10.0.0.1:8080")
    assert p2["scheme"] == "http" and p2["port"] == 8080


def test_parse_bad() -> None:
    for bad in ("ftp://1.2.3.4:21", "", "socks5://"):
        try:
            proxycheck.parse(bad)
        except Exception:
            continue
        raise AssertionError(f"应报错：{bad}")


def test_duplicate_ip() -> None:
    rows = [
        {"proxy": "socks5://a:1080", "ok": True, "ip": "1.1.1.1"},
        {"proxy": "socks5://a:1081", "ok": True, "ip": "1.1.1.1"},
        {"proxy": "socks5://b:1080", "ok": True, "ip": "2.2.2.2"},
        {"proxy": "socks5://c:1080", "ok": False, "error": "timeout"},
    ]
    proxycheck._annotate_duplicates(rows)
    assert rows[0]["duplicate_ip"] is True and rows[0]["duplicate_count"] == 2
    assert rows[1]["duplicate_ip"] is True
    assert rows[2]["duplicate_ip"] is False
    assert rows[3].get("duplicate_ip") in (False, None)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
    print("test_proxycheck 全部通过")
