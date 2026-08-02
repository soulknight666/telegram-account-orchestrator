"""Session 字符串的静态加密。

设计要点：
- session 字符串等价于账号登录凭证，必须加密落盘。
- 主密钥只存在于环境变量 / 系统密钥链，不写入数据库。
- 采用 scrypt 派生 + Fernet(AES-128-CBC + HMAC) 认证加密，每条记录独立 salt。
"""
from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

SALT_LEN = 16


def generate_master_key() -> str:
    """生成可放入 .env 的主密钥。"""
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def _fernet(master_key: str, salt: bytes) -> Fernet:
    kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)
    key = kdf.derive(master_key.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(master_key: str, plaintext: str) -> str:
    salt = os.urandom(SALT_LEN)
    token = _fernet(master_key, salt).encrypt(plaintext.encode("utf-8"))
    return base64.urlsafe_b64encode(salt + token).decode()


def decrypt(master_key: str, blob: str) -> str:
    raw = base64.urlsafe_b64decode(blob.encode())
    salt, token = raw[:SALT_LEN], raw[SALT_LEN:]
    try:
        return _fernet(master_key, salt).decrypt(token).decode("utf-8")
    except InvalidToken as exc:  # 主密钥错误或数据被篡改
        raise RuntimeError("解密失败：主密钥不匹配或数据已损坏") from exc
