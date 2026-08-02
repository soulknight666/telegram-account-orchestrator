"""最小 MCP（Model Context Protocol）stdio 服务端，零额外依赖。

启动：
    python -m tam.mcp_server            # 默认按 .env 策略
    TAM_READONLY=1 python -m tam.mcp_server

在 Claude Desktop / Cursor 等客户端中配置：
    {"mcpServers": {"tam": {"command": "python", "args": ["-m", "tam.mcp_server"],
                            "cwd": "/path/to/tam"}}}

实现 JSON-RPC 2.0 over stdio 的三个方法：initialize / tools/list / tools/call。
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

PROTOCOL_VERSION = "2024-11-05"


def _ctx():
    from .config import Settings
    from .db import Database
    from .manager import AccountManager
    from .tools import ToolContext

    s = Settings.load()
    db = Database(s.db_path)
    return ToolContext(s, db, AccountManager(s, db))


async def _handle(ctx: Any, req: dict[str, Any]) -> dict[str, Any] | None:
    from .tools import call_tool, list_tools

    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        result: Any = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "telegram-account-manager", "version": "1.1.0"},
            "instructions": (
                "管理用户自有的 Telegram 账号。发送消息、改资料、踢设备、删账号属于高危动作，"
                "必须先向用户展示预览（不传 confirm），得到确认后再传 confirm=true。"
                "登录、导入/导出会话不对 Agent 开放。"
            ),
        }
    elif method in ("notifications/initialized", "initialized"):
        return None
    elif method == "tools/list":
        result = {"tools": list_tools(readonly=ctx.readonly)}
    elif method == "tools/call":
        params = req.get("params") or {}
        payload = await call_tool(ctx, params.get("name", ""), params.get("arguments") or {})
        result = {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
            "isError": not payload.get("ok", False),
        }
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


async def serve_stdio() -> None:
    ctx = _ctx()
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            return
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = await _handle(ctx, req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(serve_stdio())
