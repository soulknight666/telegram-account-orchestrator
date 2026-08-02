"""一键体检与自动修复。

把以往需要人工排查的坑全部变成可自动检测 + 可自动修复的检查项：

    python -m tam.cli doctor         # 只体检，不改任何东西
    python -m tam.cli doctor --fix   # 体检并自动修复

设计原则：
- 每项检查独立，一项失败不阻断其他项。
- 能自动修的就自动修（生成密钥、写 .env、装依赖、打 opentele 补丁……），
  修不了的给出一句话人话指引。
- 结果同时可以输出 JSON，供向导 / Web / Agent 复用。
"""
from __future__ import annotations

import base64
import os
import re
import secrets
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
MIN_PY = (3, 10)
PLACEHOLDERS = {"", "change-me", "1234567", "0", "x" * 32,
                "your_api_hash", "your-api-hash", "none"}

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str            # ok | warn | fail
    detail: str = ""
    hint: str = ""         # 修不了时的人话指引
    fixed: bool = False
    fix_log: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = {"name": self.name, "status": self.status, "detail": self.detail}
        if self.hint:
            d["hint"] = self.hint
        if self.fixed:
            d["fixed"] = True
        if self.fix_log:
            d["fix_log"] = self.fix_log
        return d


# ---------- .env 读写（幂等、保留注释） ----------

def _unset(value: str | None) -> bool:
    return (value or "").strip().strip('"').strip("'").lower() in PLACEHOLDERS


def read_env(path: Path | None = None) -> dict[str, str]:
    path = Path(path) if path is not None else ENV_PATH
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif v.startswith("#"):
            v = ""
        else:
            v = re.sub(r"\s+#.*$", "", v).strip().strip('"').strip("'")
        out[k.strip()] = v
    return out


def set_env(values: dict[str, str], path: Path | None = None) -> None:
    """写入/更新 .env，保留原有注释与顺序。"""
    path = Path(path) if path is not None else ENV_PATH
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k = s.split("=", 1)[0].strip()
        if k in remaining:
            lines[i] = f"{k}={remaining.pop(k)}"
    for k, v in remaining.items():
        lines.append(f"{k}={v}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def gen_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def _pip(args: list[str], log: list[str]) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", *args]
    log.append(" ".join(cmd))
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900)
    except Exception as exc:  # noqa: BLE001
        log.append(f"执行失败：{exc}")
        return False
    if p.returncode == 0:
        return True
    log.append((p.stderr or p.stdout).strip()[-400:])
    cmd2 = cmd + ["-i", MIRROR]
    log.append("官方源失败，改用清华镜像重试")
    try:
        p2 = subprocess.run(cmd2, cwd=ROOT, capture_output=True, text=True, timeout=900)
    except Exception as exc:  # noqa: BLE001
        log.append(f"执行失败：{exc}")
        return False
    if p2.returncode != 0:
        log.append((p2.stderr or p2.stdout).strip()[-400:])
    return p2.returncode == 0


# ---------- 具体检查项 ----------

def check_python(fix: bool) -> Check:
    v = "%d.%d.%d" % sys.version_info[:3]
    if sys.version_info < MIN_PY:
        return Check("Python 版本", FAIL, f"当前 {v}",
                     hint=f"需要 {MIN_PY[0]}.{MIN_PY[1]} 以上，到 python.org 下载新版重装")
    if sys.version_info >= (3, 13):
        return Check("Python 版本", WARN, f"{v}（tdata 导入依赖 opentele 在 3.13 需兼容补丁）",
                     hint="若导入 tdata 持续报错，可改用 Python 3.12 重建 .venv")
    return Check("Python 版本", OK, v)


def check_core_deps(fix: bool) -> Check:
    missing = []
    for mod, pkg in (("fastapi", "fastapi"), ("uvicorn", "uvicorn[standard]"),
                     ("telethon", "telethon"), ("cryptography", "cryptography"),
                     ("pydantic", "pydantic"), ("python_socks", "python-socks[asyncio]")):
        try:
            __import__(mod)
        except BaseException:  # noqa: BLE001
            missing.append(pkg)
    if not missing:
        return Check("核心依赖", OK, "已全部安装")
    c = Check("核心依赖", FAIL, "缺少：" + ", ".join(missing),
              hint="执行：pip install -r requirements.txt")
    if not fix:
        return c
    req = ROOT / "requirements.txt"
    args = ["-r", str(req)] if req.exists() else missing
    if _pip(args, c.fix_log):
        still = []
        for mod in ("fastapi", "uvicorn", "telethon", "cryptography", "pydantic"):
            try:
                __import__(mod)
            except BaseException:  # noqa: BLE001
                still.append(mod)
        if not still:
            c.status, c.detail, c.fixed = OK, "已自动安装", True
        else:
            c.detail = "安装后仍缺少：" + ", ".join(still)
    return c


def check_env_file(fix: bool) -> Check:
    if ENV_PATH.exists():
        return Check(".env 配置文件", OK, str(ENV_PATH))
    c = Check(".env 配置文件", FAIL, "不存在",
              hint="把 .env.example 复制为 .env")
    if not fix:
        return c
    if EXAMPLE_PATH.exists():
        ENV_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        ENV_PATH.write_text("TAM_MASTER_KEY=\nTAM_WEB_TOKEN=\n", encoding="utf-8")
    c.status, c.detail, c.fixed = OK, "已自动创建", True
    c.fix_log.append(f"创建 {ENV_PATH}")
    return c


def _check_secret(key: str, label: str, fix: bool) -> Check:
    env = read_env()
    val = env.get(key) or os.environ.get(key, "")
    if not _unset(val):
        return Check(label, OK, f"已设置（{len(val)} 位）")
    c = Check(label, FAIL, "未设置或仍为示例占位值",
              hint=f"在 .env 里填写 {key}")
    if not fix:
        return c
    new = gen_key()
    set_env({key: new})
    os.environ[key] = new
    c.status, c.detail, c.fixed = OK, "已自动生成并写入 .env", True
    c.fix_log.append(f"{key} 已生成")
    return c


def check_master_key(fix: bool) -> Check:
    return _check_secret("TAM_MASTER_KEY", "主密钥 TAM_MASTER_KEY", fix)


def no_auth_mode() -> bool:
    """用户在向导里选了“本机免令牌”。"""
    env = read_env()
    raw = env.get("TAM_NO_AUTH") or os.environ.get("TAM_NO_AUTH", "")
    return str(raw).strip().lower() in {"1", "true", "yes"}


def check_web_token(fix: bool) -> Check:
    if no_auth_mode():
        return Check("访问令牌 TAM_WEB_TOKEN", WARN,
                     "已选择免令牌模式（仅限本机 127.0.0.1 使用）",
                     hint="要恢复令牌：把 .env 里的 TAM_NO_AUTH 改成 0，再重跑一次向导")
    return _check_secret("TAM_WEB_TOKEN", "访问令牌 TAM_WEB_TOKEN", fix)


def check_api_credentials(fix: bool) -> Check:
    env = read_env()
    aid = env.get("TAM_API_ID") or os.environ.get("TAM_API_ID", "")
    ahash = env.get("TAM_API_HASH") or os.environ.get("TAM_API_HASH", "")
    if _unset(aid) or _unset(ahash):
        return Check("Telegram api_id / api_hash", WARN, "未填写",
                     hint="只影响“手机号登录”；tdata 导入不需要。"
                          "需要时到 my.telegram.org 申请一对，全部账号共用一对即可")
    if not str(aid).strip().isdigit():
        return Check("Telegram api_id / api_hash", FAIL, f"api_id 不是纯数字：{aid!r}",
                     hint="api_id 是纯数字，api_hash 是 32 位字符串，别填反")
    return Check("Telegram api_id / api_hash", OK, "已填写")


def check_settings(fix: bool) -> Check:
    try:
        from .config import Settings

        s = Settings.load()
    except BaseException as exc:  # noqa: BLE001
        return Check("配置加载", FAIL, f"{type(exc).__name__}: {exc}",
                     hint="检查 .env 格式：每行 KEY=value，值里不要带引号/中文引号")
    return Check("配置加载", OK, f"数据目录 {s.data_dir}")


def check_database(fix: bool) -> Check:
    try:
        from .config import Settings
        from .db import Database

        s = Settings.load()
        s.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(s.db_path)
        n = len(db.list())
    except BaseException as exc:  # noqa: BLE001
        return Check("数据库", FAIL, f"{type(exc).__name__}: {exc}",
                     hint="确认 data/ 目录可写，或删除 data/tam.db 重建（会丢失已存账号）")
    return Check("数据库", OK, f"可读写，现有 {n} 个账号")


def check_crypto_roundtrip(fix: bool) -> Check:
    """主密钥能不能真的加解密。"""
    try:
        from .config import Settings
        from .crypto import decrypt, encrypt

        key = Settings.load().master_key
        assert decrypt(key, encrypt(key, "probe")) == "probe"
    except BaseException as exc:  # noqa: BLE001
        return Check("会话加密", FAIL, f"{type(exc).__name__}: {exc}",
                     hint="主密钥无效。若已有账号入库，换密钥会导致旧会话不可解")
    return Check("会话加密", OK, "加解密往返正常")


def check_webui(fix: bool) -> Check:
    p = Path(__file__).resolve().parent / "web" / "index.html"
    if not p.exists():
        return Check("Web 控制台文件", FAIL, f"缺失 {p}",
                     hint="重新解压完整项目包")
    return Check("Web 控制台文件", OK, f"{p.stat().st_size // 1024} KB")


def check_api_import(fix: bool) -> Check:
    try:
        from . import api  # noqa: F401

        routes = len([r for r in api.app.routes if getattr(r, "path", "").startswith("/api")])
    except BaseException as exc:  # noqa: BLE001
        return Check("API 服务", FAIL, f"{type(exc).__name__}: {exc}",
                     hint="先修好核心依赖，再重跑体检")
    return Check("API 服务", OK, f"{routes} 个接口可用")


def check_opentele(fix: bool) -> Check:
    """opentele 已不再使用；tdata 由内置纯 Python 解析器接管。

    重要：这里绝不能 `import opentele`。它一被导入就会改写 Telethon 的类，
    在 Python 3.13 上会把方法包成自己调自己，导致后续建连接时
    RecursionError: maximum recursion depth exceeded。只做静态探测。
    """
    from . import opentele_patch

    if not opentele_patch.installed():
        return Check("tdata 解析（opentele 已弃用）", OK,
                     "未安装 opentele，正合适：tdata 由内置解析器处理")
    return Check(
        "tdata 解析（opentele 已弃用）", WARN,
        "检测到环境里装了 opentele（本程序不再使用它）",
        hint="建议卸载以免干扰 Telethon：pip uninstall -y opentele",
    )


def check_port(fix: bool, port: int = 8848) -> Check:
    s = socket.socket()
    s.settimeout(0.4)
    busy = s.connect_ex(("127.0.0.1", port)) == 0
    s.close()
    if busy:
        return Check(f"端口 {port}", WARN, "已被占用",
                     hint="可能是上一个实例还在跑。关掉旧窗口，或换端口：serve --port 8849")
    return Check(f"端口 {port}", OK, "可用")


CHECKS: list[Callable[[bool], Check]] = [
    check_python,
    check_core_deps,
    check_env_file,
    check_master_key,
    check_web_token,
    check_settings,
    check_crypto_roundtrip,
    check_database,
    check_api_credentials,
    check_webui,
    check_api_import,
    check_opentele,
    check_port,
]


def run_doctor(fix: bool = False) -> dict[str, Any]:
    checks: list[Check] = []
    for fn in CHECKS:
        try:
            checks.append(fn(fix))
        except BaseException as exc:  # noqa: BLE001
            checks.append(Check(getattr(fn, "__name__", "check"), FAIL,
                                f"检查自身出错：{type(exc).__name__}: {exc}"))
    fails = [c for c in checks if c.status == FAIL]
    warns = [c for c in checks if c.status == WARN]
    return {
        "ok": not fails,
        "fixed": [c.name for c in checks if c.fixed],
        "failed": [c.name for c in fails],
        "warned": [c.name for c in warns],
        "checks": [c.as_dict() for c in checks],
    }


# ---------- 终端输出 ----------

def _color(text: str, code: str) -> str:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


ICON = {OK: ("✓", "32;1"), WARN: ("!", "33;1"), FAIL: ("✗", "31;1")}


def print_report(result: dict[str, Any], fix: bool) -> None:
    print(_color("\n  一键体检" + ("（自动修复模式）" if fix else "（仅检查，不修改）"), "1"))
    print(_color("  " + "─" * 50, "90"))
    for c in result["checks"]:
        mark, code = ICON.get(c["status"], ("?", "0"))
        tail = c.get("detail", "")
        fixed = _color(" 已自动修复", "32") if c.get("fixed") else ""
        print(f"  {_color(mark, code)} {c['name']}：{tail}{fixed}")
        if c.get("hint") and c["status"] != OK:
            print(_color(f"      → {c['hint']}", "90"))
    print(_color("  " + "─" * 50, "90"))
    if result["ok"]:
        msg = "  全部通过，可以直接启动：python -m tam.cli serve"
        if result["warned"]:
            msg += f"\n  （{len(result['warned'])} 项提醒，不影响使用）"
        print(_color(msg, "32;1"))
    else:
        print(_color(f"  {len(result['failed'])} 项未通过：" + "、".join(result["failed"]), "31;1"))
        if not fix:
            print(_color("  试试自动修复：python -m tam.cli doctor --fix", "36;1"))
    print()
