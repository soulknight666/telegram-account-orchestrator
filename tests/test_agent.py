"""AI 控制层自检：工具清单、参数校验、只读/干跑/confirm/白名单策略、MCP 协议。

运行：python3 tests/test_agent.py   （无需 telethon / fastapi）
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tam.config import Settings  # noqa: E402
from tam.db import Account, Database  # noqa: E402
from tam.manager import AccountManager  # noqa: E402
from tam.tools import ToolContext, call_tool, list_tools  # noqa: E402


def make_ctx(tmp: Path, **over):
    s = Settings(
        data_dir=tmp, db_path=tmp / "t.db", master_key="x" * 32, api_id=1, api_hash="h",
        web_token="t", default_proxy=None, global_rate=0.5,
        action_min_delay=0, action_max_delay=0,
        readonly=over.get("readonly", False), dry_run=over.get("dry_run", False),
        readonly_token="", peer_allowlist=frozenset(over.get("peer_allowlist", ())),
        auto_kick_hours=over.get("auto_kick_hours", 24.0),
    )
    db = Database(s.db_path)
    return ToolContext(s, db, AccountManager(s, db)), db


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    ctx, db = make_ctx(tmp)
    db.add_account(Account(label="a1", phone="+100", tags=["x"]))

    # 1. 工具清单结构合法（可直接喂 LLM / MCP）
    tools = list_tools()
    assert tools and all({"name", "description", "inputSchema"} <= set(t) for t in tools)
    assert all(t["inputSchema"]["type"] == "object" for t in tools)
    json.dumps(tools)  # 必须可序列化
    names = {t["name"] for t in tools}
    assert {"list_accounts", "send_message", "health_check"} <= names
    assert not (names & {"login", "import_session", "export_session"}), "登录类不得开放给 Agent"
    print("tool manifest OK（%d 个工具）" % len(tools))

    # 2. 正常调用 + 统一返回结构
    r = await call_tool(ctx, "list_accounts", {})
    assert r["ok"] and len(r["result"]) == 1 and "session_enc" not in r["result"][0]
    r = await call_tool(ctx, "stats", {})
    assert r["result"]["total"] == 1
    print("call + 返回结构 OK")

    # 3. 错误不抛异常，而是结构化错误码
    assert (await call_tool(ctx, "nope", {}))["error"]["code"] == "unknown_tool"
    assert (await call_tool(ctx, "get_account", {}))["error"]["code"] == "bad_request"
    assert (await call_tool(ctx, "get_account", {"id": 1}))["error"]["code"] == "bad_request"
    assert (await call_tool(ctx, "get_account", {"account_id": 99}))["error"]["code"] == "not_found"
    print("参数校验/错误码 OK")

    # 4. 高危动作默认不执行，只返回预览
    r = await call_tool(ctx, "send_message", {"account_id": 1, "peer": "@me", "text": "hi"})
    assert r["ok"] and r["result"]["executed"] is False
    assert r["result"]["reason"] == "confirm_required"
    r = await call_tool(ctx, "delete_account", {"account_id": 1})
    assert r["result"]["executed"] is False and db.get(1) is not None
    print("confirm 保护 OK")

    # 5. 白名单
    ctx2, db2 = make_ctx(Path(tempfile.mkdtemp()), peer_allowlist=["@me"])
    db2.add_account(Account(label="a1"))
    r = await call_tool(ctx2, "send_message",
                        {"account_id": 1, "peer": "@stranger", "text": "x", "confirm": True})
    assert r["error"]["code"] == "forbidden"
    print("发送白名单 OK")

    # 6. 只读模式
    ctx3, db3 = make_ctx(Path(tempfile.mkdtemp()), readonly=True)
    assert all(t["danger"] == "read" for t in list_tools(readonly=True))
    r = await call_tool(ctx3, "add_account", {"label": "z"})
    assert r["error"]["code"] == "readonly"
    assert (await call_tool(ctx3, "list_accounts", {}))["ok"]
    print("只读模式 OK")

    # 7. 干跑模式
    ctx4, _ = make_ctx(Path(tempfile.mkdtemp()), dry_run=True)
    r = await call_tool(ctx4, "add_account", {"label": "z"})
    assert r["ok"] and r["result"]["dry_run"] is True
    assert ctx4.db.get_by_label("z") is None
    print("干跑模式 OK")

    # 8. MCP 协议应答
    from tam.mcp_server import _handle

    init = await _handle(ctx, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["result"]["capabilities"]["tools"] == {}
    lst = await _handle(ctx, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(lst["result"]["tools"]) == len(tools)
    cal = await _handle(ctx, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": "stats", "arguments": {}}})
    assert cal["result"]["isError"] is False
    body = json.loads(cal["result"]["content"][0]["text"])
    assert body["ok"] and body["result"]["total"] == 1
    bad = await _handle(ctx, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                              "params": {"name": "nope"}})
    assert bad["result"]["isError"] is True
    assert await _handle(ctx, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    print("MCP stdio 协议 OK")

    print("\n全部 AI 控制层自检通过")


if __name__ == "__main__":
    asyncio.run(main())
