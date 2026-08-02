"""整合内核：把多个号包合并成一个。

纯本地文件操作，不认识 Telegram。
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Any

from .chaibao import (UnpackError, _cap_bytes, find_sessions, get_total_size,
                      resolve_workers, run_parallel, safe_extract)

__all__ = ["MergeError", "merge", "plan_merge"]


class MergeError(UnpackError):
    """整合失败。"""


def _unique_name(taken: set[str], stem: str) -> str:
    """同名时改名，而不是让后来的盖掉前面的。

    原实现把 base_name 当字典键用，两个包里都有 acc01.session 时
    后一个会静静覆盖前一个，号就这么没了，而且总数还显示对的。
    """
    if stem not in taken:
        taken.add(stem)
        return stem
    i = 2
    while f"{stem}_{i}" in taken:
        i += 1
    new = f"{stem}_{i}"
    taken.add(new)
    return new


def plan_merge(sessions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """给每个 session 定一个不冲突的最终名字。返回（条目, 重名个数）。"""
    taken: set[str] = set()
    items: list[dict[str, Any]] = []
    renamed = 0
    for s in sessions:
        final = _unique_name(taken, s["name"])
        if final != s["name"]:
            renamed += 1
        items.append({**s, "final": final})
    return items, renamed


def merge(zip_paths: list[str | Path], out_zip: str | Path,
          max_extract_mb: int | None = None,
          workers: int | None = None) -> dict[str, Any]:
    """把多个 zip 合成一个。

    :param workers: 并发解压数，不传则读 TAM_WORKERS（默认 4），传 0 = 不并发
    :return: {total, renamed, sources, skipped, out, workers}
    """
    import tempfile

    if not zip_paths:
        raise MergeError("没有待整合的文件")

    cap = _cap_bytes(max_extract_mb)
    out_zip = Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "extracted"
        skipped: list[dict[str, str]] = []

        def _extract_one(job):
            zp, sub = job
            try:
                safe_extract(zp, sub)
                return None
            except Exception as exc:  # noqa: BLE001 - 单个包坏不该让整批失败
                return {"file": os.path.basename(str(zp)), "reason": str(exc)}

        # 每个包解到自己的子目录，否则不同包里的同名文件
        # 在解压阶段就互相覆盖了，根本轮不到后面改名。
        # 分开目录之后才可以安全并发（往同一目录并发解压会出问题）。
        jobs = [(zp, root / f"src{i:03d}") for i, zp in enumerate(zip_paths)]
        # run_parallel 严格保序，所以 skipped 的顺序跟输入顺序一致
        for r in run_parallel(_extract_one, jobs, workers):
            if r:
                skipped.append(r)

        size = get_total_size(root) if root.exists() else 0
        if size > cap:
            raise MergeError(
                f"解压后太大（{size // 1024 // 1024}MB > {cap // 1024 // 1024}MB）"
            )

        sessions = find_sessions(root) if root.exists() else []
        if not sessions:
            raise MergeError("所有包里都没找到 .session 文件")

        items, renamed = plan_merge(sessions)

        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for it in items:
                zf.write(it["session"], f"{it['final']}.session")
                if it["json"]:
                    zf.write(it["json"], f"{it['final']}.json")

        return {
            "total": len(items),
            "renamed": renamed,
            "sources": len(zip_paths) - len(skipped),
            "skipped": skipped,
            "out": str(out_zip),
            "workers": resolve_workers(workers),
        }
