r"""内置的纯 Python tdata 解析器（不依赖 opentele）。

为什么要自己写：opentele 在 Python 3.13 下类扩展机制失效（上游 issue #133/#145），
表现为能 import、但解析出 0 个账号，最后抛 "No account has been loaded"。
本模块直接按 Telegram Desktop 的存储格式读取，只依赖 cryptography：

  key_datas -> (salt, 加密的本地主密钥, 加密的账号索引)
  本地主密钥 = PBKDF2-SHA512(sha512(salt + passcode + salt), salt, iter)
  各账号分片 D877F783D5D3EF8C[#n] -> MTP 授权块(blockId=75) -> {user_id, main_dc, auth_keys}
  auth_key + dc -> Telethon StringSession

所有函数都不抛裸异常给上层：read_tdata() 返回带逐步骤明细的 TDataReport。
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TDF_MAGIC = b"TDF$"
DBI_MTP_AUTHORIZATION = 75
LOCAL_KEY_SIZE = 256
WIDE_IDS_TAG = -1
KEY_FILE = "key_datas"
DC_PORT = 443
DC_IPS = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}


class TDataError(Exception):
    """tdata 解析失败（消息为面向用户的人话）。"""


# --------------------------------------------------------------------------- 加密原语

def _aes_ige_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if len(ciphertext) % 16:
        raise TDataError("密文长度不是 16 的整数倍，文件可能已损坏")
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()  # noqa: S305 - IGE 手工实现
    iv1, iv2 = iv[:16], iv[16:]
    out = bytearray()
    prev_c, prev_p = iv1, iv2
    for i in range(0, len(ciphertext), 16):
        block = ciphertext[i:i + 16]
        x = bytes(a ^ b for a, b in zip(block, prev_p))
        y = dec.update(x)
        plain = bytes(a ^ b for a, b in zip(y, prev_c))
        out += plain
        prev_c, prev_p = block, plain
    return bytes(out)


def _prepare_aes_old(msg_key: bytes, auth_key: bytes) -> tuple[bytes, bytes]:
    """TDesktop 本地存储用的旧版 AES 密钥派生（x=8）。"""
    x = 8
    sha1 = hashlib.sha1
    a = sha1(msg_key + auth_key[x:x + 32]).digest()
    b = sha1(auth_key[x + 32:x + 48] + msg_key + auth_key[x + 48:x + 64]).digest()
    c = sha1(auth_key[x + 64:x + 96] + msg_key).digest()
    d = sha1(msg_key + auth_key[x + 96:x + 128]).digest()
    aes_key = a[:8] + b[8:20] + c[4:16]
    aes_iv = a[8:20] + b[:8] + c[16:20] + d[:8]
    return aes_key, aes_iv


def _decrypt_local(encrypted: bytes, key: bytes) -> bytes:
    if len(encrypted) <= 16:
        raise TDataError("加密块太短，文件可能已损坏")
    msg_key = encrypted[:16]
    aes_key, aes_iv = _prepare_aes_old(msg_key, key)
    decrypted = _aes_ige_decrypt(encrypted[16:], aes_key, aes_iv)
    if hashlib.sha1(decrypted).digest()[:16] != msg_key:
        raise TDataError("解密校验失败：本地密码（passcode）不正确，或文件已损坏")
    full_len = struct.unpack("<I", decrypted[:4])[0]
    if full_len > len(decrypted) or full_len < 4:
        raise TDataError("解密后长度字段异常，文件可能已损坏")
    return decrypted[4:full_len]


def create_local_key(salt: bytes, passcode: bytes = b"") -> bytes:
    """由盐值与本地密码推导 256 字节本地主密钥。"""
    iterations = 100_000 if passcode else 1
    hashed = hashlib.sha512(salt + passcode + salt).digest()
    return hashlib.pbkdf2_hmac("sha512", hashed, salt, iterations, LOCAL_KEY_SIZE)


# --------------------------------------------------------------------------- 容器格式

class _Stream:
    """Qt QDataStream 风格的大端读取器。"""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def read(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise TDataError("数据提前结束（文件被截断或格式不符）")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def u32(self) -> int:
        return struct.unpack(">I", self.read(4))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.read(4))[0]

    def i64(self) -> int:
        return struct.unpack(">q", self.read(8))[0]

    def buf(self) -> bytes:
        n = self.u32()
        if n == 0xFFFFFFFF:
            return b""
        return self.read(n)

    @property
    def left(self) -> int:
        return len(self.data) - self.pos


def file_part(name: str) -> str:
    """把逻辑名（如 "data"）映射成 tdata 里的分片文件名（如 D877F783D5D3EF8C）。"""
    digest = hashlib.md5(name.encode()).digest()[:8]  # noqa: S324 - 上游格式规定
    value = struct.unpack("<Q", digest)[0]
    out = []
    for _ in range(16):
        out.append("0123456789ABCDEF"[value & 0x0F])
        value >>= 4
    return "".join(out)


def read_tdf(path: Path) -> bytes:
    """读取一个 TDF 容器文件，校验 md5 后返回内容体。"""
    raw = path.read_bytes()
    if len(raw) < 24 or raw[:4] != TDF_MAGIC:
        raise TDataError(f"{path.name} 不是 TDF 容器（缺少 TDF$ 头）")
    version = struct.unpack("<i", raw[4:8])[0]
    body = raw[8:-16]
    checksum = raw[-16:]
    expect = hashlib.md5(  # noqa: S324 - 上游格式规定
        body + struct.pack("<I", len(body)) + struct.pack("<i", version) + TDF_MAGIC
    ).digest()
    if checksum != expect:
        raise TDataError(f"{path.name} 校验和不匹配，文件已损坏")
    return body


def _open_data_file(base: Path, name: str) -> tuple[Path, bytes]:
    """打开 name 对应的分片（优先 xxx，其次 xxx1/xxx0 与 s 后缀变体）。"""
    part = file_part(name)
    candidates = [base / f"{part}s", base / part, base / f"{part}1", base / f"{part}0"]
    errors = []
    for cand in candidates:
        if not cand.is_file():
            continue
        try:
            return cand, read_tdf(cand)
        except TDataError as exc:
            errors.append(f"{cand.name}: {exc}")
    if errors:
        raise TDataError("；".join(errors))
    raise TDataError(f"找不到分片文件 {part}（逻辑名 {name}）")


# --------------------------------------------------------------------------- 结果对象

@dataclass
class TDataAccount:
    index: int
    user_id: int
    main_dc: int
    auth_keys: dict[int, bytes] = field(default_factory=dict)

    def to_string_session(self) -> str:
        import base64
        import ipaddress

        key = self.auth_keys.get(self.main_dc)
        if key is None:
            raise TDataError(f"账号 {self.user_id} 缺少主 DC {self.main_dc} 的授权密钥")
        ip = DC_IPS.get(self.main_dc)
        if ip is None:
            raise TDataError(f"未知的 DC 编号：{self.main_dc}")
        packed = struct.pack(
            ">B4sH256s", self.main_dc, ipaddress.ip_address(ip).packed, DC_PORT, key
        )
        return "1" + base64.urlsafe_b64encode(packed).decode("ascii")

    def summary(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "user_id": self.user_id,
            "main_dc": self.main_dc,
            "dcs": sorted(self.auth_keys),
        }


@dataclass
class TDataReport:
    """逐步骤诊断报告，调试模式直接展示给用户。"""

    path: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    accounts: list[TDataAccount] = field(default_factory=list)
    error: str | None = None

    def step(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append({"name": name, "ok": ok, "detail": detail})

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.accounts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ok": self.ok,
            "error": self.error,
            "steps": self.steps,
            "accounts": [a.summary() for a in self.accounts],
        }

    def as_text(self) -> str:
        lines = [f"目录：{self.path}"]
        for s in self.steps:
            mark = "✓" if s["ok"] else "✗"
            lines.append(f"  {mark} {s['name']}：{s['detail']}" if s["detail"]
                         else f"  {mark} {s['name']}")
        for a in self.accounts:
            lines.append(
                f"  → 账号 user_id={a.user_id} 主DC={a.main_dc} 已授权DC={sorted(a.auth_keys)}"
            )
        if self.error:
            lines.append(f"  ✗ 失败：{self.error}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- 解析主流程

def _locate(path: Path) -> Path:
    """允许用户传 tdata 的父目录，自动下钻一层。"""
    if (path / KEY_FILE).is_file():
        return path
    child = path / "tdata"
    if (child / KEY_FILE).is_file():
        return child
    raise TDataError(
        f"{path} 里没有 key_datas 文件，不是有效的 tdata 目录"
        "（正确的目录里应包含 key_datas 与 D877F783D5D3EF8C）"
    )


def _parse_mtp_authorization(data: bytes) -> list[TDataAccount]:
    st = _Stream(data)
    legacy_user_id = st.i32()
    legacy_main_dc = st.i32()
    accounts: list[TDataAccount] = []

    if legacy_user_id == WIDE_IDS_TAG and legacy_main_dc == WIDE_IDS_TAG:
        # 新版（宽 64 位 user_id）：真实值紧跟在标记后面
        user_id = st.i64()
        main_dc = st.i32()
    else:
        user_id, main_dc = legacy_user_id, legacy_main_dc

    count = st.i32()
    keys: dict[int, bytes] = {}
    for _ in range(max(count, 0)):
        if st.left < 260:
            break
        dc = st.i32()
        keys[dc] = st.read(256)

    accounts.append(TDataAccount(index=0, user_id=user_id, main_dc=main_dc, auth_keys=keys))
    return accounts


def _find_mtp_blocks(plain: bytes) -> list[bytes]:
    """在解密后的数据流里找出所有 MTP 授权块（blockId=75）的 serialized 载荷。

    TDesktop 的块流里存在很多长度未知的块，无法逐个顺序跳过，
    所以直接扫描 4 字节对齐的 blockId 标记，再校验长度与内容合理性。
    """
    tag = struct.pack(">I", DBI_MTP_AUTHORIZATION)
    out: list[bytes] = []
    pos = 0
    while True:
        i = plain.find(tag, pos)
        if i < 0:
            break
        pos = i + 1
        if i % 4:                       # 块流是 4 字节对齐的
            continue
        body = plain[i + 4:]
        if len(body) < 4:
            continue
        size = struct.unpack(">I", body[:4])[0]
        if size == 0xFFFFFFFF or size < 16 or size > len(body) - 4:
            continue
        payload = body[4:4 + size]
        if len(payload) >= 8 and struct.unpack(">i", payload[:4])[0] in (WIDE_IDS_TAG,) or size >= 260:
            out.append(payload)
    return out


def _read_account(base: Path, key: bytes, index: int, report: TDataReport) -> TDataAccount | None:
    name = "data" if index == 0 else f"data#{index + 1}"
    try:
        _, body = _open_data_file(base, name)
        # data 文件的 TDF 体是“整个加密 blob”，解密后才是块流
        st = _Stream(body)
        blob = st.buf()
        plain = _decrypt_local(blob if blob else body, key)
        found: TDataAccount | None = None
        for serialized in _find_mtp_blocks(plain):
            try:
                parsed = _parse_mtp_authorization(serialized)
            except TDataError:
                continue
            if parsed and parsed[0].auth_keys:
                found = parsed[0]
                found.index = index
                break
        if found is None:
            report.step(f"账号 #{index}", False, "分片里没有 MTP 授权块（该号可能未登录）")
            return None
        report.step(
            f"账号 #{index}", True,
            f"user_id={found.user_id} 主DC={found.main_dc} 密钥数={len(found.auth_keys)}",
        )
        return found
    except TDataError as exc:
        report.step(f"账号 #{index}", False, str(exc))
        return None


def read_tdata(path: Path | str, passcode: str | None = None) -> TDataReport:
    """解析一个 tdata 目录，返回诊断报告（不抛异常）。"""
    report = TDataReport(path=str(path))
    try:
        base = _locate(Path(path))
        report.step("定位目录", True, str(base))

        body = read_tdf(base / KEY_FILE)
        report.step("读取 key_datas", True, f"长度 {len(body)}，校验通过")

        st = _Stream(body)
        salt = st.buf()
        key_enc = st.buf()
        info_enc = st.buf()
        report.step("读取盐值", True,
                    f"salt={len(salt)}B key={len(key_enc)}B info={len(info_enc)}B")

        passcode_bytes = (passcode or "").encode("utf-8")
        local_key_raw = create_local_key(salt, passcode_bytes)
        try:
            key = _decrypt_local(key_enc, local_key_raw)
        except TDataError as exc:
            hint = "本地密码不正确" if passcode else "该 tdata 设了本地密码（passcode），请在导入时填写"
            report.step("解出本地主密钥", False, f"{hint}（{exc}）")
            report.error = hint
            return report
        report.step("解出本地主密钥", True, "有本地密码" if passcode else "无本地密码")

        info = _Stream(_decrypt_local(info_enc, key))
        count = info.i32()
        order = []
        for _ in range(max(count, 0)):
            if info.left < 4:
                break
            order.append(info.i32())
        if not order:
            order = [0]
        report.step("读取账号列表", True, f"共 {len(order)} 个账号位，序号 {order}")

        for idx in order:
            acc = _read_account(base, key, idx, report)
            if acc is not None:
                report.accounts.append(acc)

        if not report.accounts:
            report.error = "tdata 里没有解析出任何已登录账号"
    except TDataError as exc:
        report.error = str(exc)
    except Exception as exc:  # noqa: BLE001 - 报告式返回，绝不把裸栈丢给上层
        report.error = f"{type(exc).__name__}: {exc}"
    return report


def tdata_string_sessions(path: Path | str, passcode: str | None = None) -> tuple[list[str], TDataReport]:
    """解析 tdata 并直接产出 Telethon StringSession 列表。"""
    report = read_tdata(path, passcode)
    sessions: list[str] = []
    for acc in report.accounts:
        try:
            sessions.append(acc.to_string_session())
        except TDataError as exc:
            report.step(f"生成会话 #{acc.index}", False, str(exc))
    if sessions:
        report.step("生成会话", True, f"共 {len(sessions)} 个 StringSession")
    return sessions, report
