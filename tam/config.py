"""运行配置。所有敏感项从环境变量或 .env 读取。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        # 引号包裹时取引号内内容；否则去掉行尾行内注释（前面需有空白）
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif v.startswith("#"):
            # 形如 KEY=      # 说明，值为空
            v = ""
        else:
            v = re.sub(r"\s+#.*$", "", v).strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


def _int_env(key: str, default: int = 0) -> int:
    """宽容读取整数环境变量：空值 / 非数字 / 带引号均回退到默认值。"""
    raw = (os.environ.get(key) or "").strip().strip('"').strip("'")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# 时长写法：30s / 15m / 2h / 1.5h / 1h30m / 90（纯数字按秒）
_DUR_UNITS = {"s": 1.0, "秒": 1.0, "m": 60.0, "分": 60.0, "h": 3600.0, "时": 3600.0,
              "d": 86400.0, "天": 86400.0}
_DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z秒分时天]*)")
_DUR_OK = re.compile(r"(?:\d+(?:\.\d+)?[a-z秒分时天]*)+")
# 中文写法先归一：“2小时”里的“小”不在单位表里，不换会被当成 2 秒
_DUR_CN = (("小时", "h"), ("分钟", "m"), ("秒钟", "s"), ("天", "d"), ("个", ""))


def parse_duration(raw: str | float | int | None, default: float) -> float:
    """把人写的时长转成秒。看不懂就回退默认值，不抛异常。

    >>> parse_duration("90s", 0), parse_duration("15m", 0), parse_duration("2h", 0)
    (90.0, 900.0, 7200.0)
    >>> parse_duration("1h30m", 0)
    5400.0
    """
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else default
    txt = str(raw).strip().strip('"').strip("'").lower().replace(" ", "")
    for cn, en in _DUR_CN:
        txt = txt.replace(cn, en)
    # 负数、夹杂字符（比如 "-5"、"1h左右"）一律当看不懂，回退默认值
    if not txt or not _DUR_OK.fullmatch(txt):
        return default
    total, matched = 0.0, False
    for num, unit in _DUR_RE.findall(txt):
        u = unit[:1] if unit else ""
        # 单位只取首字符：sec/second/秒 都归 s，min/分钟 归 m，hour/小时 归 h
        mult = _DUR_UNITS.get(u, 1.0 if u == "" else None)
        if mult is None:
            return default
        total += float(num) * mult
        matched = True
    return total if matched and total > 0 else default


def _duration_env(key: str, default: float, minimum: float = 0.0) -> float:
    """读一个带单位的时长环境变量，并抓住下限（避免写 1s 把自己打死）。"""
    val = parse_duration(os.environ.get(key), default)
    return max(val, minimum)


def _float_env(key: str, default: float) -> float:
    raw = (os.environ.get(key) or "").strip().strip('"').strip("'")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    master_key: str            # 用于加密 session 字符串的主密钥
    api_id: int                # my.telegram.org 申请
    api_hash: str
    web_token: str             # Web/API 访问令牌
    default_proxy: str | None  # 形如 socks5://user:pass@host:1080
    global_rate: float         # 每账号每秒最大请求数
    action_min_delay: float    # 批量动作最小间隔（秒）
    action_max_delay: float    # 批量动作最大间隔（秒）
    auto_kick_hours: float     # 本机接管满多少小时自动踢出其它设备（0=关闭）
    readonly: bool             # 只读模式：拒绝一切写入类工具
    dry_run: bool              # 干跑模式：写操作只返回预览
    readonly_token: str        # 只读令牌（给 Agent 使用）
    peer_allowlist: frozenset  # 发送对象白名单，为空则不限制
    # 清设备失败/校验未通过后隔多久重试（秒）。写法：45s / 10m / 2h
    kick_retry_s: float = 3600.0

    @staticmethod
    def load(env_file: str | os.PathLike[str] = ".env") -> "Settings":
        _load_dotenv(Path(env_file))
        data_dir = Path(os.environ.get("TAM_DATA_DIR", "./data")).expanduser()
        data_dir.mkdir(parents=True, exist_ok=True)
        master_key = os.environ.get("TAM_MASTER_KEY", "")
        if not master_key:
            raise RuntimeError(
                "缺少 TAM_MASTER_KEY。请先运行: python -m tam.cli init-key"
            )
        return Settings(
            data_dir=data_dir,
            db_path=data_dir / "tam.sqlite3",
            master_key=master_key,
            api_id=_int_env("TAM_API_ID", 0),
            api_hash=(os.environ.get("TAM_API_HASH") or "").strip(),
            web_token=(os.environ.get("TAM_WEB_TOKEN") or "").strip(),
            default_proxy=(os.environ.get("TAM_DEFAULT_PROXY") or "").strip() or None,
            global_rate=_float_env("TAM_RATE", 0.5),
            action_min_delay=_float_env("TAM_MIN_DELAY", 8.0),
            action_max_delay=_float_env("TAM_MAX_DELAY", 25.0),
            auto_kick_hours=_float_env("TAM_AUTO_KICK_HOURS", 24.0),
            kick_retry_s=_duration_env("TAM_KICK_RETRY", 3600.0, minimum=10.0),
            readonly=os.environ.get("TAM_READONLY", "0").lower() in {"1", "true", "yes"},
            dry_run=os.environ.get("TAM_DRY_RUN", "0").lower() in {"1", "true", "yes"},
            readonly_token=os.environ.get("TAM_READONLY_TOKEN", ""),
            peer_allowlist=frozenset(
                x.strip() for x in os.environ.get("TAM_PEER_ALLOWLIST", "").split(",") if x.strip()
            ),
        )
