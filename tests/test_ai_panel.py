"""AI 面板 run_chat 两阶段确认逻辑自检：pending 挂起 / approve 批准执行 / 取消回灌。

运行：python3 tests/test_ai_panel.py   （无需 telethon / fastapi / 真实 LLM）
用 monkeypatch 顶掉 LLM HTTP 与工具执行，只验证 run_chat 的阶段机。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tam.ai_panel as ap  # noqa: E402
from tam.db import Database  # noqa: E402
from tam.config import Settings  # noqa: E402
from tam.manager import AccountManager  # noqa: E402


def make_cfg(**over) -> dict:
    cfg = ap.default_config()
    cfg.update({
        "enabled": True,
        "api_key": "test-key",
        "provider": "openai_compatible",
        "base_url": "http://127.0.0.1:9/v1",  # 会被顶掉，不发网络
        "model": "gpt-test",
        "require_confirm_destructive": True,
        "confirm_write": True,
        "tools": {t: True for t in ap.TOOLS},  # 全部授权，测试开关逻辑
    })
    cfg.update(over)
    return cfg


# 脚本化 LLM：按「轮次序号」返回预定的 (content, tool_calls)
FULL_TOOLS = [{
    "type": "function",
    "function": {"name": "list_accounts", "description": "x", "parameters": {"type": "object", "properties": {}}},
}]


def _tool_call(name: str, args: dict) -> dict:
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": __import__("json").dumps(args, ensure_ascii=False)},
    }


def _make_db():
    s = Settings(
        data_dir=__import__("tempfile").mkdtemp(), db_path=__import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "t.db",
        master_key="x" * 32, api_id=1, api_hash="h", web_token="t",
        default_proxy=None, global_rate=0.5, action_min_delay=0, action_max_delay=0,
        readonly=False, dry_run=False, readonly_token="", peer_allowlist=frozenset(),
        auto_kick_hours=24.0,
    )
    db = Database(s.db_path)
    return s, db, AccountManager(s, db)


async def main() -> None:
    s, db, mgr = _make_db()

    # === 用例 1：write 工具首轮挂起为 pending，不执行 ===
    calls: list[tuple[str, dict]] = []
    async def _fake_call_z(*a, **k):  # noqa: D401
        raise AssertionError("不应实际执行；应进入 pending")
    ap.call_tool = _fake_call_z
    script = {
        0: (None, [_tool_call("update_account", {"account_id": 1, "fields": {"label": "x"}})]),
    }
    orig_call = ap._call_openai_compatible
    def _fake_llm(*, base, api_key, model, messages, tools, temperature, azure=False):
        round_i = len([m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")])
        return script.get(round_i, ("完成", []))  # type: ignore[return-value]
    ap._call_openai_compatible = _fake_llm  # type: ignore
    try:
        r = await ap.run_chat(db=db, settings=s, manager=mgr,
                              user_messages=[{"role": "user", "content": "改账号"}], cfg=make_cfg())
    finally:
        ap._call_openai_compatible = orig_call
    assert r["ok"], r
    assert len(r["pending"]) == 1 and r["pending"][0]["tool"] == "update_account"
    assert r["pending"][0]["danger"] == "write"
    assert not r["trace"], "挂起阶段不应有任何已执行痕迹"

    # === 用例 2：approve 批准后执行；取消项回灌"已取消" ===
    executed: list[tuple[str, dict]] = []
    async def _fake_call_r(ctx, name, args=None):  # noqa: D401
        executed.append((name, args or {}))
        return {"ok": True, "tool": name, "result": {"executed": True}}
    ap.call_tool = _fake_call_r
    script = {
        0: (None, [
            _tool_call("twofa_set", {"account_id": 1, "new": "pw"}),            # destructive，批准
            _tool_call("update_account", {"account_id": 1, "fields": {"label": "y"}}),  # write，取消
        ]),
        1: ("已办妥。", []),                                  # 批准执行轮之后：模型收尾
    }
    def _fake_llm2(*, base, api_key, model, messages, tools, temperature, azure=False):
        round_i = len([m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")])
        return script.get(round_i, ("完成", []))  # type: ignore[return-value]
    ap._call_openai_compatible = _fake_llm2  # type: ignore
    try:
        r = await ap.run_chat(
            db=db, settings=s, manager=mgr,
            user_messages=[{"role": "user", "content": "改二验+改标签"}],
            cfg=make_cfg(),
            approve=[
                {"tool": "twofa_set", "arguments": {"account_id": 1, "new": "pw"}, "approved": True},
                {"tool": "update_account", "arguments": {"account_id": 1, "fields": {"label": "y"}}, "approved": False},
            ],
        )
    finally:
        ap._call_openai_compatible = orig_call
    assert r["ok"], r
    assert not r["pending"], "approve 后不应再挂起"
    tools_executed = [t for t, _ in executed]
    assert "twofa_set" in tools_executed, "批准项应被执行"
    assert "update_account" not in tools_executed, "取消项不得执行"
    # 批准即授权：带工具层 confirm 门的工具必须补 confirm=True，
    # 否则会被工具自身的 confirm_required 挡住 —— 表现为「批准了却没执行」
    approved_args = [a for t, a in executed if t == "twofa_set"][0]
    assert approved_args.get("confirm") is True, \
        "批准后应补 confirm=True，否则工具层安全门会拒绝执行"
    # 取消项应以 cancelled 回灌给模型，落进 trace
    cancelled_msgs = [m for m in r["trace"] if m["tool"] == "update_account"]
    assert cancelled_msgs and not cancelled_msgs[0]["result"].get("ok")

    # === 用例 3：纯读不挂起，直接完成 ===
    from tam.tools import _list_accounts  # noqa: F401
    read_args: list[dict] = []
    async def _fake_call_read(ctx, name, args=None):  # noqa: D401
        read_args.append(dict(args or {}))
        return {"ok": True, "tool": name, "result": []}
    ap.call_tool = _fake_call_read
    script = {
        0: (None, [_tool_call("list_accounts", {})]),
        1: ("共 0 个账号。", []),
    }
    ap._call_openai_compatible = _fake_llm2  # type: ignore
    try:
        r = await ap.run_chat(db=db, settings=s, manager=mgr,
                              user_messages=[{"role": "user", "content": "查账号"}], cfg=make_cfg())
    finally:
        ap._call_openai_compatible = orig_call
    assert r["ok"] and not r["pending"]
    assert r["trace"] and r["trace"][0]["tool"] == "list_accounts"
    # 反向约束：只读工具的 schema 没有 confirm 参数，不得被注入，
    # 否则 call_tool 的未知参数校验会直接判 bad_request
    assert read_args and "confirm" not in read_args[0], \
        "只读工具不应被注入 confirm（其 schema 未声明该参数）"

    # === 用例 4：stream_cb 在最终文本轮被按块回调 ===
    async def _fake_call_t(ctx, name, args=None):  # noqa: D401
        return {"ok": True, "tool": name, "result": []}
    ap.call_tool = _fake_call_t
    script = {
        0: (None, [_tool_call("list_accounts", {})]),
        1: ("一二三四五六七八九十一二三四五六七八九十尾部", []),   # 纯文本最终轮
    }
    ap._call_openai_compatible = _fake_llm2  # type: ignore
    chunks: list[str] = []
    async def _cb(t: str) -> None:
        chunks.append(t)
    try:
        r = await ap.run_chat(
            db=db, settings=s, manager=mgr,
            user_messages=[{"role": "user", "content": "查账号"}], cfg=make_cfg(),
            stream_cb=_cb,
        )
    finally:
        ap._call_openai_compatible = orig_call
    assert r["ok"] and not r["pending"]
    assert chunks, "应产生流式分块"
    assert "".join(chunks) == "一二三四五六七八九十一二三四五六七八九十尾部", "分块应还原完整文本"
    assert all(len(c) <= 32 for c in chunks), "每条分块不应过大(默认 step=24 附近)"

    # === 用例 5：_call_anthropic 对 system 与末位 tool 注入 cache_control ===
    calls: list[dict] = []
    def fake_http(url, payload, *, headers, method="POST", timeout=120.0):
        calls.append({"url": url, "payload": payload})
        return {"content": [{"type": "text", "text": "ok"}]}
    orig_http = ap._http_json
    ap._http_json = fake_http  # type: ignore
    try:
        ap._call_anthropic(
            base="http://x/v1", api_key="k", model="m",
            system="你是助手", messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "t1", "input_schema": {"type": "object", "properties": {}}},
                   {"name": "t2", "input_schema": {"type": "object", "properties": {}}}],
            temperature=0.2,
        )
    finally:
        ap._http_json = orig_http  # type: ignore
    assert calls, "应发起一次 Anthropic 请求"
    sys_block = calls[0]["payload"]["system"]
    assert isinstance(sys_block, list) and sys_block[0].get("cache_control", {}).get("type") == "ephemeral"
    tools_out = calls[0]["payload"]["tools"]
    assert tools_out[-1].get("cache_control", {}).get("type") == "ephemeral", "末位 tool 应带 cache_control"
    assert "cache_control" not in tools_out[0], "非末位 tool 不应注入(保持前缀稳定)"

    print("\nAI 面板 run_chat(确认+流式) 与 Anthropic prompt cache 自检通过")


if __name__ == "__main__":
    asyncio.run(main())
