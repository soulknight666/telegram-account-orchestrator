#!/usr/bin/env python3
r"""Telegram 账号管理器 · 一键向导（零基础可用）

作用：检查 Python → 建虚拟环境 → 装依赖 → 生成 .env（含主密钥与访问令牌）
      → 可选填 api_id/api_hash → 启动 Web 控制台并自动开浏览器。

用法：
    Windows  双击 start.bat
    Mac/Linux  ./start.sh
    或直接： python setup.py --auto

一键模式（--auto，start 脚本默认）：
    自动建虚拟环境、装依赖、生成主密钥、体检。
    启动前只问两件事：自己电脑还是服务器、网页还是机器人（或双端）。
    api_id / 代理可以后在 .env 或网页参数面板补。

可选参数：
    --auto                一键模式（start 脚本默认带上）
    --deploy local|server 跳过「部署」提问
    --frontend web|bot|both 跳过「前端」提问
    --skip-install / --no-venv / --no-start / --port
    --no-token / --token   强制关/开访问令牌
只依赖标准库，可在任何 Python 3.10+ 上直接运行。
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from tam.release_config import update_env_values

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"
VENV_DIR = ROOT / ".venv"
MIN_PY = (3, 10)
STEPS = 6


# ---------- 终端输出 ----------

def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if sys.platform == "win32":
        return os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM") or False
    return sys.stdout.isatty()


C = _supports_color()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if C else text


def title(text: str) -> None:
    line = "─" * 52
    print(f"\n{_c(line, '90')}\n  {_c(text, '1')}\n{_c(line, '90')}")


def step(n: int, text: str) -> None:
    print(f"\n{_c(f'[{n}/{STEPS}]', '36;1')} {_c(text, '1')}")


def ok(text: str) -> None:
    print(f"  {_c('✓', '32;1')} {text}")


def warn(text: str) -> None:
    print(f"  {_c('!', '33;1')} {text}")


def fail(text: str) -> None:
    print(f"  {_c('✗', '31;1')} {text}")


def info(text: str) -> None:
    print(f"    {_c(text, '90')}")


def ask(prompt: str, default: str = "") -> str:
    tip = f" [{default}]" if default else ""
    try:
        val = input(f"  {_c('?', '36;1')} {prompt}{tip}: ").strip()
    except EOFError:
        return default
    return val or default


def ask_yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    val = ask(f"{prompt} ({hint})").lower()
    if not val:
        return default
    return val in {"y", "yes", "是", "1"}


def die(text: str, code: int = 1) -> None:
    fail(text)
    print()
    input("  按回车键退出...")
    sys.exit(code)


# ---------- .env 读写 ----------

def read_env() -> dict[str, str]:
    data: dict[str, str] = {}
    if not ENV_PATH.exists():
        return data
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return data


def set_env(updates: dict[str, str]) -> None:
    """就地更新 .env，保留注释与未提及的行。"""
    if not ENV_PATH.exists() and EXAMPLE_PATH.exists():
        shutil.copy(EXAMPLE_PATH, ENV_PATH)
    update_env_values(updates, ENV_PATH)


PLACEHOLDERS = {"", "0", "change-me", "changeme", "1234567",
                "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "your-api-hash", "your_api_id"}


def unset(value: str | None) -> bool:
    """空值或 .env.example 里的占位示例都视为未配置。"""
    return (value or "").strip().lower() in PLACEHOLDERS


def gen_master_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


# ---------- 环境 ----------

def venv_python() -> Path:
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def run(cmd: list[str], desc: str) -> bool:
    info(" ".join(str(c) for c in cmd))
    try:
        proc = subprocess.run(cmd, cwd=ROOT)
    except FileNotFoundError:
        fail(f"{desc}失败：找不到 {cmd[0]}")
        return False
    if proc.returncode != 0:
        fail(f"{desc}失败（退出码 {proc.returncode}）")
        return False
    return True


def _importable(py: Path, code: str) -> bool:
    try:
        return subprocess.run([str(py), "-c", code], cwd=ROOT,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except Exception:
        return False


def deps_ready(py: Path) -> bool:
    """核心依赖是否已就绪（不含可选的 opentele）。"""
    return _importable(py, "import fastapi, uvicorn, telethon, cryptography")


def has_opentele(py: Path) -> bool:
    return _importable(py, "import opentele")


def install_opentele(py: Path) -> bool:
    """安装可选依赖 opentele（tdata 导入专用）。失败不中断安装流程。"""
    req = ROOT / "requirements-optional.txt"
    base = [str(py), "-m", "pip", "install"]
    cmd = base + (["-r", str(req)] if req.exists() else ["opentele>=1.15"])
    if run(cmd, "安装 opentele"):
        return True
    warn("官方源失败，改用清华镜像重试")
    return run(cmd + ["-i", MIRROR], "安装 opentele(镜像)")


# ---------- 主流程 ----------


def _prompt_choice(title: str, options: list[tuple[str, str]], default: str) -> str:
    """终端数字菜单。回车=默认；EOF/非 TTY 直接返回默认。"""
    names = [v for v, _ in options]
    if default not in names:
        default = names[0]
    if not sys.stdin.isatty():
        return default
    print(f"\n{title}")
    for i, (val, desc) in enumerate(options, 1):
        mark = "  ← 默认" if val == default else ""
        print(f"    {i}. {val:<6}  {desc}{mark}")
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


def choose_deploy_frontend(
    deploy: str | None,
    frontend: str | None,
    env: dict[str, str],
    *,
    force_menu: bool = True,
) -> tuple[str, str]:
    """选部署位置 + 前端。命令行已指定的不再问。"""
    def _norm_d(v: str | None) -> str:
        x = (v or "").strip().lower()
        return x if x in ("local", "server") else "local"

    def _norm_f(v: str | None) -> str:
        x = (v or "").strip().lower()
        return x if x in ("web", "bot", "both") else "web"

    d_cli = _norm_d(deploy) if deploy else None
    f_cli = _norm_f(frontend) if frontend else None
    d_default = d_cli or _norm_d(env.get("TAM_DEPLOY")) or "local"
    f_default = f_cli or _norm_f(env.get("TAM_FRONTEND")) or "web"

    # 命令行两项都给了：完全静默
    if d_cli and f_cli:
        return d_cli, f_cli

    need_d = d_cli is None
    need_f = f_cli is None
    if (force_menu or sys.stdin.isatty()) and (need_d or need_f):
        print("\n" + "─" * 52)
        print("  启动选项（写入 .env，下次 start 会记住）")
        print("─" * 52)
        d = d_cli or _prompt_choice(
            "  部署在哪里？",
            [
                ("local", "自己电脑 · 网页只监听 127.0.0.1，本机打开即用"),
                ("server", "服务器 · 对外访问（建议开令牌 + HTTPS 反代 + 代理池）"),
            ],
            d_default,
        )
        f = f_cli or _prompt_choice(
            "  用哪个前端？",
            [
                ("web", "网页控制台（管自己的号：登录/导入/养号/群发/线索）"),
                ("bot", "Telegram 机器人（用户上传号包自助处理）"),
                ("both", "网页 + 机器人一起开"),
            ],
            f_default,
        )
        print("─" * 52)
        return d, f

    return d_default, f_default


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Telegram 账号管理器 · 一键配置 / 启动",
        add_help=True,
    )
    ap.add_argument("--auto", action="store_true",
                    help="一键模式：自动装依赖/写配置；启动前可选部署与前端（start 脚本默认）")
    ap.add_argument("--deploy", choices=("local", "server"),
                    help="部署：local=自己电脑，server=服务器（指定则不再询问）")
    ap.add_argument("--frontend", choices=("web", "bot", "both"),
                    help="前端：web=网页，bot=机器人，both=双端（指定则不再询问）")
    ap.add_argument("--skip-install", action="store_true")
    ap.add_argument("--no-venv", action="store_true")
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--port", type=int, default=8848)
    ap.add_argument("--no-token", action="store_true",
                    help="本机免令牌：不问直接关闭访问令牌")
    ap.add_argument("--token", action="store_true",
                    help="启用访问令牌（与 --auto 联用时优先于免令牌）")
    args = ap.parse_args()
    auto = bool(args.auto)

    title("Telegram 账号管理器 · " + ("一键启动" if auto else "安装向导"))
    if auto:
        print("  全自动模式：装依赖 → 写配置 → 体检 → 启动网页，无需手动输入。")
    else:
        print("  遇到提问直接回车即默认值。想完全免交互请用：python setup.py --auto")
    print(f"  项目目录：{ROOT}")

    # 1 Python 版本
    step(1, "检查 Python 版本")
    if sys.version_info < MIN_PY:
        die(f"当前 Python {sys.version.split()[0]}，需要 {MIN_PY[0]}.{MIN_PY[1]} 以上。"
            "请到 python.org 下载新版，安装时勾选 Add Python to PATH。")
    ok(f"Python {sys.version.split()[0]}")

    # 2 虚拟环境
    step(2, "准备虚拟环境 .venv")
    py = Path(sys.executable)
    if args.no_venv:
        warn("已指定 --no-venv，使用当前 Python 环境")
    else:
        if venv_python().exists():
            ok("虚拟环境已存在，直接复用")
        else:
            if not run([sys.executable, "-m", "venv", str(VENV_DIR)], "创建虚拟环境"):
                die("创建虚拟环境失败。Linux 上可能需要：sudo apt install python3-venv")
            ok("已创建 .venv")
        py = venv_python()

    # 3 依赖
    step(3, "安装依赖（首次约 1–2 分钟）")
    if args.skip_install:
        warn("已指定 --skip-install，跳过")
    elif deps_ready(py):
        ok("依赖已齐备，跳过安装")
    else:
        run([str(py), "-m", "pip", "install", "--upgrade", "pip", "-q"], "升级 pip")
        cmd = [str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]
        if not run(cmd, "安装依赖"):
            warn("官方源失败，改用清华镜像重试")
            cmd += ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
            if not run(cmd, "安装依赖(镜像)"):
                die("依赖安装失败。请检查网络或代理后重试。")
        ok("依赖安装完成")

    # 3b tdata 导入：已改用内置纯 Python 解析器，无需任何额外依赖
    if not args.skip_install and has_opentele(py):
        warn("检测到环境里装了 opentele，本程序已不再使用它。"
             "它会改写 Telethon 导致连接报错，建议卸载：pip uninstall -y opentele")

    # 4 .env
    step(4, "生成配置文件 .env")
    if not ENV_PATH.exists():
        if EXAMPLE_PATH.exists():
            shutil.copy(EXAMPLE_PATH, ENV_PATH)
            ok("已从 .env.example 创建 .env")
        else:
            ENV_PATH.write_text("", encoding="utf-8")
            ok("已创建空 .env")
    else:
        ok(".env 已存在，将保留现有配置")

    env = read_env()
    updates: dict[str, str] = {}

    if not unset(env.get("TAM_MASTER_KEY")):
        ok("主密钥已存在，保持不变（换掉会导致已存会话无法解密）")
    else:
        updates["TAM_MASTER_KEY"] = gen_master_key()
        ok("已生成主密钥 TAM_MASTER_KEY")

    has_token = not unset(env.get("TAM_WEB_TOKEN")) and len(env.get("TAM_WEB_TOKEN", "")) >= 16
    was_no_auth = str(env.get("TAM_NO_AUTH", "")).strip().lower() in {"1", "true", "yes"}

    # 先选部署 / 前端（写入 .env，决定令牌默认策略与启动命令）
    deploy, frontend = choose_deploy_frontend(
        args.deploy, args.frontend, env, force_menu=True,
    )
    ok(f"部署={deploy}  前端={frontend}")

    # 令牌策略：
    #   --token / --no-token 显式优先
    #   server 默认开令牌（安全）
    #   local  默认免令牌（本机打开即用）；若已有令牌则尊重
    if args.token:
        use_token = True
    elif args.no_token:
        use_token = False
    elif deploy == "server":
        use_token = True
        info("服务器模式：默认启用访问令牌（可用 --no-token 强制关闭，不推荐）")
    elif auto:
        use_token = has_token and not was_no_auth
        if not use_token:
            info("本机模式：免令牌（只监听 127.0.0.1）。需要令牌：start 时加 --token")
    else:
        print()
        info("访问令牌用来拦住别人打开你的控制台。")
        info("自己电脑可以不启用；服务器 / 局域网强烈建议启用。")
        default_on = (has_token and not was_no_auth) or deploy == "server"
        use_token = ask_yes("启用访问令牌？", default_on)

    token = ""
    if not use_token:
        token = ""
        updates["TAM_WEB_TOKEN"] = ""
        updates["TAM_NO_AUTH"] = "1"
        ok("已关闭访问令牌（免令牌模式）")
        if deploy == "server":
            warn("服务器模式却关了令牌：任何人都能打开控制台，风险极高。")
    elif has_token:
        token = env["TAM_WEB_TOKEN"]
        updates["TAM_NO_AUTH"] = "0"
        ok("访问令牌已存在，保持不变")
    else:
        token = secrets.token_urlsafe(24)
        updates["TAM_WEB_TOKEN"] = token
        updates["TAM_NO_AUTH"] = "0"
        ok("已生成访问令牌 TAM_WEB_TOKEN")

    # api_id / 代理：一键模式跳过，避免打断
    if unset(env.get("TAM_API_ID")) or unset(env.get("TAM_API_HASH")):
        if auto:
            updates.setdefault("TAM_API_ID", env.get("TAM_API_ID") or "0")
            if unset(env.get("TAM_API_HASH")):
                updates.setdefault("TAM_API_HASH", "")
            info("已跳过 api_id/api_hash（手机号验证码登录需要；tdata/session 导入不需要）")
        else:
            print()
            info("api_id / api_hash 取自 my.telegram.org → API development tools。")
            info("只用 tdata/session 导入可以直接回车跳过，以后随时补填。")
            api_id = ask("api_id（纯数字，回车跳过）")
            if api_id.isdigit():
                updates["TAM_API_ID"] = api_id
                api_hash = ask("api_hash（32 位字符）")
                if api_hash:
                    updates["TAM_API_HASH"] = api_hash
                ok("已记录 API 凭据")
            else:
                updates.setdefault("TAM_API_ID", "0")
                warn("已跳过：不能用手机号+验证码登录，tdata/session 导入不受影响")
    else:
        ok("API 凭据已配置")

    if unset(env.get("TAM_DEFAULT_PROXY")):
        if auto:
            if deploy == "server":
                warn("服务器未设默认代理：号会走服务器裸 IP，风控风险高。"
                     "可在 .env 写 TAM_DEFAULT_PROXY= 或准备 proxy.txt")
            else:
                info("未设置默认代理。中国大陆可在 .env 写 "
                     "TAM_DEFAULT_PROXY=socks5://127.0.0.1:1080")
        else:
            print()
            info("中国大陆直连不上 Telegram，需要代理。格式：socks5://127.0.0.1:1080")
            proxy = ask("默认代理（回车跳过）")
            if proxy:
                updates["TAM_DEFAULT_PROXY"] = proxy
                ok("已设置默认代理")

    # 机器人 Token：选了 bot/both 且还没有时，尽量问一次（可回车跳过）
    if frontend in ("bot", "both") and unset(env.get("TAM_BOT_TOKEN")):
        if auto and sys.stdin.isatty():
            print()
            info("机器人模式需要 @BotFather 发的 Token。")
            bot_tok = ask("TAM_BOT_TOKEN（回车跳过，稍后写 .env）")
            if bot_tok:
                updates["TAM_BOT_TOKEN"] = bot_tok
                ok("已写入 TAM_BOT_TOKEN")
            else:
                warn("未填机器人 Token：启动后机器人端会起不来，可随后写入 .env 再重启")
        elif not auto:
            print()
            bot_tok = ask("TAM_BOT_TOKEN（@BotFather，回车跳过）")
            if bot_tok:
                updates["TAM_BOT_TOKEN"] = bot_tok
        else:
            warn("未设 TAM_BOT_TOKEN（机器人前端需要）")

    updates["TAM_DEPLOY"] = deploy
    updates["TAM_FRONTEND"] = frontend
    updates.setdefault("TAM_PORT", str(args.port))

    if updates:
        set_env(updates)
        env = read_env()
        if use_token and not token:
            token = env.get("TAM_WEB_TOKEN", "") or token
    ok(f"配置已保存：{ENV_PATH}")
    info("常用可调：TAM_WORKERS / TAM_BATCH_CONCURRENCY / TAM_KICK_RETRY（见 .env.example）")

    # 5 自检
    step(5, "一键体检（发现问题自动修复）")
    check = subprocess.run([str(py), "-m", "tam.cli", "doctor", "--fix"], cwd=ROOT)
    if check.returncode != 0:
        warn("体检有项目未通过，请看上方提示。")
        if auto:
            warn("将继续尝试启动；若失败，把上方报错发出来即可。")
        elif not ask_yes("仍然继续启动？", True):
            return
    else:
        ok("体检全部通过")

    # 6 启动（走统一入口，尊重 deploy × frontend）
    step(6, f"启动服务（{deploy} / {frontend}）")
    host = "0.0.0.0" if deploy == "server" else "127.0.0.1"
    url = f"http://127.0.0.1:{args.port}"
    print()
    if frontend in ("web", "both"):
        print(f"  网页地址：  {_c(url if deploy == 'local' else f'http://<服务器IP>:{args.port}', '36;1')}")
        if deploy == "server":
            info(f"本机绑定 {host}:{args.port}（全网可达，请前置 HTTPS 反代）")
    if frontend in ("bot", "both"):
        print(f"  机器人：    {_c('已按 TAM_FRONTEND 启动，去 Telegram 里找你的 Bot', '36;1')}")
    if token:
        print(f"  访问令牌：  {_c(token, '33;1')}")
        info("网页右上角粘贴令牌后回车（仅网页需要）。")
    else:
        print(f"  访问令牌：  {_c('未启用（本机打开网页即用）', '32;1')}")
    info("停止服务：本窗口按 Ctrl + C。下次直接再运行 start 即可。")
    print()

    if args.no_start:
        info("已指定 --no-start，未启动服务。")
        info(f"手动启动：{py} -m tam.cli run --deploy {deploy} --frontend {frontend}")
        return
    if not auto and not ask_yes("现在启动？", True):
        info(f"以后手动启动：{py} -m tam.cli run --deploy {deploy} --frontend {frontend}")
        return

    cmd = [
        str(py), "-m", "tam.cli", "run",
        "--deploy", deploy,
        "--frontend", frontend,
        "--port", str(args.port),
        "--no-menu",
    ]
    try:
        proc = subprocess.Popen(cmd, cwd=ROOT)
    except Exception as exc:
        die(f"启动失败：{exc!r}")
        return

    if frontend in ("web", "both") and deploy == "local":
        time.sleep(2.5)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        print("\n  已停止。")



if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  已取消。")
