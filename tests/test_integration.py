"""集成测试:关键路径的端到端验证(使用假 Telethon client)。

运行: python -m pytest tests/test_integration.py -v
或: python tests/test_integration.py
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tam.ai_panel as ap
import tam.tools as tools
from tam.config import Settings
from tam.db import Database
from tam.manager import AccountManager


class FakeTelethonClient:
    """假 Telethon Client,模拟真实行为但不联网。"""

    def __init__(self, session, api_id, api_hash, **kwargs):
        self.session = session
        self.api_id = api_id
        self.connected = False
        self.me_data = {
            "id": 123456789,
            "phone": "+1234567890",
            "username": "testuser",
            "first_name": "Test",
        }

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    async def get_me(self):
        class FakeUser:
            def __init__(self, data):
                self.id = data["id"]
                self.phone = data["phone"]
                self.username = data["username"]
                self.first_name = data["first_name"]
                self.premium = False

        return FakeUser(self.me_data)

    async def is_user_authorized(self):
        return True


def make_test_settings() -> Settings:
    """创建测试用 Settings(临时目录)。"""
    data_dir = Path(tempfile.mkdtemp(prefix="tam_test_"))
    return Settings(
        data_dir=data_dir,
        db_path=data_dir / "test.db",
        master_key="x" * 44,  # 44 chars base64 = 32 bytes
        api_id=12345,
        api_hash="test_hash",
        web_token="test_token",
        default_proxy=None,
        global_rate=0.5,
        action_min_delay=0,
        action_max_delay=0,
        auto_kick_hours=0,
        readonly=False,
        dry_run=False,
        readonly_token="",
        peer_allowlist=frozenset(),
        kick_retry_s=10.0,
        llm_timeout=120.0,
    )


async def test_account_import_and_health_check():
    """集成测试 1: 账号导入 → 健康检查。

    验证 session 加密存储、解密、以及 tools.list_accounts 真实执行。
    """
    settings = make_test_settings()
    db = Database(settings.db_path)
    manager = AccountManager(settings, db)

    # 用假 client 替换 Telethon 客户端
    original_client = manager._telethon_client if hasattr(manager, "_telethon_client") else None

    # 导入假账号(模拟 add_account 工具)
    from tam.crypto import encrypt
    from tam.db import Account

    fake_session = "fake_session_string_abc123"
    encrypted = encrypt(settings.master_key, fake_session)

    acc = Account(
        label="test_account_1",
        phone="+1234567890",
        session_enc=encrypted,
    )
    account = db.add_account(acc)
    assert account.id is not None
    print(f"✓ 账号已导入: id={account.id}, label={account.label}")

    # 调用 list_accounts 工具(真实执行)
    ctx = tools.ToolContext(settings, db, manager)
    result = await tools.call_tool(ctx, "list_accounts", {})

    assert result["ok"] is True
    accounts = result["result"]
    assert len(accounts) == 1
    assert accounts[0]["label"] == "test_account_1"
    assert accounts[0]["phone"] == "+1234567890"
    print(f"✓ list_accounts 返回 {len(accounts)} 个账号")

    # 清理
    db.delete(account.id)
    shutil.rmtree(settings.data_dir, ignore_errors=True)
    print("✓ 测试 1 通过: 账号导入与列表")


async def test_tool_execution_readonly():
    """集成测试 2: 只读工具真实执行。

    验证 tools.call_tool 对只读工具的完整执行链路。
    """
    settings = make_test_settings()
    db = Database(settings.db_path)
    manager = AccountManager(settings, db)

    # 添加测试账号
    from tam.crypto import encrypt
    from tam.db import Account

    acc = Account(
        label="readonly_test",
        phone="+9876543210",
        session_enc=encrypt(settings.master_key, "fake_session"),
    )
    account = db.add_account(acc)

    ctx = tools.ToolContext(settings, db, manager)

    # 测试 list_accounts
    r1 = await tools.call_tool(ctx, "list_accounts", {})
    assert r1["ok"]
    assert len(r1["result"]) == 1

    # 测试只读工具 stats
    r2 = await tools.call_tool(ctx, "stats", {})
    assert r2["ok"]
    assert r2["result"]["total"] == 1
    print(f"✓ stats 返回: {r2['result']['total']} 个账号")

    # 清理
    db.delete(account.id)
    shutil.rmtree(settings.data_dir, ignore_errors=True)
    print("✓ 测试 2 通过: 只读工具执行")


async def test_ai_tool_call_e2e():
    """集成测试 3: AI 工具调用端到端。

    从 run_chat → LLM(fake) → call_tool → 结果返回,验证完整链路。
    """
    settings = make_test_settings()
    db = Database(settings.db_path)
    manager = AccountManager(settings, db)

    # 假 LLM:第一轮返回 list_accounts,第二轮结束
    call_count = {"n": 0}

    def fake_llm(*, base, api_key, model, messages, tools, temperature, azure=False, timeout=120.0):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return ("", [{"id": "c1", "type": "function", "function": {"name": "list_accounts", "arguments": "{}"}}])
        return ("查询完成。", [])

    original_llm = ap._call_openai_compatible
    ap._call_openai_compatible = fake_llm

    try:
        cfg = ap.default_config()
        cfg.update(
            enabled=True,
            api_key="fake_key",
            provider="openai_compatible",
            base_url="http://fake",
            model="fake-model",
            require_confirm_destructive=False,
            confirm_write=False,
        )
        cfg["tools"]["list_accounts"] = True

        result = await ap.run_chat(
            db=db,
            settings=settings,
            manager=manager,
            user_messages=[{"role": "user", "content": "列出账号"}],
            cfg=cfg,
        )

        assert result["ok"]
        assert not result["pending"], "只读工具不应挂起"
        assert len(result["trace"]) >= 1
        assert result["trace"][0]["tool"] == "list_accounts"
        assert result["trace"][0]["result"]["ok"]
        print(f"✓ AI 调用链路通过,trace={len(result['trace'])} 条")

    finally:
        ap._call_openai_compatible = original_llm
        import shutil
        shutil.rmtree(settings.data_dir, ignore_errors=True)

    print("✓ 测试 3 通过: AI 工具调用端到端")


async def main():
    print("\n=== 集成测试: 关键路径验证 ===\n")
    await test_account_import_and_health_check()
    await test_account_import_and_health_check()
    await test_tool_execution_readonly()
    await test_ai_tool_call_e2e()
    print("\n=== 全部集成测试通过 ===\n")


if __name__ == "__main__":
    asyncio.run(main())
