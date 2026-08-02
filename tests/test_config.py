""".env 解析与向导配置写入的回归自检。

覆盖历史 bug：
- TAM_WEB_TOKEN 行尾的行内注释被当成令牌的一部分 → 全局 401
- TAM_API_ID 留空时 int('') 直接崩溃
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tam.config import Settings, _int_env, _float_env  # noqa: E402


def _fresh_env(**kv: str) -> None:
    for key in list(os.environ):
        if key.startswith("TAM_"):
            del os.environ[key]
    os.environ.update(kv)


def test_inline_comment_and_quotes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text(
            "# 注释行\n"
            "TAM_MASTER_KEY=abc123==   # 向导自动生成\n"
            "TAM_WEB_TOKEN=Tok3n-With_Comment  # Web/API 访问令牌\n"
            'TAM_READONLY_TOKEN="quoted-token"\n'
            "TAM_API_ID=\n"
            "TAM_API_HASH=   \n"
            "TAM_RATE=0.5  # 每账号每秒\n"
            "TAM_DEFAULT_PROXY=       # socks5://user:pass@host:1080\n"
            f"TAM_DATA_DIR={tmp}/data\n",
            encoding="utf-8",
        )
        _fresh_env()
        s = Settings.load(env)
        assert s.master_key == "abc123==", s.master_key
        assert s.web_token == "Tok3n-With_Comment", repr(s.web_token)
        assert s.readonly_token == "quoted-token", repr(s.readonly_token)
        assert s.api_id == 0 and s.api_hash == ""
        assert s.global_rate == 0.5
        assert s.default_proxy is None, s.default_proxy
    print(".env 行内注释 / 引号 / 空值解析 OK")


def test_numeric_fallbacks() -> None:
    _fresh_env(TAM_API_ID="", TAM_RATE="abc", TAM_MIN_DELAY="", TAM_MAX_DELAY="30")
    assert _int_env("TAM_API_ID", 0) == 0
    assert _float_env("TAM_RATE", 0.5) == 0.5
    assert _float_env("TAM_MIN_DELAY", 8.0) == 8.0
    assert _float_env("TAM_MAX_DELAY", 25.0) == 30.0
    print("数值环境变量回退 OK")


def test_master_key_required() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text(f"TAM_DATA_DIR={tmp}/data\n", encoding="utf-8")
        _fresh_env()
        try:
            Settings.load(env)
        except RuntimeError as exc:
            assert "TAM_MASTER_KEY" in str(exc)
        else:
            raise AssertionError("缺少主密钥时应报错")
    print("主密钥必填校验 OK")


def test_setup_wizard_env_writer() -> None:
    """向导写入后，配置必须能被 Settings 原样读回（防止令牌不一致）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("tam_setup", ROOT / "setup.py")
    assert spec and spec.loader
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)

    assert setup.unset("") and setup.unset("change-me") and setup.unset("1234567")
    assert not setup.unset("real-token-123")

    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text(
            "TAM_MASTER_KEY=          # 向导自动生成\n"
            "TAM_WEB_TOKEN=           # Web/API 访问令牌\n"
            "TAM_DATA_DIR=./data\n",
            encoding="utf-8",
        )
        setup.ENV_PATH = env
        setup.EXAMPLE_PATH = Path(tmp) / "missing.example"
        token = "Abc123_token-XYZ"
        key = setup.gen_master_key()
        setup.set_env({"TAM_MASTER_KEY": key, "TAM_WEB_TOKEN": token,
                       "TAM_DATA_DIR": f"{tmp}/data"})

        back = setup.read_env(  ) if False else None  # noqa: F841
        _fresh_env()
        s = Settings.load(env)
        assert s.web_token == token, repr(s.web_token)
        assert s.master_key == key, repr(s.master_key)

        # 幂等：再跑一次不会丢失或重复写入
        setup.set_env({"TAM_API_ID": "1234"})
        text = env.read_text(encoding="utf-8")
        assert text.count("TAM_WEB_TOKEN=") == 1
        assert text.count("TAM_API_ID=") == 1
        _fresh_env()
        s2 = Settings.load(env)
        assert s2.web_token == token and s2.api_id == 1234
    print("向导写入 ↔ 配置读取一致性 OK")


def main() -> None:
    test_inline_comment_and_quotes()
    test_numeric_fallbacks()
    test_master_key_required()
    test_setup_wizard_env_writer()
    print("\n全部配置自检通过")


if __name__ == "__main__":
    main()
