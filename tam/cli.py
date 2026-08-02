"""命令行入口：python -m tam.cli <command>"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .crypto import generate_master_key


def _ctx():
    from .config import Settings
    from .db import Database
    from .manager import AccountManager

    s = Settings.load()
    db = Database(s.db_path)
    return s, db, AccountManager(s, db)


def cmd_init_key(_: argparse.Namespace) -> None:
    print("TAM_MASTER_KEY=" + generate_master_key())
    print("# 将上面一行写入 .env，丢失后已存储的 session 将无法解密。", file=sys.stderr)


def cmd_add(args: argparse.Namespace) -> None:
    from .db import Account

    _, db, _m = _ctx()
    acc = db.add_account(Account(
        label=args.label, phone=args.phone, proxy=args.proxy,
        tags=args.tags.split(",") if args.tags else [],
    ))
    print(json.dumps(acc.public(), ensure_ascii=False, indent=2))


def cmd_list(args: argparse.Namespace) -> None:
    _, db, _m = _ctx()
    rows = db.list(status=args.status, tag=args.tag)
    if args.json:
        print(json.dumps([a.public() for a in rows], ensure_ascii=False, indent=2))
        return
    for a in rows:
        print(f"[{a.id:>3}] {a.label:<16} {a.phone or '-':<16} "
              f"{a.status:<12} {'@' + a.username if a.username else '-'}")
    print(f"共 {len(rows)} 个账号")


def cmd_login(args: argparse.Namespace) -> None:
    async def run() -> None:
        _, _db, mgr = _ctx()
        # 两段式：--send-code 先发码，再带 --code 回来完成。适合脚本/自动化。
        if args.send_code or not args.code:
            res = await mgr.send_code(args.id)
            if args.send_code:
                print(json.dumps({"ok": True, "stage": "code_sent", "result": res},
                                 ensure_ascii=False))
                return
        code = args.code
        if not code:
            if args.non_interactive:
                print(json.dumps({"ok": False, "error": {"code": "code_required",
                      "message": "非交互模式下必须传 --code"}}, ensure_ascii=False))
                sys.exit(2)
            code = input("请输入收到的验证码: ").strip()
        res = await mgr.sign_in(args.id, code)
        if res.get("need_password"):
            pwd = args.password
            if not pwd:
                if args.non_interactive:
                    print(json.dumps({"ok": False, "error": {"code": "password_required",
                          "message": "需要两步验证密码，请传 --password"}}, ensure_ascii=False))
                    sys.exit(2)
                import getpass

                pwd = getpass.getpass("两步验证密码: ")
            res = await mgr.sign_in(args.id, code, pwd)
        print(json.dumps({"ok": True, "result": res}, ensure_ascii=False))

    asyncio.run(run())


def cmd_import_accounts(args: argparse.Namespace) -> None:
    """批量导入 手机号|取码链接 格式。"""
    from .importer import import_accounts

    _, db, _m = _ctx()
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    res = import_accounts(db, text, tags=args.tags.split(",") if args.tags else (),
                          proxy=args.proxy, dry_run=args.dry_run)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if res["errors"]:
        sys.exit(1)


def cmd_auto_login(args: argparse.Namespace) -> None:
    """自动取码登录（单个或批量）。"""
    async def run() -> None:
        _, db, mgr = _ctx()
        ids = [args.id] if args.id else [
            a.id for a in db.list(tag=args.tag) if a.code_url and not a.session_enc
        ]

        async def task(aid: int):
            return await mgr.auto_login(aid, password=args.password, timeout=args.timeout)

        res = await mgr.run_batch(ids, task, concurrency=args.concurrency, stagger=True)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if any(not r.get("ok") for r in res):
            sys.exit(1)

    asyncio.run(run())


def cmd_import(args: argparse.Namespace) -> None:
    async def run() -> None:
        _, _db, mgr = _ctx()
        print(json.dumps(await mgr.import_session(args.id, args.session), ensure_ascii=False))

    asyncio.run(run())


def cmd_import_sessions(args: argparse.Namespace) -> None:
    """批量导入 .session 文件（单个文件或目录）。"""
    async def run() -> None:
        _, _db, mgr = _ctx()
        try:
            items = await mgr.import_session_files(
                args.path,
                label=args.label,
                proxy=args.proxy,
                tags=args.tags.split(",") if args.tags else (),
                scan=args.scan,
            )
        except RuntimeError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            sys.exit(1)
        ok_n = sum(1 for i in items if i.get("ok"))
        print(json.dumps({
            "ok": True, "total": len(items), "succeeded": ok_n,
            "failed": len(items) - ok_n, "items": items,
        }, ensure_ascii=False, indent=2))
        if ok_n == 0:
            sys.exit(1)

    asyncio.run(run())


def cmd_import_session_strings(args: argparse.Namespace) -> None:
    """批量导入 StringSession 文本（文件路径或 - 读 stdin）。"""
    if args.file == "-":
        text = sys.stdin.read()
    else:
        text = Path(args.file).read_text(encoding="utf-8")

    async def run() -> None:
        _, _db, mgr = _ctx()
        try:
            items = await mgr.import_session_strings(
                text,
                label=args.label,
                proxy=args.proxy,
                tags=args.tags.split(",") if args.tags else (),
            )
        except RuntimeError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            sys.exit(1)
        ok_n = sum(1 for i in items if i.get("ok"))
        print(json.dumps({
            "ok": True, "total": len(items), "succeeded": ok_n,
            "failed": len(items) - ok_n, "items": items,
        }, ensure_ascii=False, indent=2))
        if ok_n == 0:
            sys.exit(1)

    asyncio.run(run())


def cmd_doctor(args: argparse.Namespace) -> None:
    """一键体检 + 自动修复。"""
    from .doctor import print_report, run_doctor

    res = run_doctor(fix=args.fix)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print_report(res, args.fix)
    if not res["ok"]:
        sys.exit(1)


def cmd_fix_opentele(args: argparse.Namespace) -> None:
    """修复 opentele 在 Python 3.13 上的导入崩溃（可回滚）。"""
    from . import opentele_patch

    if args.revert:
        res = opentele_patch.revert()
    elif args.status:
        res = opentele_patch.status()
    else:
        res = opentele_patch.apply_patch()
        try:
            from .tdata import ensure_opentele

            ensure_opentele()
            res["import_ok"] = True
        except BaseException as exc:  # noqa: BLE001
            res["import_ok"] = False
            res["import_error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if res.get("ok") is False or res.get("import_ok") is False:
        sys.exit(1)


def cmd_import_tdata(args: argparse.Namespace) -> None:
    """导入 Telegram Desktop 的 tdata 目录（单个或扫描父目录批量）。"""
    from .tdata import find_tdata_dirs

    async def run() -> None:
        _, _db, mgr = _ctx()
        dirs = find_tdata_dirs(args.path) if args.scan else [Path(args.path)]
        if not dirs:
            print(json.dumps({"ok": False, "error": "未找到 tdata 目录"}, ensure_ascii=False))
            sys.exit(1)
        results = []
        for d in dirs:
            try:
                res = await mgr.import_tdata(
                    str(d), label=args.label if len(dirs) == 1 else None,
                    password=args.password, proxy=args.proxy,
                    tags=args.tags.split(",") if args.tags else (),
                    use_desktop_api=not args.own_api,
                )
            except Exception as exc:
                res = [{"ok": False, "path": str(d), "error": str(exc)}]
            results.append({"path": str(d), "accounts": res})
        print(json.dumps(results, ensure_ascii=False, indent=2))
        if any(not a.get("ok") for r in results for a in r["accounts"]):
            sys.exit(1)

    asyncio.run(run())


def cmd_check(args: argparse.Namespace) -> None:
    async def run() -> None:
        _, db, mgr = _ctx()
        ids = [args.id] if args.id else [a.id for a in db.list(tag=args.tag)]
        res = await mgr.run_batch(ids, mgr.health_check, concurrency=args.concurrency, stagger=False)
        print(json.dumps(res, ensure_ascii=False, indent=2))

    asyncio.run(run())


def cmd_send(args: argparse.Namespace) -> None:
    async def run() -> None:
        _, db, mgr = _ctx()
        ids = [args.id] if args.id else [a.id for a in db.list(status="active", tag=args.tag)]

        async def task(aid: int):
            return await mgr.send_message(aid, args.peer, args.text)

        res = await mgr.run_batch(ids, task, concurrency=args.concurrency, stagger=True)
        print(json.dumps(res, ensure_ascii=False, indent=2))

    asyncio.run(run())


def cmd_devices(args: argparse.Namespace) -> None:
    async def run() -> None:
        _, _db, mgr = _ctx()
        print(json.dumps(await mgr.list_sessions(args.id), ensure_ascii=False, indent=2))

    asyncio.run(run())


def cmd_tools(args: argparse.Namespace) -> None:
    from .tools import list_tools

    s, _db, _m = _ctx()
    print(json.dumps(list_tools(readonly=s.readonly), ensure_ascii=False, indent=2))


def cmd_call(args: argparse.Namespace) -> None:
    """统一工具调用，供脚本 / Agent 包装使用。"""
    from .tools import ToolContext, call_tool

    async def run() -> None:
        s, db, mgr = _ctx()
        ctx = ToolContext(s, db, mgr, readonly=args.readonly or None)
        payload = json.loads(args.arguments) if args.arguments else {}
        res = await call_tool(ctx, args.tool, payload)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if not res.get("ok"):
            sys.exit(1)

    asyncio.run(run())


def cmd_mcp(args: argparse.Namespace) -> None:
    from .mcp_server import serve_stdio

    asyncio.run(serve_stdio())


def cmd_serve(args: argparse.Namespace) -> None:
    if not getattr(args, "no_doctor", False):
        from .doctor import print_report, run_doctor

        res = run_doctor(fix=True)  # 启动前自动体检并修复
        if not res["ok"] or res["fixed"]:
            print_report(res, True)
        if not res["ok"]:
            print("  上述问题无法自动修复，服务仍会尝试启动。")

    from .api import serve

    serve(args.host, args.port)


def cmd_bot(args: argparse.Namespace) -> None:
    """只启 Telegram 机器人前端（用户上传号包自助处理）。"""
    from .bot import run as run_bot

    run_bot()


def cmd_run(args: argparse.Namespace) -> None:
    """统一启动：按 TAM_DEPLOY / TAM_FRONTEND 决定本地或服务器、网页或机器人。"""
    from .run import run as run_all

    run_all(
        deploy=args.deploy,
        frontend=args.frontend,
        host=args.host,
        port=args.port,
        skip_doctor=args.no_doctor,
        force=args.force,
        menu=args.menu,
    )


def cmd_setup(args: argparse.Namespace) -> None:
    """Linux/服务器无桌面配置入口。"""
    from .headless_setup import run_headless_setup

    run_headless_setup(args)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="tam", description="Telegram 账号管理器")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-key", help="生成主密钥").set_defaults(func=cmd_init_key)

    a = sub.add_parser("add", help="添加账号")
    a.add_argument("label")
    a.add_argument("--phone")
    a.add_argument("--proxy")
    a.add_argument("--tags")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="列出账号")
    l.add_argument("--status")
    l.add_argument("--tag")
    l.add_argument("--json", action="store_true", help="输出结构化 JSON")
    l.set_defaults(func=cmd_list)

    lg = sub.add_parser("login", help="验证码登录（支持非交互）")
    lg.add_argument("id", type=int)
    lg.add_argument("--send-code", action="store_true", help="仅发送验证码后退出")
    lg.add_argument("--code", help="已收到的验证码")
    lg.add_argument("--password", help="两步验证密码")
    lg.add_argument("--non-interactive", action="store_true", help="禁止任何终端输入")
    lg.set_defaults(func=cmd_login)

    ia = sub.add_parser("import-accounts",
                        help="批量导入 手机号|取码链接 清单（文件路径或 - 读 stdin）")
    ia.add_argument("file")
    ia.add_argument("--tags")
    ia.add_argument("--proxy", help="统一代理；建议导入后逐号改成一号一代理")
    ia.add_argument("--dry-run", action="store_true")
    ia.set_defaults(func=cmd_import_accounts)

    al = sub.add_parser("auto-login", help="自动从取码链接拉验证码并登录")
    al.add_argument("--id", type=int)
    al.add_argument("--tag")
    al.add_argument("--password", help="两步验证密码（如有）")
    al.add_argument("--timeout", type=float, default=120.0)
    al.add_argument("--concurrency", type=int, default=1)
    al.set_defaults(func=cmd_auto_login)

    it = sub.add_parser("import-tdata", help="导入 Telegram Desktop 的 tdata 目录")
    it.add_argument("path", help="tdata 目录，或配合 --scan 传父目录")
    it.add_argument("--scan", action="store_true", help="递归扫描子目录中的多份 tdata")
    it.add_argument("--label", help="自定义别名（单目录时生效）")
    it.add_argument("--password", help="tdata 本地密码 passcode（如有）")
    it.add_argument("--proxy")
    it.add_argument("--tags")
    it.add_argument("--own-api", action="store_true",
                    help="用自己的 TAM_API_ID 而非桌面端官方 API（风控较高，不推荐）")
    it.set_defaults(func=cmd_import_tdata)

    isf = sub.add_parser("import-sessions",
                         help="批量导入 .session 文件（路径可为文件或目录）")
    isf.add_argument("path", help=".session 文件，或含多个 .session 的目录")
    isf.add_argument("--scan", action="store_true", default=True,
                     help="目录时递归扫描（默认开）")
    isf.add_argument("--no-scan", action="store_false", dest="scan",
                     help="目录时只扫当前层")
    isf.add_argument("--label", help="统一别名前缀")
    isf.add_argument("--proxy")
    isf.add_argument("--tags")
    isf.set_defaults(func=cmd_import_sessions)

    iss = sub.add_parser("import-session-strings",
                         help="批量导入 StringSession 文本（文件或 - 读 stdin）")
    iss.add_argument("file", help="文本文件路径，或 - 表示 stdin；一行一个，可用 别名|session")
    iss.add_argument("--label", help="统一别名前缀")
    iss.add_argument("--proxy")
    iss.add_argument("--tags")
    iss.set_defaults(func=cmd_import_session_strings)

    im = sub.add_parser("import", help="给已有账号导入单条 StringSession")
    im.add_argument("id", type=int)
    im.add_argument("session")
    im.set_defaults(func=cmd_import)

    ck = sub.add_parser("check", help="健康检查")
    ck.add_argument("--id", type=int)
    ck.add_argument("--tag")
    ck.add_argument("--concurrency", type=int, default=3)
    ck.set_defaults(func=cmd_check)

    sd = sub.add_parser("send", help="发送消息（单个或批量）")
    sd.add_argument("peer")
    sd.add_argument("text")
    sd.add_argument("--id", type=int)
    sd.add_argument("--tag")
    sd.add_argument("--concurrency", type=int, default=2)
    sd.set_defaults(func=cmd_send)

    dv = sub.add_parser("devices", help="查看账号登录设备")
    dv.add_argument("id", type=int)
    dv.set_defaults(func=cmd_devices)

    tl = sub.add_parser("tools", help="输出 Agent 可用工具清单（JSON Schema）")
    tl.set_defaults(func=cmd_tools)

    cl = sub.add_parser("call", help="调用单个工具，输出结构化 JSON")
    cl.add_argument("tool")
    cl.add_argument("arguments", nargs="?", default="", help='JSON 参数，如 \'{"account_id":1}\'')
    cl.add_argument("--readonly", action="store_true", help="本次调用强制只读")
    cl.set_defaults(func=cmd_call)

    dc = sub.add_parser("doctor", help="一键体检：环境/依赖/配置/密钥/数据库/tdata 全部校验")
    dc.add_argument("--fix", action="store_true", help="发现问题就自动修复")
    dc.add_argument("--json", action="store_true", help="输出 JSON")
    dc.set_defaults(func=cmd_doctor)

    fx = sub.add_parser("fix-opentele",
                        help="修复 opentele 在 Python 3.13 上的导入崩溃（tdata 导入用）")
    fx.add_argument("--status", action="store_true", help="只查看状态，不修改")
    fx.add_argument("--revert", action="store_true", help="还原补丁")
    fx.set_defaults(func=cmd_fix_opentele)

    st = sub.add_parser("setup", help="无桌面配置向导（Linux/systemd/Docker）")
    from .headless_setup import add_setup_arguments

    add_setup_arguments(st)
    st.set_defaults(func=cmd_setup)

    mc = sub.add_parser("mcp", help="以 MCP stdio 服务端运行（供 Claude/Cursor 等接入）")
    mc.set_defaults(func=cmd_mcp)

    sv = sub.add_parser("serve", help="启动 Web 控制台")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8848)
    sv.add_argument("--no-doctor", action="store_true", help="跳过启动前的自动体检")
    sv.set_defaults(func=cmd_serve)

    bt = sub.add_parser("bot", help="启动 Telegram 机器人前端（号包自助工具箱）")
    bt.set_defaults(func=cmd_bot)

    ra = sub.add_parser(
        "run", help="统一启动：本地/服务器 × 网页/机器人（读 TAM_DEPLOY / TAM_FRONTEND）"
    )
    ra.add_argument("--deploy", choices=("local", "server"), help="默认读 TAM_DEPLOY，再默认 local")
    ra.add_argument(
        "--frontend", choices=("web", "bot", "both"), help="默认读 TAM_FRONTEND，再默认 web"
    )
    ra.add_argument("--host", help="不写时：本地=127.0.0.1，服务器=0.0.0.0")
    ra.add_argument("--port", type=int, help="不写时读 TAM_PORT，再默认 8848")
    ra.add_argument("--no-doctor", action="store_true", help="跳过启动前的自动体检")
    ra.add_argument(
        "--force", action="store_true", help="忽略体检的致命错误强行启动（不建议）"
    )
    rg = ra.add_mutually_exclusive_group()
    rg.add_argument("--menu", dest="menu", action="store_true", default=None,
                    help="强制弹启动模式选择菜单")
    rg.add_argument("--no-menu", dest="menu", action="store_false",
                    help="不弹菜单，直接用环境变量/默认值（守护进程用这个）"
    )
    ra.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
