"""统一启动器：一个命令同时决定“部署在哪”和“用哪个前端”。

两个开关（.env 里改，或命令行参数覆盖）：

  TAM_DEPLOY   = local | server
      local ：本机自用。只绑 127.0.0.1，不对外暴露，不强制代理。
      server：服务器。绑 0.0.0.0，强烈建议配代理池 + 反代 HTTPS。

  TAM_FRONTEND = web | bot | both
      web ：只开网页控制台（托管自己的号，登录/踢设备/养号/发信）。
      bot ：只开 Telegram 机器人（用户上传号包自助处理）。
      both：两个一起跑，共用同一份数据目录与 .env。

web-only 时不会 import telegram，所以不装 python-telegram-bot 也能正常跑网页。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from .config import Settings, _load_dotenv

DEPLOYS = ("local", "server")
FRONTENDS = ("web", "bot", "both")


def resolve_mode(deploy: str | None = None, frontend: str | None = None) -> tuple[str, str]:
    """命令行参数 > 环境变量 > 默认值（local + web）。"""
    d = (deploy or os.getenv("TAM_DEPLOY") or "local").strip().lower()
    f = (frontend or os.getenv("TAM_FRONTEND") or "web").strip().lower()
    if d not in DEPLOYS:
        raise ValueError(f"TAM_DEPLOY 只能是 {'/'.join(DEPLOYS)}，得到：{d}")
    if f not in FRONTENDS:
        raise ValueError(f"TAM_FRONTEND 只能是 {'/'.join(FRONTENDS)}，得到：{f}")
    return d, f


def _menu_enabled(menu: bool | None = None) -> bool:
    """什么时候该弹选择菜单。

    显式指定 > TAM_NO_MENU 环境变量 > 是否在交互终端里。
    systemd / docker / nohup 这些场景没有 tty，绝不能卡在 input() 上等输入。
    """
    if menu is not None:
        return menu
    if (os.getenv("TAM_NO_MENU") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:  # noqa: BLE001 - 某些嵌入环境 stdin 被探测时会抛
        return False


def _prompt_choice(title: str, options: list[tuple[str, str]], default: str) -> str:
    """终端里选一项，数字和名字都认，回车用默认。"""
    names = [v for v, _ in options]
    if default not in names:          # 环境变量里的值可能是错的，不能拿它当默认
        default = names[0]
    print(f"\n{title}")
    for i, (val, desc) in enumerate(options, 1):
        mark = "  ← 默认" if val == default else ""
        print(f"    {i}. {val:<6} {desc}{mark}")
    while True:
        try:
            raw = input(f"  请选择 1-{len(options)}（直接回车用 {default}）：").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  未选择，用默认：{default}")
            return default
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        if raw in names:
            return raw
        print(f"    输入无效，请输 1-{len(options)} 或 {'/'.join(names)}")


def choose_mode(deploy: str | None = None, frontend: str | None = None,
                menu: bool | None = None) -> tuple[str, str]:
    """启动时确定跑什么模式。

    命令行已经指定的不再问；两个都指定了就完全不弹菜单，
    方便写进脚本和守护进程。没指定又在终端里，就当面问。
    """
    if deploy and frontend:
        return resolve_mode(deploy, frontend)
    if not _menu_enabled(menu):
        return resolve_mode(deploy, frontend)

    print("\n" + "=" * 52)
    print("  TAM 启动选项")
    print("=" * 52)
    d = deploy or _prompt_choice(
        "  部署在哪里？",
        [("local", "只在本机跑，网页只监听 127.0.0.1"),
         ("server", "放服务器，对外提供访问（记得配令牌和 HTTPS）")],
        (os.getenv("TAM_DEPLOY") or "local").strip().lower(),
    )
    f = frontend or _prompt_choice(
        "  用哪种前端？",
        [("web", "网页控制台"),
         ("bot", "Telegram 机器人"),
         ("both", "两个一起开")],
        (os.getenv("TAM_FRONTEND") or "web").strip().lower(),
    )
    print("=" * 52)
    return resolve_mode(d, f)


def resolve_bind(deploy: str, host: str | None = None, port: int | None = None) -> tuple[str, int]:
    """本地部署默认只听本机，服务器部署默认对外。显式传入的优先。"""
    if host:
        h = host
    else:
        h = os.getenv("TAM_HOST") or ("0.0.0.0" if deploy == "server" else "127.0.0.1")
    p = port or int(os.getenv("TAM_PORT") or 8848)
    return h, p


def _missing_bot_deps() -> list[str]:
    """机器人前端的额外依赖，只用网页的人不需要装，所以到这一步才查。"""
    import importlib.util

    need = {
        "telegram": "python-telegram-bot[job-queue]",
        "opentele": "opentele",
        "requests": "requests",
        "aiohttp": "aiohttp",
        "cbor2": "cbor2",
    }
    missing = []
    for mod, pkg in need.items():
        try:
            if importlib.util.find_spec(mod) is None:
                missing.append(pkg)
        except (ImportError, ValueError):
            missing.append(pkg)
    return missing


def _missing_web_deps() -> list[str]:
    """网页控制台的依赖，缺了会在 import api 时直接炸，提前拦下来给人话。"""
    import importlib.util

    need = {"fastapi": "fastapi", "uvicorn": "uvicorn[standard]", "pydantic": "pydantic"}
    missing = []
    for mod, pkg in need.items():
        try:
            if importlib.util.find_spec(mod) is None:
                missing.append(pkg)
        except (ImportError, ValueError):
            missing.append(pkg)
    return missing


def preflight(deploy: str, frontend: str) -> tuple[list[str], list[str]]:
    """启动前体检。返回（致命错误, 警告）；致命错误会阻断启动，警告只提醒。"""
    errors: list[str] = []
    warns: list[str] = []

    if frontend in ("web", "both"):
        missing_web = _missing_web_deps()
        if missing_web:
            errors.append(
                "网页前端的依赖没装齐：" + "、".join(missing_web) + "\n"
                "       → pip install -r requirements.txt"
            )

    if frontend in ("bot", "both"):
        if not (os.getenv("TAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")):
            errors.append(
                "未设 TAM_BOT_TOKEN，机器人启不了。\n"
                "       → 到 @BotFather 领一个填进 .env，或把 TAM_FRONTEND 改成 web"
            )
        missing = _missing_bot_deps()
        if missing:
            errors.append(
                "机器人前端的依赖没装齐：" + "、".join(missing) + "\n"
                "       → pip install -r requirements.txt\n"
                "       → 只想用网页的话这些不用装，把 TAM_FRONTEND 改成 web 即可"
            )
        if not (os.getenv("TAM_BOT_ADMIN_ID") or os.getenv("ADMIN_ID")):
            warns.append(
                "未设 TAM_BOT_ADMIN_ID，/vip /unvip /gb 管理命令对所有人失效（含你自己）。"
            )
        if not os.getenv("OKPAY_COST"):
            warns.append(
                "OKPAY_COST 留空 = 不收费，任何人发 /start 都直接是 VIP。\n"
                "       → 自用无所谓；对外开放请配 OKPAY_ID / OKPAY_TOKEN / OKPAY_COST"
            )

    if deploy == "server":
        warns.append(
            "服务器模式会监听 0.0.0.0（全网可达），请在前面套一层 HTTPS 反代。"
        )
        if os.getenv("TAM_NO_AUTH", "0").strip() in ("1", "true", "True"):
            errors.append(
                "危险：服务器模式下开了 TAM_NO_AUTH=1，等于把所有账号裸奉给公网。\n"
                "       → 改成 TAM_NO_AUTH=0 并设好 TAM_WEB_TOKEN（免令牌只能本机用）"
            )
        if not os.getenv("TAM_WEB_TOKEN"):
            errors.append(
                "服务器模式未设 TAM_WEB_TOKEN，控制台会没有访问门槛。\n"
                "       → python -m tam.cli init-key 生成一个填进 .env"
            )
        if not Path(os.getenv("TAM_PROXY_FILE", "proxy.txt")).exists():
            warns.append(
                "没找到 proxy.txt，所有号会走服务器裸 IP，风控风险很高。\n"
                "       → 每行一个：IP:端口:账户:密码:过期时间戳"
            )
        if not os.getenv("TAM_MASTER_KEY"):
            warns.append(
                "未设 TAM_MASTER_KEY，会话加密密钥会落盘。服务器上建议走环境变量。"
            )
    else:
        if os.getenv("TAM_HOST") in ("0.0.0.0", "::"):
            warns.append(
                "TAM_DEPLOY=local 却把 TAM_HOST 设成了 0.0.0.0，实际对外暴露了。\n"
                "       → 真要对外请改用 --deploy server，会多一层安全检查"
            )
    return errors, warns


def _banner(deploy: str, frontend: str, host: str, port: int) -> None:
    print("=" * 52)
    print(f"  TAM 启动：部署={deploy}  前端={frontend}")
    # 把几个容易被忽略的新变量打一行，避免只看 .env.example 才知道
    w = (os.getenv("TAM_WORKERS") or "4").strip()
    bc = (os.getenv("TAM_BATCH_CONCURRENCY") or "3").strip()
    kr = (os.getenv("TAM_KICK_RETRY") or "1h").strip()
    print(f"  并发：ZIP workers={w}  批量跑号={bc}  清设备重试={kr}")
    if frontend in ("web", "both"):
        shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        print("  网页控制台：http://" + shown + ":" + str(port) + "   (监听 " + host + ")")
    if frontend in ("bot", "both"):
        print("  Telegram 机器人：polling 中，对机器人发 /start")
    print("=" * 52)


async def _serve_web_async(host: str, port: int) -> None:
    import uvicorn

    from .api import app

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    await uvicorn.Server(config).serve()


async def _serve_bot_async() -> None:
    """在已有事件循环里跑 PTB（run_polling 会自己建循环，所以这里手动拆开）。"""
    from .bot import build_application

    app = build_application()
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


async def _run_both(host: str, port: int) -> None:
    tasks = [
        asyncio.create_task(_serve_web_async(host, port), name="web"),
        asyncio.create_task(_serve_bot_async(), name="bot"),
    ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for t in pending:
        t.cancel()
    for t in done:
        exc = t.exception()
        if exc:  # 一个前端挂了就整体退出，避免假活
            raise exc


def run(
    deploy: str | None = None,
    frontend: str | None = None,
    host: str | None = None,
    port: int | None = None,
    skip_doctor: bool = False,
    force: bool = False,
    menu: bool | None = None,
) -> None:
    _load_dotenv(Path(os.getenv("TAM_ENV_FILE", ".env")))
    # 先加载 .env 再问，菜单里的默认值才能反映你配好的 TAM_DEPLOY / TAM_FRONTEND
    deploy, frontend = choose_mode(deploy, frontend, menu=menu)
    host, port = resolve_bind(deploy, host, port)

    errors, warns = preflight(deploy, frontend)
    if errors or warns:
        print("\n" + "-" * 52)
        print(f"  启动前体检（部署={deploy}  前端={frontend}）")
        print("-" * 52)
        for e in errors:
            print(f"  ❌  {e}")
        for w in warns:
            print(f"  ⚠️  {w}")
        print("-" * 52 + "\n")
    if errors and not force:
        print("上面的问题会直接影响运行或安全，已停止启动。")
        print("确实知道自己在干什么的话，加 --force 可以继续。")
        raise SystemExit(1)
    if errors and force:
        print("已用 --force 忽略上述问题，继续启动。出事自负。\n")

    # 放在体检之后：缺密钥这类问题要给人话提示，不能抛一屏 traceback
    try:
        settings = Settings.load()
    except RuntimeError as exc:
        print(f"  ❌  {exc}")
        print("\n配置不完整，已停止启动。")
        raise SystemExit(1)
    os.environ.setdefault("TAM_DATA_DIR", str(settings.data_dir))

    if not skip_doctor:
        from .doctor import print_report, run_doctor

        res = run_doctor(fix=True)
        if not res["ok"] or res["fixed"]:
            print_report(res, True)

    _banner(deploy, frontend, host, port)

    try:
        if frontend == "web":
            from .api import serve

            serve(host, port)
        elif frontend == "bot":
            from .bot import run as run_bot

            run_bot()
        else:
            asyncio.run(_run_both(host, port))
    except KeyboardInterrupt:
        print("\n已停止。")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="tam-run", description="TAM 统一启动（本地/服务器 × 网页/机器人）")
    p.add_argument("--deploy", choices=DEPLOYS, help="默认读 TAM_DEPLOY，再默认 local")
    p.add_argument("--frontend", choices=FRONTENDS, help="默认读 TAM_FRONTEND，再默认 web")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--no-doctor", action="store_true", help="跳过启动前体检")
    p.add_argument("--force", action="store_true", help="忽略体检的致命错误强行启动")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--menu", dest="menu", action="store_true", default=None,
                   help="强制弹启动模式选择菜单")
    g.add_argument("--no-menu", dest="menu", action="store_false",
                   help="不弹菜单，直接用环境变量/默认值（守护进程用这个）")
    args = p.parse_args(argv)
    try:
        run(args.deploy, args.frontend, args.host, args.port, args.no_doctor,
            args.force, args.menu)
    except ValueError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
