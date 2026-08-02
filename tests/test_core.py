"""不依赖网络与 Telethon 的核心逻辑自检：python tests/test_core.py"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tam.crypto import decrypt, encrypt, generate_master_key  # noqa: E402
from tam.db import Account, Database  # noqa: E402
from tam.ratelimit import TokenBucket  # noqa: E402


def test_crypto():
    key = generate_master_key()
    blob = encrypt(key, "1BVtsOK4Bu...fake-session")
    assert blob != "1BVtsOK4Bu...fake-session"
    assert decrypt(key, blob) == "1BVtsOK4Bu...fake-session"
    try:
        decrypt(generate_master_key(), blob)
        raise AssertionError("错误密钥应当解密失败")
    except RuntimeError:
        pass
    print("crypto            OK")


def test_db():
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.sqlite3")
        a = db.add_account(Account(label="主号", phone="+8613800138000", tags=["work"]))
        db.add_account(Account(label="小号", tags=["test"]))
        assert a.id == 1
        db.update(a.id, status="active", username="demo", session_enc="cipher")
        got = db.get(a.id)
        assert got.status == "active" and got.username == "demo"
        assert "session_enc" not in got.public() and got.public()["authorized"] is True
        assert len(db.list(tag="work")) == 1
        assert len(db.list(status="active")) == 1
        db.log(a.id, "health_check", True, "active")
        assert db.logs(a.id)[0]["action"] == "health_check"
        db.delete(a.id)
        assert db.get(a.id) is None
        print("db                OK")


def test_proxy_parse():
    os.environ.setdefault("TAM_MASTER_KEY", generate_master_key())
    from tam.manager import parse_proxy

    p = parse_proxy("socks5://u:p@1.2.3.4:1080")
    assert p == {"proxy_type": "socks5", "addr": "1.2.3.4", "port": 1080,
                 "rdns": True, "username": "u", "password": "p"}
    assert parse_proxy(None) is None
    print("proxy parse       OK")


def test_bucket():
    async def run():
        bucket = TokenBucket(rate=5, capacity=1)
        t0 = time.monotonic()
        for _ in range(3):
            await bucket.acquire()
        elapsed = time.monotonic() - t0
        assert 0.3 < elapsed < 1.0, elapsed

    asyncio.run(run())
    print("rate limiter      OK")


if __name__ == "__main__":
    test_crypto()
    test_db()
    test_proxy_parse()
    test_bucket()
    print("\n全部核心自检通过")
