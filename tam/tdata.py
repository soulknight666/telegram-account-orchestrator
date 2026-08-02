r"""Telegram Desktop 的 tdata 目录导入。

tdata 是桌面端的本地加密目录（key_datas + D877F783D5D3EF8C* 分片，可带本地密码），
里面保存着 auth_key / dc_id。

解析优先用内置的纯 Python 解析器 :mod:`tam.tdata_native`（无需 opentele，
支持 TDesktop 6.x）；仅当它失败且环境里确实装了可用的 opentele 时才回退。
全程不在磁盘留下明文 .session 文件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .tdata_native import TDataError, read_tdata, tdata_string_sessions

KEY_FILE = "key_datas"
MAX_DEPTH = 3

__all__ = [
    "TDataError",
    "ensure_opentele",
    "find_tdata_dirs",
    "inspect_tdata",
    "is_tdata_dir",
    "tdata_to_sessions",
]


def is_tdata_dir(path: Path | str) -> bool:
    """判定一个目录是否是 tdata 根目录。"""
    p = Path(path)
    if not p.is_dir():
        return False
    if (p / KEY_FILE).exists():
        return True
    # 少数版本没有 key_datas，退而判断 D877F783D5D3EF8C 分片目录
    return any(c.name.startswith("D877F783D5D3EF8C") for c in p.iterdir())


def find_tdata_dirs(root: Path | str, max_depth: int = MAX_DEPTH) -> list[Path]:
    """在 root 下递归找出所有 tdata 目录（支持一个父目录放多份 tdata 的批量场景）。"""
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(f"路径不存在：{root}")
    if is_tdata_dir(root):
        return [root]
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(c for c in d.iterdir() if c.is_dir())
        except PermissionError:
            return
        for c in children:
            if is_tdata_dir(c):
                found.append(c)          # 命中后不再深入，避免把分片目录也算进去
            else:
                walk(c, depth + 1)

    walk(root, 1)
    return found


def inspect_tdata(path: Path | str, password: str | None = None) -> dict[str, Any]:
    """只诊断不导入：返回逐步骤报告（调试模式专用，永不抛异常）。"""
    return read_tdata(path, password).as_dict()


def ensure_opentele() -> None:
    """确保可选依赖 opentele 可用（仅回退路径使用，已非必需）。

    opentele 在 Python 3.13 上会抛 ``BaseException("err")``（上游 issue #133/#145），
    那不是 Exception 子类，会穿透普通的 except，所以这里统一接住并转成人话错误。
    """
    from . import opentele_patch

    def _try_import() -> BaseException | None:
        try:
            import opentele  # noqa: F401
        except BaseException as exc:  # noqa: BLE001 - opentele 确实会抛 BaseException
            return exc
        return None

    if opentele_patch.needs_patch():
        opentele_patch.apply_patch()

    exc = _try_import()
    if exc is None:
        return

    if isinstance(exc, ImportError):
        raise RuntimeError(
            "未安装可选依赖 opentele（已非必需，内置解析器已接管 tdata 导入）"
        ) from exc

    res = opentele_patch.apply_patch()
    if res.get("ok") and res.get("changed"):
        import importlib
        import sys as _sys

        for mod in [m for m in _sys.modules if m == "opentele" or m.startswith("opentele.")]:
            _sys.modules.pop(mod, None)
        importlib.invalidate_caches()
        if _try_import() is None:
            return

    raise RuntimeError(f"opentele 无法导入：{type(exc).__name__}: {exc}")


def _resolve_api(api_id: int | str | None, api_hash: str | None, use_desktop_api: bool) -> Any:
    from opentele.api import API, APIData

    if use_desktop_api or not api_id or not api_hash:
        # 沿用桌面端官方 API 指纹，风控最低
        return API.TelegramDesktop
    return APIData(api_id=int(api_id), api_hash=str(api_hash))


async def tdata_to_sessions(
    path: Path | str,
    api_id: int | str | None = None,
    api_hash: str | None = None,
    password: str | None = None,
    use_desktop_api: bool = True,
    debug: bool = False,
) -> list[str]:
    """把一个 tdata 目录里的全部账号转换成 StringSession 字符串列表。

    不落盘、不入库；复用桌面端已有授权，不会额外产生一次新登录
    （但桌面端与本工具会成为同一授权的两个使用方）。
    """
    path = Path(path)
    if not is_tdata_dir(path):
        raise TDataError(f"不是有效的 tdata 目录（应含 key_datas 文件）：{path}")

    sessions, report = tdata_string_sessions(path, password)
    if sessions:
        return sessions

    native_error = report.error or "内置解析器未能解出任何账号"
    detail = f"内置解析器：{native_error}"
    if debug:
        detail += "\n" + report.as_text()

    # 注意：绝不在服务进程里 import opentele。它会在导入时改写 Telethon 的类，
    # 在 Python 3.13 上会把方法包成自己调自己，后续建连接直接 RecursionError。
    raise TDataError(detail)
