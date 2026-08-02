"""内置 tdata 解析器的自测：现造一份合成 tdata，再把它解回来。"""
from __future__ import annotations

import hashlib
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tam.tdata_native import (  # noqa: E402
    DBI_MTP_AUTHORIZATION,
    TDF_MAGIC,
    _aes_ige_decrypt,
    _prepare_aes_old,
    create_local_key,
    file_part,
    read_tdata,
    read_tdf,
    tdata_string_sessions,
)


def aes_ige_encrypt(plain: bytes, key: bytes, iv: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()  # noqa: S305
    iv1, iv2 = iv[:16], iv[16:]
    out = bytearray()
    prev_c, prev_p = iv1, iv2
    for i in range(0, len(plain), 16):
        block = plain[i:i + 16]
        x = bytes(a ^ b for a, b in zip(block, prev_c))
        y = enc.update(x)
        cipher = bytes(a ^ b for a, b in zip(y, prev_p))
        out += cipher
        prev_c, prev_p = cipher, block
    return bytes(out)


def encrypt_local(plain: bytes, key: bytes) -> bytes:
    body = struct.pack("<I", len(plain) + 4) + plain
    pad = (-len(body)) % 16
    body += b"\x00" * pad
    msg_key = hashlib.sha1(body).digest()[:16]
    aes_key, aes_iv = _prepare_aes_old(msg_key, key)
    return msg_key + aes_ige_encrypt(body, aes_key, aes_iv)


def write_tdf(path: Path, body: bytes, version: int = 6006002) -> None:
    checksum = hashlib.md5(  # noqa: S324
        body + struct.pack("<I", len(body)) + struct.pack("<i", version) + TDF_MAGIC
    ).digest()
    path.write_bytes(TDF_MAGIC + struct.pack("<i", version) + body + checksum)


def qbuf(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def make_tdata(root: Path, passcode: bytes = b"", user_id: int = 8522045839,
               main_dc: int = 5) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    salt = b"\x11" * 32
    local_key = create_local_key(salt, passcode)
    key = bytes(range(256))

    write_tdf(root / "key_datas",
              qbuf(salt) + qbuf(encrypt_local(key, local_key))
              + qbuf(encrypt_local(struct.pack(">ii", 1, 0), key)))

    auth_key = bytes((i * 7) % 256 for i in range(256))
    serialized = (struct.pack(">ii", -1, -1) + struct.pack(">q", user_id)
                  + struct.pack(">i", main_dc) + struct.pack(">i", 1)
                  + struct.pack(">i", main_dc) + auth_key)
    # 真实格式：TDF 体 = 整个加密 blob，解密后才是块流
    stream = (b"\x00" * 4 + struct.pack(">I", 3) + b"\x01" * 4          # 一个无关的块
              + struct.pack(">I", DBI_MTP_AUTHORIZATION) + qbuf(serialized))
    write_tdf(root / (file_part("data") + "s"), qbuf(encrypt_local(stream, key)))
    return root


def test_file_part() -> None:
    assert file_part("data") == "D877F783D5D3EF8C"
    assert file_part("data#2") == "A7FDF864FBC10B77"


def test_tdf_roundtrip(tmp: Path) -> None:
    p = tmp / "x"
    write_tdf(p, b"hello world 1234")
    assert read_tdf(p) == b"hello world 1234"


def test_ige_roundtrip() -> None:
    key, iv, plain = b"k" * 32, b"v" * 32, b"p" * 64
    assert _aes_ige_decrypt(aes_ige_encrypt(plain, key, iv), key, iv) == plain


def test_parse_no_passcode(tmp: Path) -> None:
    root = make_tdata(tmp / "a" / "tdata")
    rep = read_tdata(root)
    assert rep.ok, rep.as_text()
    assert rep.accounts[0].user_id == 8522045839
    assert rep.accounts[0].main_dc == 5


def test_parent_dir(tmp: Path) -> None:
    make_tdata(tmp / "b" / "tdata")
    rep = read_tdata(tmp / "b")            # 传父目录也能下钻
    assert rep.ok, rep.as_text()


def test_passcode(tmp: Path) -> None:
    root = make_tdata(tmp / "c" / "tdata", passcode=b"secret")
    assert not read_tdata(root).ok                        # 不填密码：失败
    assert not read_tdata(root, "wrong").ok               # 密码错：失败
    assert read_tdata(root, "secret").ok                  # 密码对：成功


def test_bad_path(tmp: Path) -> None:
    rep = read_tdata(tmp / "nope")
    assert not rep.ok and rep.error and "key_datas" in rep.error


def test_string_session(tmp: Path) -> None:
    root = make_tdata(tmp / "d" / "tdata")
    sessions, rep = tdata_string_sessions(root)
    assert len(sessions) == 1 and sessions[0].startswith("1")
    assert len(sessions[0]) > 300, rep.as_text()


def test_truncated(tmp: Path) -> None:
    root = make_tdata(tmp / "e" / "tdata")
    kd = root / "key_datas"
    kd.write_bytes(kd.read_bytes()[:40])
    assert not read_tdata(root).ok


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_file_part()
        test_tdf_roundtrip(tmp)
        test_ige_roundtrip()
        test_parse_no_passcode(tmp)
        test_parent_dir(tmp)
        test_passcode(tmp)
        test_bad_path(tmp)
        test_string_session(tmp)
        test_truncated(tmp)
    print("ALL PASS")
