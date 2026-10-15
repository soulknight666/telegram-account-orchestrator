"""纯函数批量预览工具。

只负责把后端已经判定好的可运行/跳过结果整理成稳定的 API 形状，
不连接 Telegram，也不修改数据库，方便脱机测试。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from .errors import normalize_reason_code


def build_preview(
    ids: Iterable[int],
    *,
    runnable: Iterable[int],
    reasons: Mapping[int, tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """返回 ``run`` 与带机器码/中文原因的 ``skip``。

    ``runnable`` 和 ``reasons`` 由现有操作的筛选逻辑提供，本函数不重新
    推断账号状态，因此预览不会产生第二套业务规则。
    """
    ordered = list(dict.fromkeys(int(x) for x in ids))
    run_set = set(int(x) for x in runnable)
    reasons = reasons or {}
    run = [aid for aid in ordered if aid in run_set]
    skip = []
    for aid in ordered:
        if aid in run_set:
            continue
        code, reason = reasons.get(aid, ("validation_error", "当前操作不满足执行条件"))
        skip.append({"id": aid, "reason_code": normalize_reason_code(code, reason), "reason": reason})
    return {"run": run, "skip": skip}
