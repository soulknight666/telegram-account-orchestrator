"""AI 控制层：把管理器的能力封装成一组带 JSON Schema 的工具。

为什么需要这一层：
- REST/CLI 面向人，形参分散、错误形式不统一，LLM / Agent 难以可靠调用。
- 这里提供：统一入口 call_tool()、机器可读清单 list_tools()、统一结果包结构。
- 同时强制安全策略：只读模式、干跑模式、高危动作必须显式 confirm、发送对象白名单。

Agent 接入方式：
- MCP：python -m tam.mcp_server（stdio）
- OpenAI/Anthropic function calling：list_tools() 直接作为 tools 参数
- HTTP：GET /api/tools、POST /api/tools/call
- CLI：python -m tam.cli call <tool> '<json>'
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import Settings
from .db import Account, Database
from .manager import AccountManager

# danger 等级：read=只读，write=会产生副作用，destructive=不可逆/对外可见
ToolFn = Callable[..., Awaitable[Any]]
_REGISTRY: dict[str, dict[str, Any]] = {}


def tool(name: str, description: str, danger: str, schema: dict[str, Any]) -> Callable[[ToolFn], ToolFn]:
    def deco(fn: ToolFn) -> ToolFn:
        _REGISTRY[name] = {
            "name": name,
            "description": description,
            "danger": danger,
            "inputSchema": {"type": "object", "additionalProperties": False, **schema},
            "fn": fn,
        }
        return fn

    return deco


def _prop(**kw: Any) -> dict[str, Any]:
    return kw


ID = _prop(type="integer", description="账号 ID")
CONFIRM = _prop(type="boolean", default=False, description="高危动作必须为 true 才会真正执行")


class ToolContext:
    """工具运行上下文与安全策略。"""

    def __init__(self, settings: Settings, db: Database, manager: AccountManager,
                 readonly: bool | None = None) -> None:
        self.s = settings
        self.db = db
        self.mgr = manager
        self.readonly = settings.readonly if readonly is None else readonly
        self.dry_run = settings.dry_run
        self.peer_allowlist = settings.peer_allowlist

    def check_peer(self, peer: str | int) -> None:
        if self.peer_allowlist and str(peer) not in self.peer_allowlist:
            raise PermissionError(
                f"目标 {peer} 不在 TAM_PEER_ALLOWLIST 白名单中，拒绝发送"
            )


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------- 只读工具 ----------------

@tool("list_settings", "读取参数面板当前值（按分组，不含密钥明文）", "read",
      {"properties": {
          "group": _prop(type="string", description="可选，只返回该分组，如 并发与速率"),
      }})
async def _list_settings(ctx: ToolContext, group: str | None = None) -> Any:
    import os
    from . import doctor
    from .api import _SETTING_SPECS, _SECRET_KEYS

    env = doctor.read_env()
    groups: dict[str, list[dict[str, Any]]] = {}
    for key, kind, default, grp, desc, restart in _SETTING_SPECS:
        if group and grp != group:
            continue
        cur = os.environ.get(key, env.get(key, default))
        groups.setdefault(grp, []).append({
            "key": key, "type": kind, "value": cur, "default": default,
            "desc": desc, "restart_required": bool(restart),
        })
    secrets = [
        {"key": k, "set": bool((os.environ.get(k) or env.get(k) or "").strip())}
        for k in sorted(_SECRET_KEYS)
    ]
    return {"groups": groups, "secrets": secrets}


@tool("update_settings", "修改参数面板中的配置项（写入 .env，热生效项立即生效）", "write",
      {"properties": {
          "values": _prop(
              type="object",
              description="键值对，键为 TAM_* 参数名，例如 {\"TAM_BATCH_CONCURRENCY\": \"5\"}",
          ),
          "confirm": CONFIRM,
      }, "required": ["values"]})
async def _update_settings(ctx: ToolContext, values: dict[str, Any] | None = None,
                           confirm: bool = False) -> Any:
    import os
    from . import doctor
    from .api import _SPEC_BY_KEY, _SECRET_KEYS, _coerce_setting

    if not values or not isinstance(values, dict):
        raise ToolError("bad_request", "values 必须是非空对象")
    if ctx.dry_run or not confirm:
        return {
            "executed": False,
            "reason": "dry_run" if ctx.dry_run else "confirm_required",
            "preview": values,
        }
    to_write: dict[str, str] = {}
    restart_keys: list[str] = []
    for key, raw in values.items():
        key = str(key)
        if key in _SECRET_KEYS:
            raise ToolError("forbidden", f"密钥类参数 {key} 不允许通过 AI 修改，请在网页参数面板操作")
        spec = _SPEC_BY_KEY.get(key)
        if spec is None:
            raise ToolError("bad_request", f"不认识的参数：{key}")
        to_write[key] = _coerce_setting(key, spec[1], raw)
        if spec[5]:
            restart_keys.append(key)
    if not to_write:
        return {"saved": 0, "restart_required": [], "values": {}}
    doctor.set_env(to_write)
    for k, v in to_write.items():
        os.environ[k] = v
    ctx.db.log(None, "settings.save", True, "ai:" + ",".join(sorted(to_write)))
    return {
        "saved": len(to_write),
        "restart_required": sorted(set(restart_keys)),
        "values": to_write,
        "note": "含 restart_required 的项需热重载/重启进程后完全生效",
    }


@tool("list_accounts", "列出账号（不含任何会话凭证）", "read",
      {"properties": {"status": _prop(type="string", description="按状态过滤"),
                      "tag": _prop(type="string", description="按标签过滤")}})
async def _list_accounts(ctx: ToolContext, status: str | None = None, tag: str | None = None) -> Any:
    return [a.public() for a in ctx.db.list(status=status, tag=tag)]


@tool("get_account", "获取单个账号详情", "read",
      {"properties": {"account_id": ID}, "required": ["account_id"]})
async def _get_account(ctx: ToolContext, account_id: int) -> Any:
    acc = ctx.db.get(account_id)
    if acc is None:
        raise ToolError("not_found", f"账号 {account_id} 不存在")
    return acc.public()


@tool("stats", "账号状态统计", "read", {"properties": {}})
async def _stats(ctx: ToolContext) -> Any:
    counts: dict[str, int] = {}
    accounts = ctx.db.list()
    for a in accounts:
        counts[a.status] = counts.get(a.status, 0) + 1
    return {"total": len(accounts), "by_status": counts}


@tool("read_logs", "读取审计日志", "read",
      {"properties": {"account_id": _prop(type="integer"),
                      "limit": _prop(type="integer", default=50, maximum=500)}})
async def _read_logs(ctx: ToolContext, account_id: int | None = None, limit: int = 50) -> Any:
    return ctx.db.logs(account_id, min(limit, 500))


@tool("health_check", "对一个或多个账号做健康检查（仅读取远端状态）", "read",
      {"properties": {"account_ids": _prop(type="array", items={"type": "integer"},
                                           description="为空则检查全部"),
                      "tag": _prop(type="string"),
                      "concurrency": _prop(type="integer", default=3, maximum=10)}})
async def _health_check(ctx: ToolContext, account_ids: list[int] | None = None,
                        tag: str | None = None, concurrency: int = 3) -> Any:
    ids = account_ids or [a.id for a in ctx.db.list(tag=tag) if a.id]
    if ctx.dry_run:
        return {"dry_run": True, "would_check": ids}
    return await ctx.mgr.run_batch(ids, ctx.mgr.health_check,
                                   concurrency=min(concurrency, 10), stagger=False)


@tool("list_dialogs", "列出某账号的会话", "read",
      {"properties": {"account_id": ID, "limit": _prop(type="integer", default=30, maximum=200)},
       "required": ["account_id"]})
async def _list_dialogs(ctx: ToolContext, account_id: int, limit: int = 30) -> Any:
    if ctx.dry_run:
        return {"dry_run": True}
    return await ctx.mgr.get_dialogs(account_id, min(limit, 200))


@tool("list_devices", "列出某账号已授权的登录设备", "read",
      {"properties": {"account_id": ID}, "required": ["account_id"]})
async def _list_devices(ctx: ToolContext, account_id: int) -> Any:
    if ctx.dry_run:
        return {"dry_run": True}
    return await ctx.mgr.list_sessions(account_id)


# ---------------- 写入工具 ----------------
@tool("add_account", "新增账号条目（不含登录）", "write",
      {"properties": {"label": _prop(type="string"), "phone": _prop(type="string"),
                      "proxy": _prop(type="string"),
                      "tags": _prop(type="array", items={"type": "string"})},
       "required": ["label"]})
async def _add_account(ctx: ToolContext, label: str, phone: str | None = None,
                       proxy: str | None = None, tags: list[str] | None = None) -> Any:
    if ctx.db.get_by_label(label):
        raise ToolError("conflict", f"别名 {label} 已存在")
    if ctx.dry_run:
        return {"dry_run": True, "would_add": label}
    return ctx.db.add_account(
        Account(label=label, phone=phone, proxy=proxy, tags=tags or [])
    ).public()


@tool("update_account", "修改账号元信息（代理/标签/设备指纹等）", "write",
      {"properties": {"account_id": ID,
                      "fields": _prop(type="object", description="可改：label/phone/proxy/tags/device_model/app_version/system_version/lang_code")},
       "required": ["account_id", "fields"]})
async def _update_account(ctx: ToolContext, account_id: int, fields: dict[str, Any]) -> Any:
    allowed = {"label", "phone", "proxy", "code_url", "tags", "device_model",
               "app_version", "system_version", "lang_code"}
    patch = {k: v for k, v in fields.items() if k in allowed}
    if not patch:
        raise ToolError("bad_request", f"没有可修改字段，允许：{sorted(allowed)}")
    if ctx.dry_run:
        return {"dry_run": True, "would_update": patch}
    ctx.db.update(account_id, **patch)
    acc = ctx.db.get(account_id)
    return acc.public() if acc else None


@tool("import_accounts", "批量导入 `手机号|取码链接` 格式的初始账号清单（幂等）", "write",
      {"properties": {"text": _prop(type="string", description="每行一个，如 +10000000000|https://tgapi.example.com/@u/<uuid>/GetHTML"),
                      "tags": _prop(type="array", items={"type": "string"}),
                      "proxy": _prop(type="string", description="统一代理（可选）")},
       "required": ["text"]})
async def _import_accounts(ctx: ToolContext, text: str, tags: list[str] | None = None,
                           proxy: str | None = None) -> Any:
    from .importer import import_accounts as _imp

    return _imp(ctx.db, text, tags=tags or [], proxy=proxy, dry_run=ctx.dry_run)


@tool("preview_import", "只解析不写入，检查导入清单格式是否可识别", "read",
      {"properties": {"text": _prop(type="string")}, "required": ["text"]})
async def _preview_import(ctx: ToolContext, text: str) -> Any:
    from .importer import parse_text

    items, bad = parse_text(text)
    return {"parsed": [i.to_dict() for i in items], "errors": bad}


@tool("auto_login", "自动登录：发验证码后从账号的取码链接轮询新码并完成登录", "destructive",
      {"properties": {"account_id": ID,
                      "password": _prop(type="string", description="两步验证密码（如有）"),
                      "timeout": _prop(type="number", default=120),
                      "confirm": CONFIRM},
       "required": ["account_id"]})
async def _auto_login(ctx: ToolContext, account_id: int, password: str | None = None,
                      timeout: float = 120, confirm: bool = False) -> Any:
    acc = ctx.db.get(account_id)
    if acc is None:
        raise ToolError("not_found", f"账号 {account_id} 不存在")
    if not acc.code_url:
        raise ToolError("bad_request", "该账号未配置取码链接 code_url")
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"account_id": account_id, "phone": acc.phone}}
    return await ctx.mgr.auto_login(account_id, password=password, timeout=timeout)


@tool("send_message", "以指定账号发送消息（受白名单与限速约束）", "destructive",
      {"properties": {"account_id": ID,
                      "peer": _prop(type="string", description="@username / 手机号 / chat id"),
                      "text": _prop(type="string", maxLength=4096,
                                    description="支持 {你好|在吗} 形式的 spintax 变体"),
                      "spintax": _prop(type="boolean", default=True),
                      "confirm": CONFIRM},
       "required": ["account_id", "peer", "text"]})
async def _send_message(ctx: ToolContext, account_id: int, peer: str, text: str,
                        spintax: bool = True, confirm: bool = False) -> Any:
    ctx.check_peer(peer)
    if ctx.dry_run or not confirm:
        preview: dict[str, Any] = {"account_id": account_id, "peer": peer, "text": text}
        if spintax:
            from .spintax import validate

            preview["spintax"] = validate(text)
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": preview}
    return await ctx.mgr.send_message(account_id, peer, text, spintax=spintax)


@tool("update_profile", "修改账号资料（对外可见）", "destructive",
      {"properties": {"account_id": ID, "first_name": _prop(type="string"),
                      "last_name": _prop(type="string"), "about": _prop(type="string"),
                      "confirm": CONFIRM},
       "required": ["account_id"]})
async def _update_profile(ctx: ToolContext, account_id: int, first_name: str | None = None,
                          last_name: str | None = None, about: str | None = None,
                          confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required"}
    return await ctx.mgr.update_profile(account_id, first_name, last_name, about)


@tool("terminate_other_devices", "踢掉该账号除本会话外的所有登录", "destructive",
      {"properties": {"account_id": ID, "confirm": CONFIRM}, "required": ["account_id"]})
async def _terminate(ctx: ToolContext, account_id: int, confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required"}
    return await ctx.mgr.terminate_other_sessions(account_id)


@tool("autokick_status", "查看登录满 N 小时自动踢出其它设备的开关、周期与待处理数量", "read",
      {"properties": {}, "required": []})
async def _autokick_status(ctx: ToolContext) -> Any:
    from .autokick import status

    return status(ctx.mgr)


@tool("autokick_run", "立即对到期账号执行一次自动清设备", "destructive",
      {"properties": {"confirm": CONFIRM}, "required": []})
async def _autokick_run(ctx: ToolContext, confirm: bool = False) -> Any:
    from .autokick import due_ids, run_once

    if ctx.dry_run or not confirm:
        return {"executed": False,
                "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": due_ids(ctx.db.list(), ctx.s.auto_kick_hours)}
    return await run_once(ctx.mgr)


@tool("delete_account", "从本地库删除账号条目（不会注销 Telegram 账号）", "destructive",
      {"properties": {"account_id": ID, "confirm": CONFIRM}, "required": ["account_id"]})
async def _delete_account(ctx: ToolContext, account_id: int, confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required"}
    ctx.db.delete(account_id)
    return {"ok": True, "deleted": account_id}


@tool("spintax_preview", "校验 {a|b} 文案并预览变体（不发送）", "read",
      {"properties": {"text": _prop(type="string")}, "required": ["text"]})
async def _spintax_preview(ctx: ToolContext, text: str) -> Any:
    from .spintax import validate

    return validate(text)


@tool("healthy_accounts", "列出可用于批量动作的健康账号（active 且不在 spam 封锁期）", "read",
      {"properties": {"tag": _prop(type="string")}})
async def _healthy_accounts(ctx: ToolContext, tag: str | None = None) -> Any:
    return {"ids": ctx.mgr.healthy_ids(None, tag)}


@tool("proxy_audit", "代理体检：逐账号测连通性与出口 IP 去重（只读）", "read",
      {"properties": {"concurrency": _prop(type="integer", default=10, maximum=50)}})
async def _proxy_audit(ctx: ToolContext, concurrency: int = 10) -> Any:
    from .proxycheck import check_accounts

    if ctx.dry_run:
        return {"dry_run": True}
    return await check_accounts(ctx.db, ctx.s.default_proxy,
                                concurrency=min(concurrency, 50))


@tool("spam_check", "与 @SpamBot 对话判定账号是否被限制（会发送 /start）", "write",
      {"properties": {"account_id": ID}, "required": ["account_id"]})
async def _spam_check(ctx: ToolContext, account_id: int) -> Any:
    if ctx.dry_run:
        return {"dry_run": True, "would_check": account_id}
    return await ctx.mgr.check_spam_status(account_id)


@tool("warmup", "养号：保活在线 / 随机已读 / 账号之间互聊", "destructive",
      {"properties": {"account_ids": _prop(type="array", items={"type": "integer"}),
                      "tag": _prop(type="string"),
                      "online": _prop(type="boolean", default=True),
                      "read": _prop(type="boolean", default=True),
                      "chat": _prop(type="boolean", default=True),
                      "rounds": _prop(type="integer", default=1, maximum=5),
                      "concurrency": _prop(type="integer", default=3, maximum=10),
                      "confirm": CONFIRM}})
async def _warmup(ctx: ToolContext, account_ids: list[int] | None = None,
                  tag: str | None = None, online: bool = True, read: bool = True,
                  chat: bool = True, rounds: int = 1, concurrency: int = 3,
                  confirm: bool = False) -> Any:
    from .warmup import warmup as _run

    ids = ctx.mgr.healthy_ids(account_ids, tag)
    if ctx.dry_run or not confirm:
        return {"executed": False,
                "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"accounts": ids, "online": online, "read": read,
                            "chat": chat, "rounds": rounds}}
    if not ids:
        raise ToolError("bad_request", "没有可用的健康账号")
    return await _run(ctx.mgr, ids, online=online, read=read, chat=chat,
                      rounds=rounds, concurrency=min(concurrency, 10))


@tool("list_tasks", "列出最近的任务及进度（群发 / 采集发言人）", "read",
      {"properties": {"limit": _prop(type="integer", default=20, maximum=100),
                      "status": _prop(type="string")}})
async def _list_tasks(ctx: ToolContext, limit: int = 20, status: str | None = None) -> Any:
    from .tasks import TaskStore

    return [t.public() for t in TaskStore(ctx.db.conn).list(limit=min(limit, 100),
                                                            status=status)]


@tool("get_task", "查看单个任务的逐目标结果与失败原因", "read",
      {"properties": {"task_id": _prop(type="integer"),
                      "target_status": _prop(type="string"),
                      "limit": _prop(type="integer", default=200, maximum=1000)},
       "required": ["task_id"]})
async def _get_task(ctx: ToolContext, task_id: int, target_status: str | None = None,
                    limit: int = 200) -> Any:
    from .tasks import TaskStore

    store = TaskStore(ctx.db.conn)
    t = store.get(task_id)
    if t is None:
        raise ToolError("not_found", f"任务 {task_id} 不存在")
    d = t.public()
    d["targets"] = store.targets(task_id, status=target_status, limit=min(limit, 1000))
    return d


@tool("list_leads", "查线索库（采集到的活跃发言人）", "read",
      {"properties": {"source": _prop(type="string"),
                      "days": _prop(type="number"),
                      "has_username": _prop(type="boolean", default=False),
                      "limit": _prop(type="integer", default=200, maximum=1000)}})
async def _list_leads(ctx: ToolContext, source: str | None = None, days: float | None = None,
                      has_username: bool = False, limit: int = 200) -> Any:
    import time as _t

    from .leads import LeadStore

    since = (_t.time() - days * 86400) if days else None
    rows = LeadStore(ctx.db.conn).list(source=source, since=since,
                                       has_username=has_username, limit=min(limit, 1000))
    return {"count": len(rows), "items": rows}


@tool("lead_sources", "线索来源统计（每个群采集到多少人）", "read",
      {"properties": {}})
async def _lead_sources(ctx: ToolContext) -> Any:
    from .leads import LeadStore

    return LeadStore(ctx.db.conn).sources()


@tool("stop_task", "停止正在运行的任务（剩余目标标为已跳过）", "destructive",
      {"properties": {"task_id": _prop(type="integer"), "confirm": CONFIRM},
       "required": ["task_id"]})
async def _stop_task(ctx: ToolContext, task_id: int, confirm: bool = False) -> Any:
    from .tasks import TaskStore

    if ctx.dry_run or not confirm:
        return {"executed": False,
                "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "task_id": task_id}
    return {"ok": TaskStore(ctx.db.conn).request_stop(task_id)}



@tool("block_user", "用指定托管账号屏蔽一个用户（@用户名 / 用户ID / 手机号）", "destructive",
      {"properties": {
          "account_id": ID,
          "target": _prop(type="string", description="@username / 用户ID / 手机号"),
          "confirm": CONFIRM,
      }, "required": ["account_id", "target"]})
async def _block_user(ctx: ToolContext, account_id: int, target: str,
                      confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False,
                "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"account_id": account_id, "target": target}}
    from telethon.tl.functions.contacts import BlockRequest

    async with ctx.mgr.session(account_id) as client:
        entity = await client.get_entity(target)
        await client(BlockRequest(id=entity))
        return {
            "ok": True,
            "account_id": account_id,
            "target": target,
            "user_id": getattr(entity, "id", None),
            "username": getattr(entity, "username", None),
        }


@tool("unblock_user", "用指定托管账号取消屏蔽一个用户", "write",
      {"properties": {
          "account_id": ID,
          "target": _prop(type="string", description="@username / 用户ID / 手机号"),
          "confirm": CONFIRM,
      }, "required": ["account_id", "target"]})
async def _unblock_user(ctx: ToolContext, account_id: int, target: str,
                        confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False,
                "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"account_id": account_id, "target": target}}
    from telethon.tl.functions.contacts import UnblockRequest

    async with ctx.mgr.session(account_id) as client:
        entity = await client.get_entity(target)
        await client(UnblockRequest(id=entity))
        return {
            "ok": True,
            "account_id": account_id,
            "target": target,
            "user_id": getattr(entity, "id", None),
            "username": getattr(entity, "username", None),
        }



# ---------------- ZIP / 号包工具（对齐网页 ZIP 工具箱，路径须在 data 目录内） ----------------

def _data_root(ctx: ToolContext) -> Path:
    return Path(ctx.settings.data_dir).resolve()


def _safe_under_data(ctx: ToolContext, path: str) -> Path:
    root = _data_root(ctx)
    raw = (path or "").strip()
    if not raw:
        raise ToolError("bad_request", "路径不能为空")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (root / raw).resolve()
    else:
        p = p.resolve()
    try:
        p.relative_to(root)
    except ValueError as exc:
        raise ToolError("forbidden", f"路径必须位于 data 目录内：{root}") from exc
    return p


def _unpack_root(ctx: ToolContext) -> Path:
    return (_data_root(ctx) / "unpack").resolve()


@tool("zip_list_jobs", "列出 data/unpack 下的 ZIP 作业目录与主要文件", "read",
      {"properties": {"limit": _prop(type="integer", default=30, maximum=100)}})
async def _zip_list_jobs(ctx: ToolContext, limit: int = 30) -> Any:
    root = _unpack_root(ctx)
    if not root.is_dir():
        return {"jobs": [], "unpack_dir": str(root)}
    jobs = []
    for d in sorted(root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        files = [f.name for f in d.iterdir() if f.is_file()][:30]
        jobs.append({
            "job": d.name,
            "files": files,
            "file_count": sum(1 for f in d.iterdir() if f.is_file()),
            "mtime": int(d.stat().st_mtime),
        })
        if len(jobs) >= max(1, min(limit, 100)):
            break
    return {"unpack_dir": str(root), "jobs": jobs}


@tool("zip_job_files", "列出某个 ZIP 作业目录内的文件（可下载路径供人工用）", "read",
      {"properties": {"job": _prop(type="string", description="作业 ID（unpack 下目录名）")},
       "required": ["job"]})
async def _zip_job_files(ctx: ToolContext, job: str) -> Any:
    import re
    if not re.fullmatch(r"[0-9a-f]{8,32}|cvt_[\w]+|job_[\w]+", job or ""):
        # 允许常见前缀，仍限制在 unpack 下
        if ".." in (job or "") or "/" in (job or "") or "\\" in (job or ""):
            raise ToolError("bad_request", "job 不合法")
    d = _unpack_root(ctx) / job
    if not d.is_dir():
        raise ToolError("not_found", f"作业不存在：{job}")
    files = []
    for f in sorted(d.rglob("*")):
        if f.is_file():
            rel = str(f.relative_to(d))
            files.append({
                "path": rel,
                "size": f.stat().st_size,
                "download_url": f"/api/tools/unpack/{job}/{Path(rel).name}" if "/" not in rel else None,
            })
    return {"job": job, "dir": str(d), "files": files[:200]}


@tool("zip_analyze", "分析 data 目录内某个号包 ZIP（只读，不拆包）", "read",
      {"properties": {
          "path": _prop(type="string", description="相对于 data 或绝对路径（须在 data 内）"),
      }, "required": ["path"]})
async def _zip_analyze(ctx: ToolContext, path: str) -> Any:
    import asyncio
    from .gaf.core import chaibao as core

    zp = _safe_under_data(ctx, path)
    if not zp.is_file():
        raise ToolError("not_found", f"文件不存在：{zp}")
    try:
        info = await asyncio.to_thread(core.analyze, str(zp))
    except Exception as exc:  # noqa: BLE001
        raise ToolError("unpack_error", f"{type(exc).__name__}: {exc}") from exc
    return {"ok": True, "path": str(zp), **info}


@tool("zip_unpack", "拆分号包 ZIP：按 fmt（如 -9- 或 5,5,5）拆成多个小包", "write",
      {"properties": {
          "path": _prop(type="string", description="源 ZIP，须在 data 目录内"),
          "fmt": _prop(type="string", description="拆分格式，如 -9- 或 5,5,5", default="-9-"),
          "workers": _prop(type="integer", description="并发，可选"),
          "confirm": CONFIRM,
      }, "required": ["path"]})
async def _zip_unpack(ctx: ToolContext, path: str, fmt: str = "-9-",
                      workers: int | None = None, confirm: bool = False) -> Any:
    import asyncio
    import uuid
    from .gaf.core import chaibao as core

    if ctx.dry_run or not confirm:
        return {"executed": False,
                "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"path": path, "fmt": fmt}}
    zp = _safe_under_data(ctx, path)
    if not zp.is_file():
        raise ToolError("not_found", f"文件不存在：{zp}")
    job = uuid.uuid4().hex[:16]
    out = _unpack_root(ctx) / job
    out.mkdir(parents=True, exist_ok=True)
    try:
        result = await asyncio.to_thread(
            core.unpack, str(zp), str(out), fmt or "-9-", None, "pack")
    except Exception as exc:  # noqa: BLE001
        raise ToolError("unpack_error", f"{type(exc).__name__}: {exc}") from exc
    packs = []
    for pk in result.get("packs") or []:
        packs.append({
            "filename": pk.get("filename"),
            "size": pk.get("size"),
            "url": f"/api/tools/unpack/{job}/{pk.get('filename')}",
        })
    ctx.db.log(None, "tools.unpack", True,
               f"ai 拆包 {result.get('total')} -> {result.get('pack_count')}")
    return {"ok": True, "job": job, "total": result.get("total"),
            "pack_count": result.get("pack_count"), "packs": packs}


@tool("zip_merge", "合并作业目录 data/unpack/{job}/src 下已上传的多个 src*.zip", "write",
      {"properties": {
          "job": _prop(type="string", description="已通过网页上传过分包的 merge 作业 ID"),
          "workers": _prop(type="integer"),
          "confirm": CONFIRM,
      }, "required": ["job"]})
async def _zip_merge(ctx: ToolContext, job: str, workers: int | None = None,
                     confirm: bool = False) -> Any:
    import asyncio
    import shutil
    from .gaf.core import zhenghe as core

    if ctx.dry_run or not confirm:
        return {"executed": False,
                "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"job": job}}
    if ".." in job or "/" in job or "\\" in job:
        raise ToolError("bad_request", "job 不合法")
    d = _unpack_root(ctx) / job
    src = d / "src"
    parts = sorted(str(p) for p in src.glob("src*.zip")) if src.is_dir() else []
    if not parts:
        raise ToolError("bad_request", "作业里没有 src*.zip，请先在网页合并工具上传分包")
    out = d / "merged.zip"
    try:
        result = await asyncio.to_thread(core.merge, parts, str(out), None, workers)
    except Exception as exc:  # noqa: BLE001
        raise ToolError("merge_error", f"{type(exc).__name__}: {exc}") from exc
    finally:
        if src.is_dir():
            shutil.rmtree(src, ignore_errors=True)
    result = dict(result)
    result.pop("out", None)
    ctx.db.log(None, "tools.merge", True, f"ai merge job={job}")
    return {"ok": True, "job": job, "url": f"/api/tools/unpack/{job}/merged.zip", **result}


@tool("zip_regtime", "按注册时间给号包 ZIP 分类（需已配置注册时间接口或离线模式）", "write",
      {"properties": {
          "path": _prop(type="string", description="源号包 ZIP，data 目录内"),
          "workers": _prop(type="integer"),
          "confirm": CONFIRM,
      }, "required": ["path"]})
async def _zip_regtime(ctx: ToolContext, path: str, workers: int | None = None,
                       confirm: bool = False) -> Any:
    import asyncio
    import uuid
    from .gaf.core import shaireg as core

    if ctx.dry_run or not confirm:
        return {"executed": False,
                "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"path": path}}
    zp = _safe_under_data(ctx, path)
    if not zp.is_file():
        raise ToolError("not_found", f"文件不存在：{zp}")
    job = uuid.uuid4().hex[:16]
    d = _unpack_root(ctx) / job
    d.mkdir(parents=True, exist_ok=True)
    out = d / "regtime.zip"
    try:
        result = await asyncio.to_thread(core.regtime, str(zp), str(out), workers)
    except Exception as exc:  # noqa: BLE001
        raise ToolError("regtime_error", f"{type(exc).__name__}: {exc}") from exc
    result = dict(result) if isinstance(result, dict) else {"result": result}
    result.pop("out", None)
    ctx.db.log(None, "tools.regtime", True, f"ai regtime job={job}")
    return {"ok": True, "job": job, "url": f"/api/tools/unpack/{job}/regtime.zip", **result}


@tool("zip_cleanup", "删除 data/unpack 下的某个作业目录", "destructive",
      {"properties": {
          "job": _prop(type="string"),
          "confirm": CONFIRM,
      }, "required": ["job"]})
async def _zip_cleanup(ctx: ToolContext, job: str, confirm: bool = False) -> Any:
    import shutil
    if ctx.dry_run or not confirm:
        return {"executed": False,
                "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"job": job}}
    if ".." in (job or "") or "/" in (job or "") or "\\" in (job or ""):
        raise ToolError("bad_request", "job 不合法")
    d = _unpack_root(ctx) / job
    if not d.is_dir():
        raise ToolError("not_found", f"作业不存在：{job}")
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True, "removed": job}


@tool("zip_convert", "格式互转：session ZIP ↔ tdata ZIP（文件须在 data 内）", "write",
      {"properties": {
          "path": _prop(type="string", description="源 ZIP"),
          "mode": _prop(type="string", description="session_to_tdata | tdata_to_session",
                        default="session_to_tdata"),
          "confirm": CONFIRM,
      }, "required": ["path"]})
async def _zip_convert(ctx: ToolContext, path: str, mode: str = "session_to_tdata",
                       confirm: bool = False) -> Any:
    import uuid
    if ctx.dry_run or not confirm:
        return {"executed": False,
                "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"path": path, "mode": mode}}
    zp = _safe_under_data(ctx, path)
    if not zp.is_file():
        raise ToolError("not_found", f"文件不存在：{zp}")
    mode = (mode or "session_to_tdata").strip().lower()
    job = uuid.uuid4().hex[:16]
    d = _unpack_root(ctx) / job
    d.mkdir(parents=True, exist_ok=True)
    raw = zp.read_bytes()
    api_id = int(getattr(ctx.settings, "api_id", 0) or 0)
    api_hash = str(getattr(ctx.settings, "api_hash", "") or "")
    try:
        from .convert_tool import session_zip_to_tdata_zip, tdata_zip_to_session_zip
        if mode in ("session_to_tdata", "s2t", "to_tdata"):
            out_bytes, meta = await session_zip_to_tdata_zip(
                raw, api_id=api_id, api_hash=api_hash)
            out_name = "tdata.zip"
        elif mode in ("tdata_to_session", "t2s", "to_session"):
            out_bytes, meta = await tdata_zip_to_session_zip(
                raw, api_id=api_id, api_hash=api_hash)
            out_name = "session.zip"
        else:
            raise ToolError("bad_request", f"不支持的 mode：{mode}")
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ToolError("convert_error", f"{type(exc).__name__}: {exc}") from exc
    (d / out_name).write_bytes(out_bytes)
    meta = meta if isinstance(meta, dict) else {}
    return {
        "ok": True, "job": job, "mode": mode,
        "url": f"/api/tools/unpack/{job}/{out_name}",
        **{k: v for k, v in meta.items() if k not in ("out", "path")},
    }



@tool("search_public", "搜索公开用户/群/频道（关键词）", "read",
      {"properties": {
          "account_id": ID,
          "query": _prop(type="string", description="搜索关键词"),
          "limit": _prop(type="integer", default=20, maximum=50),
      }, "required": ["account_id", "query"]})
async def _search_public(ctx: ToolContext, account_id: int, query: str,
                         limit: int = 20) -> Any:
    from .toolbox import op_search_public
    async with ctx.mgr.session(account_id) as client:
        return await op_search_public(client, {"query": query, "limit": limit})


@tool("join_chat", "加入公开群/频道或邀请链接", "destructive",
      {"properties": {
          "account_id": ID,
          "target": _prop(type="string", description="@username 或 https://t.me/+xxx"),
          "confirm": CONFIRM,
      }, "required": ["account_id", "target"]})
async def _join_chat(ctx: ToolContext, account_id: int, target: str,
                     confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False,
                "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"account_id": account_id, "target": target}}
    from .toolbox import op_join_chat
    async with ctx.mgr.session(account_id) as client:
        return await op_join_chat(client, {"target": target})


@tool("list_members", "浏览群/频道成员列表", "read",
      {"properties": {
          "account_id": ID,
          "target": _prop(type="string", description="群 @username 或 chat id"),
          "limit": _prop(type="integer", default=100, maximum=500),
      }, "required": ["account_id", "target"]})
async def _list_members(ctx: ToolContext, account_id: int, target: str,
                        limit: int = 100) -> Any:
    from .toolbox import op_list_members
    async with ctx.mgr.session(account_id) as client:
        return await op_list_members(client, {"target": target, "limit": limit})


@tool("read_chat_messages", "读取群/频道/会话最近消息内容", "read",
      {"properties": {
          "account_id": ID,
          "target": _prop(type="string", description="@username / chat id"),
          "limit": _prop(type="integer", default=30, maximum=200),
          "search": _prop(type="string", description="可选关键词过滤"),
      }, "required": ["account_id", "target"]})
async def _read_chat_messages(ctx: ToolContext, account_id: int, target: str,
                              limit: int = 30, search: str | None = None) -> Any:
    from .toolbox import op_read_messages
    async with ctx.mgr.session(account_id) as client:
        return await op_read_messages(client, {
            "target": target, "limit": limit, "search": search or "",
        })



@tool("delete_messages", "删除/撤回消息（revoke 默认尽量双向）", "destructive",
      {"properties": {
          "account_id": ID,
          "target": _prop(type="string"),
          "message_ids": _prop(type="array", items={"type": "integer"}),
          "revoke": _prop(type="boolean", default=True),
          "confirm": CONFIRM,
      }, "required": ["account_id", "target", "message_ids"]})
async def _delete_messages(ctx: ToolContext, account_id: int, target: str,
                           message_ids: list[int], revoke: bool = True,
                           confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"target": target, "message_ids": message_ids, "revoke": revoke}}
    from .toolbox import op_delete_messages
    async with ctx.mgr.session(account_id) as client:
        return await op_delete_messages(client, {
            "target": target, "message_ids": message_ids, "revoke": revoke,
        })


@tool("edit_message", "编辑本账号已发送的消息", "destructive",
      {"properties": {
          "account_id": ID,
          "target": _prop(type="string"),
          "message_id": _prop(type="integer"),
          "text": _prop(type="string"),
          "confirm": CONFIRM,
      }, "required": ["account_id", "target", "message_id", "text"]})
async def _edit_message(ctx: ToolContext, account_id: int, target: str,
                        message_id: int, text: str, confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"target": target, "message_id": message_id, "text": text[:200]}}
    from .toolbox import op_edit_message
    async with ctx.mgr.session(account_id) as client:
        return await op_edit_message(client, {
            "target": target, "message_id": message_id, "text": text,
        })


@tool("forward_messages", "转发消息到另一会话", "destructive",
      {"properties": {
          "account_id": ID,
          "from_peer": _prop(type="string"),
          "to_peer": _prop(type="string"),
          "message_ids": _prop(type="array", items={"type": "integer"}),
          "confirm": CONFIRM,
      }, "required": ["account_id", "from_peer", "to_peer", "message_ids"]})
async def _forward_messages(ctx: ToolContext, account_id: int, from_peer: str,
                            to_peer: str, message_ids: list[int],
                            confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"from_peer": from_peer, "to_peer": to_peer, "message_ids": message_ids}}
    from .toolbox import op_forward_messages
    async with ctx.mgr.session(account_id) as client:
        return await op_forward_messages(client, {
            "from_peer": from_peer, "to_peer": to_peer, "message_ids": message_ids,
        })


@tool("reply_message", "回复/引用指定消息", "destructive",
      {"properties": {
          "account_id": ID,
          "target": _prop(type="string"),
          "message_id": _prop(type="integer"),
          "text": _prop(type="string"),
          "confirm": CONFIRM,
      }, "required": ["account_id", "target", "message_id", "text"]})
async def _reply_message(ctx: ToolContext, account_id: int, target: str,
                         message_id: int, text: str, confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"target": target, "message_id": message_id, "text": text[:200]}}
    from .toolbox import op_reply_message
    async with ctx.mgr.session(account_id) as client:
        return await op_reply_message(client, {
            "target": target, "message_id": message_id, "text": text,
        })


@tool("mark_read", "标记会话已读到指定消息", "write",
      {"properties": {
          "account_id": ID,
          "target": _prop(type="string"),
          "message_id": _prop(type="integer", description="max_id"),
          "confirm": CONFIRM,
      }, "required": ["account_id", "target", "message_id"]})
async def _mark_read(ctx: ToolContext, account_id: int, target: str,
                     message_id: int, confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"target": target, "message_id": message_id}}
    from .toolbox import op_read_message
    async with ctx.mgr.session(account_id) as client:
        return await op_read_message(client, {"target": target, "message_id": message_id})


@tool("get_message", "按 ID 读取单条消息内容", "read",
      {"properties": {
          "account_id": ID,
          "target": _prop(type="string"),
          "message_id": _prop(type="integer"),
      }, "required": ["account_id", "target", "message_id"]})
async def _get_message(ctx: ToolContext, account_id: int, target: str,
                       message_id: int) -> Any:
    from .toolbox import op_get_message
    async with ctx.mgr.session(account_id) as client:
        return await op_get_message(client, {"target": target, "message_id": message_id})


@tool("create_group", "创建群聊（默认超级群）", "destructive",
      {"properties": {
          "account_id": ID,
          "title": _prop(type="string"),
          "about": _prop(type="string"),
          "megagroup": _prop(type="boolean", default=True),
          "users": _prop(type="array", items={"type": "string"}, description="可选初始成员"),
          "confirm": CONFIRM,
      }, "required": ["account_id", "title"]})
async def _create_group(ctx: ToolContext, account_id: int, title: str,
                        about: str = "", megagroup: bool = True,
                        users: list[str] | None = None, confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"title": title, "megagroup": megagroup, "users": users or []}}
    from .toolbox import op_create_group
    async with ctx.mgr.session(account_id) as client:
        return await op_create_group(client, {
            "title": title, "about": about, "megagroup": megagroup,
            "users": users or [],
        })


@tool("leave_chat", "离开群/频道", "destructive",
      {"properties": {
          "account_id": ID,
          "target": _prop(type="string"),
          "confirm": CONFIRM,
      }, "required": ["account_id", "target"]})
async def _leave_chat(ctx: ToolContext, account_id: int, target: str,
                      confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"target": target}}
    from .toolbox import op_leave_chat
    async with ctx.mgr.session(account_id) as client:
        return await op_leave_chat(client, {"target": target})




# ---- 工具箱能力同步到 AI（双注册）+ 新增联系人/媒体/频道/Bot 交互 ----

@tool("twofa_status", "查询账号二步验证状态（是否设密码、是否绑恢复邮箱、是否有重置中）", "read",
      {"properties": {"account_id": ID}, "required": ["account_id"]})
async def _twofa_status(ctx: ToolContext, account_id: int) -> Any:
    from .toolbox import op_twofa_status
    async with ctx.mgr.session(account_id) as client:
        return await op_twofa_status(client, {})


@tool("twofa_set", "设置/修改/移除二步验证密码（有密码时必须传 old）", "destructive",
      {"properties": {
          "account_id": ID,
          "old": _prop(type="string", description="当前二验密码（已有时必填）"),
          "new": _prop(type="string", description="新密码；空字符串=移除二验"),
          "hint": _prop(type="string", description="密码提示"),
          "confirm": CONFIRM,
      }, "required": ["account_id"]})
async def _twofa_set(ctx: ToolContext, account_id: int, old: str | None = None,
                     new: str | None = None, hint: str | None = None,
                     confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False,
                "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"account_id": account_id, "has_old": bool(old), "has_new": bool(new)}}
    from .toolbox import op_twofa
    async with ctx.mgr.session(account_id) as client:
        return await op_twofa(client, {"old": old or "", "new": new or "", "hint": hint or ""})


@tool("twofa_reset", "发起官方二步验证重置流程（需满足 Telegram 冷却条件）", "destructive",
      {"properties": {"account_id": ID, "confirm": CONFIRM}, "required": ["account_id"]})
async def _twofa_reset(ctx: ToolContext, account_id: int, confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required"}
    from .toolbox import op_twofa_reset
    async with ctx.mgr.session(account_id) as client:
        return await op_twofa_reset(client, {})


@tool("twofa_reset_cancel", "取消进行中的二步验证重置", "write",
      {"properties": {"account_id": ID, "confirm": CONFIRM}, "required": ["account_id"]})
async def _twofa_reset_cancel(ctx: ToolContext, account_id: int, confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required"}
    from .toolbox import op_twofa_reset_cancel
    async with ctx.mgr.session(account_id) as client:
        return await op_twofa_reset_cancel(client, {})


@tool("privacy_set", "批量设置隐私（phone/last_seen/invite/avatar → everybody|contacts|nobody）", "destructive",
      {"properties": {
          "account_id": ID,
          "items": _prop(type="object",
                         description='如 {"phone":"nobody","last_seen":"nobody","invite":"contacts","avatar":"contacts"}'),
          "confirm": CONFIRM,
      }, "required": ["account_id"]})
async def _privacy_set(ctx: ToolContext, account_id: int, items: dict | None = None,
                       confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": items or "default"}
    from .toolbox import op_privacy
    async with ctx.mgr.session(account_id) as client:
        return await op_privacy(client, {"items": items or {}})


@tool("logout_session", "退出登录：作废当前 session（不解绑手机、不删 Telegram 账号）", "destructive",
      {"properties": {"account_id": ID, "confirm": CONFIRM}, "required": ["account_id"]})
async def _logout_session(ctx: ToolContext, account_id: int, confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required"}
    from .toolbox import op_logout
    async with ctx.mgr.session(account_id) as client:
        res = await op_logout(client, {"confirm": True})
    # 清本地会话字段
    try:
        ctx.db.update(account_id, session_enc=None, status="unauthorized",
                      status_note="logged_out_via_ai")
    except Exception:
        pass
    return res


@tool("delete_tg_account", "向 Telegram 申请注销账号（不可逆，真正删号）", "destructive",
      {"properties": {
          "account_id": ID,
          "reason": _prop(type="string", default="User requested deletion"),
          "confirm": CONFIRM,
      }, "required": ["account_id"]})
async def _delete_tg_account(ctx: ToolContext, account_id: int,
                             reason: str = "User requested deletion",
                             confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required"}
    from .toolbox import op_delete_tg_account
    async with ctx.mgr.session(account_id) as client:
        res = await op_delete_tg_account(client, {"confirm": True, "reason": reason})
    try:
        ctx.db.update(account_id, session_enc=None, status="banned",
                      status_note="tg_account_deleted")
    except Exception:
        pass
    return res


@tool("alive_check", "筛活：轻量探测号是否仍可用（不产生可见行为）", "read",
      {"properties": {
          "account_id": ID,
          "timeout": _prop(type="number", default=10),
      }, "required": ["account_id"]})
async def _alive_check(ctx: ToolContext, account_id: int, timeout: float = 10) -> Any:
    from .toolbox import op_alive
    async with ctx.mgr.session(account_id) as client:
        return await op_alive(client, {"timeout": timeout})


@tool("okpay_balance", "查询 @Okpay 钱包余额", "read",
      {"properties": {"account_id": ID}, "required": ["account_id"]})
async def _okpay_balance(ctx: ToolContext, account_id: int) -> Any:
    from .toolbox import op_okpay_balance
    async with ctx.mgr.session(account_id) as client:
        return await op_okpay_balance(client, {})


@tool("contacts_clear", "清空通讯录（二手号防关联常用）", "destructive",
      {"properties": {
          "account_id": ID,
          "dry_run": _prop(type="boolean", default=False),
          "confirm": CONFIRM,
      }, "required": ["account_id"]})
async def _contacts_clear(ctx: ToolContext, account_id: int, dry_run: bool = False,
                          confirm: bool = False) -> Any:
    if (ctx.dry_run or not confirm) and not dry_run:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required"}
    from .toolbox import op_contacts_clear
    async with ctx.mgr.session(account_id) as client:
        return await op_contacts_clear(client, {"dry_run": dry_run})


@tool("dialogs_clear", "清理会话列表（默认只退群/频道，不动私聊）", "destructive",
      {"properties": {
          "account_id": ID,
          "include_private": _prop(type="boolean", default=False),
          "limit": _prop(type="integer", default=0, description="0=不限"),
          "confirm": CONFIRM,
      }, "required": ["account_id"]})
async def _dialogs_clear(ctx: ToolContext, account_id: int, include_private: bool = False,
                         limit: int = 0, confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required"}
    from .toolbox import op_dialogs_clear
    async with ctx.mgr.session(account_id) as client:
        return await op_dialogs_clear(client, {
            "include_private": include_private, "limit": limit,
        })


@tool("profile_clear", "防找回清理：删头像/清简介/清用户名", "destructive",
      {"properties": {
          "account_id": ID,
          "photos": _prop(type="boolean", default=True),
          "bio": _prop(type="boolean", default=True),
          "username": _prop(type="boolean", default=False),
          "confirm": CONFIRM,
      }, "required": ["account_id"]})
async def _profile_clear(ctx: ToolContext, account_id: int, photos: bool = True,
                         bio: bool = True, username: bool = False,
                         confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required"}
    from .toolbox import op_profile_clear
    async with ctx.mgr.session(account_id) as client:
        return await op_profile_clear(client, {
            "photos": photos, "bio": bio, "username": username,
        })


@tool("list_contacts", "列出通讯录联系人", "read",
      {"properties": {
          "account_id": ID,
          "limit": _prop(type="integer", default=200, maximum=1000),
      }, "required": ["account_id"]})
async def _list_contacts(ctx: ToolContext, account_id: int, limit: int = 200) -> Any:
    from .toolbox import op_list_contacts
    async with ctx.mgr.session(account_id) as client:
        return await op_list_contacts(client, {"limit": limit})


@tool("add_contact", "添加联系人到通讯录", "destructive",
      {"properties": {
          "account_id": ID,
          "target": _prop(type="string", description="@username / 用户ID / 手机号"),
          "first_name": _prop(type="string", default="Contact"),
          "last_name": _prop(type="string"),
          "phone": _prop(type="string"),
          "confirm": CONFIRM,
      }, "required": ["account_id", "target"]})
async def _add_contact(ctx: ToolContext, account_id: int, target: str,
                       first_name: str = "Contact", last_name: str = "",
                       phone: str = "", confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"target": target}}
    from .toolbox import op_add_contact
    async with ctx.mgr.session(account_id) as client:
        return await op_add_contact(client, {
            "target": target, "first_name": first_name,
            "last_name": last_name, "phone": phone,
        })


@tool("delete_contact", "从通讯录删除联系人", "destructive",
      {"properties": {
          "account_id": ID,
          "target": _prop(type="string"),
          "confirm": CONFIRM,
      }, "required": ["account_id", "target"]})
async def _delete_contact(ctx: ToolContext, account_id: int, target: str,
                          confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required"}
    from .toolbox import op_delete_contact
    async with ctx.mgr.session(account_id) as client:
        return await op_delete_contact(client, {"target": target})


@tool("send_media", "发送图片/视频/文件（path 必须在 data 目录内）", "destructive",
      {"properties": {
          "account_id": ID,
          "peer": _prop(type="string", description="@username / chat id"),
          "path": _prop(type="string", description="相对 data 或 data 内绝对路径"),
          "caption": _prop(type="string"),
          "confirm": CONFIRM,
      }, "required": ["account_id", "peer", "path"]})
async def _send_media(ctx: ToolContext, account_id: int, peer: str, path: str,
                      caption: str = "", confirm: bool = False) -> Any:
    ctx.check_peer(peer)
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"peer": peer, "path": path}}
    safe = _safe_under_data(ctx, path)
    from .toolbox import op_send_media
    async with ctx.mgr.session(account_id) as client:
        return await op_send_media(client, {
            "target": peer, "path": str(safe), "caption": caption,
        })


@tool("download_media", "下载指定消息的媒体到 data/downloads", "read",
      {"properties": {
          "account_id": ID,
          "target": _prop(type="string"),
          "message_id": _prop(type="integer"),
          "out_dir": _prop(type="string", description="可选，须在 data 内"),
      }, "required": ["account_id", "target", "message_id"]})
async def _download_media(ctx: ToolContext, account_id: int, target: str,
                          message_id: int, out_dir: str | None = None) -> Any:
    out = None
    if out_dir:
        out = str(_safe_under_data(ctx, out_dir))
    from .toolbox import op_download_media
    async with ctx.mgr.session(account_id) as client:
        return await op_download_media(client, {
            "target": target, "message_id": message_id, "out_dir": out or "",
        })


@tool("create_channel", "创建广播频道（可选公开用户名）", "destructive",
      {"properties": {
          "account_id": ID,
          "title": _prop(type="string"),
          "about": _prop(type="string"),
          "username": _prop(type="string"),
          "confirm": CONFIRM,
      }, "required": ["account_id", "title"]})
async def _create_channel(ctx: ToolContext, account_id: int, title: str,
                          about: str = "", username: str = "",
                          confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"title": title, "username": username}}
    from .toolbox import op_create_channel
    async with ctx.mgr.session(account_id) as client:
        return await op_create_channel(client, {
            "title": title, "about": about, "username": username,
        })


@tool("set_username", "设置本账号公开用户名（空=清除）", "destructive",
      {"properties": {
          "account_id": ID,
          "username": _prop(type="string"),
          "confirm": CONFIRM,
      }, "required": ["account_id"]})
async def _set_username(ctx: ToolContext, account_id: int, username: str = "",
                        confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"username": username}}
    from .toolbox import op_set_username
    async with ctx.mgr.session(account_id) as client:
        return await op_set_username(client, {"username": username})


@tool("interact_bot", "与任意机器人交互：发消息并等待回复", "destructive",
      {"properties": {
          "account_id": ID,
          "bot": _prop(type="string", description="@BotUsername"),
          "text": _prop(type="string", default="/start"),
          "wait": _prop(type="number", default=5),
          "limit": _prop(type="integer", default=3, maximum=10),
          "confirm": CONFIRM,
      }, "required": ["account_id", "bot"]})
async def _interact_bot(ctx: ToolContext, account_id: int, bot: str,
                        text: str = "/start", wait: float = 5, limit: int = 3,
                        confirm: bool = False) -> Any:
    if ctx.dry_run or not confirm:
        return {"executed": False, "reason": "dry_run" if ctx.dry_run else "confirm_required",
                "preview": {"bot": bot, "text": text}}
    from .toolbox import op_interact_bot
    async with ctx.mgr.session(account_id) as client:
        return await op_interact_bot(client, {
            "bot": bot, "text": text, "wait": wait, "limit": limit,
        })


@tool("check_phones", "筛料：批量检测手机号是否已注册 Telegram", "read",
      {"properties": {
          "account_id": ID,
          "phones": _prop(type="string", description="手机号列表，逗号或换行分隔"),
      }, "required": ["account_id", "phones"]})
async def _check_phones(ctx: ToolContext, account_id: int, phones: str) -> Any:
    from .toolbox import op_check_phones
    async with ctx.mgr.session(account_id) as client:
        return await op_check_phones(client, {"phones": phones})



# 登录类动作涉及验证码与密码，故意不开放给 Agent，只能人工走 CLI/Web。
HUMAN_ONLY = {"login", "sign_in", "import_session", "import_tdata", "logout", "export_session", "qr_login"}


def list_tools(include_danger: bool = True, readonly: bool = False) -> list[dict[str, Any]]:
    """返回可直接喂给 LLM / MCP 的工具清单。"""
    out = []
    for t in _REGISTRY.values():
        if readonly and t["danger"] != "read":
            continue
        item = {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        if include_danger:
            item["danger"] = t["danger"]
        out.append(item)
    return sorted(out, key=lambda x: x["name"])


async def call_tool(ctx: ToolContext, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """统一入口。永远返回 {ok, tool, result|error} 结构，不抛异常。"""
    args = dict(args or {})
    spec = _REGISTRY.get(name)
    if spec is None:
        return {"ok": False, "tool": name,
                "error": {"code": "unknown_tool", "message": f"未知工具：{name}",
                          "available": sorted(_REGISTRY)}}
    if ctx.readonly and spec["danger"] != "read":
        return {"ok": False, "tool": name,
                "error": {"code": "readonly", "message": "当前为只读模式，拒绝写入类工具"}}

    schema = spec["inputSchema"]
    unknown = set(args) - set(schema.get("properties", {}))
    if unknown:
        return {"ok": False, "tool": name,
                "error": {"code": "bad_request", "message": f"未知参数：{sorted(unknown)}"}}
    missing = set(schema.get("required", [])) - set(args)
    if missing:
        return {"ok": False, "tool": name,
                "error": {"code": "bad_request", "message": f"缺少必填参数：{sorted(missing)}"}}

    try:
        result = await spec["fn"](ctx, **args)
        return {"ok": True, "tool": name, "danger": spec["danger"], "result": result}
    except ToolError as exc:
        return {"ok": False, "tool": name, "error": {"code": exc.code, "message": exc.message}}
    except PermissionError as exc:
        return {"ok": False, "tool": name, "error": {"code": "forbidden", "message": str(exc)}}
    except Exception as exc:
        return {"ok": False, "tool": name,
                "error": {"code": "error", "message": f"{type(exc).__name__}: {exc}"}}
