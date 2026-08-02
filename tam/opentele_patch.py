"""opentele 与 Python 3.13 的兼容性修复。

背景：Python 3.13 给每个类新增了 ``__firstlineno__`` / ``__static_attributes__``
属性，opentele 的 ``extend_class``（utils.py）在校验时发现两边不一致，直接
``raise BaseException("err")``，导致 ``import opentele`` 彻底失败：

    File ".../opentele/utils.py", line 121, in __new__
        raise BaseException("err")
    BaseException: err

它抛的是 ``BaseException`` 而非 ``Exception``，所以普通的 ``except Exception``
根本拦不住。上游 issue #133 / #145 至今未修。

本模块直接改写已安装的 ``opentele/utils.py``：把那一句校验性的
``raise BaseException("err")`` 降级为放行（它只是个断言，不影响 tdata 解析）。
改写前会先备份为 ``utils.py.tam-bak``，可随时 ``revert()`` 还原。

宁愿不打补丁也不要静默失败：所有函数都返回结构化结果，供 CLI / API 展示。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

MARK = "# tam-patch: Python 3.13 兼容（原为 raise BaseException）"
BAK_SUFFIX = ".tam-bak"
_RAISE = re.compile(r'^(\s*)raise BaseException\(\s*["\']err["\']\s*\)\s*$', re.M)


def utils_path() -> Path | None:
    """定位已安装的 opentele/utils.py（不导入 opentele，避开崩溃）。"""
    import importlib.util

    try:
        spec = importlib.util.find_spec("opentele")
    except BaseException:
        return None
    if spec is None:
        return None
    roots = list(spec.submodule_search_locations or [])
    if not roots and spec.origin:
        roots = [str(Path(spec.origin).parent)]
    for r in roots:
        p = Path(r) / "utils.py"
        if p.exists():
            return p
    return None


def installed() -> bool:
    return utils_path() is not None


def patched() -> bool:
    p = utils_path()
    if p is None:
        return False
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return MARK in text or not _RAISE.search(text)


def needs_patch() -> bool:
    """仅在 Python ≥ 3.13 且尚未打补丁时为 True。"""
    return sys.version_info >= (3, 13) and installed() and not patched()


def apply_patch() -> dict[str, Any]:
    p = utils_path()
    if p is None:
        return {"ok": False, "error": "未找到已安装的 opentele，请先 pip install opentele"}
    text = p.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        return {"ok": True, "path": str(p), "changed": 0, "note": "已打过补丁"}
    if not _RAISE.search(text):
        return {"ok": True, "path": str(p), "changed": 0,
                "note": "未发现需要修复的代码，opentele 可能已官方修复"}

    bak = p.with_suffix(p.suffix + BAK_SUFFIX)
    if not bak.exists():
        bak.write_text(text, encoding="utf-8")

    new, n = _RAISE.subn(lambda m: f"{m.group(1)}pass  {MARK}", text)
    try:
        p.write_text(new, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"写入失败（权限不足？）：{exc}", "path": str(p)}
    return {"ok": True, "path": str(p), "backup": str(bak), "changed": n}


def revert() -> dict[str, Any]:
    p = utils_path()
    if p is None:
        return {"ok": False, "error": "未找到 opentele"}
    bak = p.with_suffix(p.suffix + BAK_SUFFIX)
    if not bak.exists():
        return {"ok": False, "error": "没有备份文件，无需或无法还原"}
    p.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
    return {"ok": True, "path": str(p), "restored_from": str(bak)}


def status() -> dict[str, Any]:
    p = utils_path()
    return {
        "python": "%d.%d.%d" % sys.version_info[:3],
        "opentele_installed": p is not None,
        "utils_path": str(p) if p else None,
        "patched": patched(),
        "needs_patch": needs_patch(),
    }


def ensure_patched(auto: bool = True) -> dict[str, Any]:
    """导入 opentele 前调用：若环境会撞就先自动修复。"""
    if not needs_patch():
        return {"ok": True, "applied": False, **status()}
    if not auto:
        return {"ok": False, "applied": False, **status()}
    res = apply_patch()
    return {"ok": bool(res.get("ok")), "applied": True, "detail": res, **status()}
