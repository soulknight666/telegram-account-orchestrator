"""Spintax 文案变体引擎。

语法：`{你好|您好|hi}`，支持任意层嵌套 `{你好{呀|哇}|hi}`，
用反斜杠转义特殊字符：`\\{` `\\|` `\\}` `\\\\`。

为什么需要它：Telegram 的反垃圾会对"短时间内大量完全相同的文本"加权，
群发时每条走一次 expand()，可以让每条消息在字面上各不相同。

参考实现思路：AceLewis/spintax（递归 + 转义），这里不引第三方依赖，
改为一次线性扫描构建语法树，避免嵌套正则回溯。
"""
from __future__ import annotations

import random
from typing import Iterator

__all__ = ["expand", "count", "variants", "validate", "SpintaxError"]

_SPECIAL = "{}|\\"


class SpintaxError(ValueError):
    """括号不配对等语法错误。"""


# 语法树节点：
#   str            -> 字面量
#   list[Node]     -> 顺序拼接（Seq）
#   tuple[Node...] -> 多选一（Choice）
Node = object


def _parse(text: str, pos: int, depth: int) -> tuple[list[Node], int]:
    """解析一段顺序节点，遇到 `|` 或 `}`（depth>0 时）就返回。"""
    seq: list[Node] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            seq.append("".join(buf))
            buf.clear()

    n = len(text)
    while pos < n:
        ch = text[pos]
        if ch == "\\" and pos + 1 < n and text[pos + 1] in _SPECIAL:
            buf.append(text[pos + 1])
            pos += 2
            continue
        if ch == "{":
            flush()
            choice, pos = _parse_choice(text, pos + 1, depth + 1)
            seq.append(choice)
            continue
        # depth=0 时孤立的 } 当作普通字符（消息里常带 JSON / 颜文字）
        if depth > 0 and ch in "|}":
            break
        buf.append(ch)
        pos += 1
    flush()
    return seq, pos


def _parse_choice(text: str, pos: int, depth: int) -> tuple[tuple, int]:
    """解析 `{a|b|c}`，pos 指向 `{` 之后的第一个字符。"""
    options: list[list[Node]] = []
    while True:
        seq, pos = _parse(text, pos, depth)
        options.append(seq)
        if pos >= len(text):
            raise SpintaxError("花括号没有闭合：缺少 }")
        if text[pos] == "|":
            pos += 1
            continue
        if text[pos] == "}":
            return tuple(options), pos + 1
        raise SpintaxError(f"位置 {pos} 处出现意外字符 {text[pos]!r}")


def _compile(text: str) -> list[Node]:
    seq, pos = _parse(text, 0, 0)
    if pos != len(text):
        # 只有 depth=0 时读到孤立的 `}` 才会提前停下
        raise SpintaxError(f"位置 {pos} 处有多余的 }}，如需字面量请写 \\}}")
    return seq


def _render(seq: list[Node], rng: random.Random) -> str:
    out: list[str] = []
    for node in seq:
        if isinstance(node, str):
            out.append(node)
        else:  # Choice
            out.append(_render(rng.choice(node), rng))
    return "".join(out)


def _count(seq: list[Node]) -> int:
    total = 1
    for node in seq:
        if not isinstance(node, str):
            total *= sum(_count(opt) for opt in node)
    return total


def _iter_all(seq: list[Node]) -> Iterator[str]:
    if not seq:
        yield ""
        return
    head, rest = seq[0], seq[1:]
    heads: Iterator[str]
    if isinstance(head, str):
        heads = iter((head,))
    else:
        heads = (s for opt in head for s in _iter_all(opt))
    for h in heads:
        for tail in _iter_all(rest):
            yield h + tail


def _needs_parse(text: str) -> bool:
    """既无分支也无转义时走快路，避免白白解析一遍。"""
    return "{" in text or "\\" in text


def expand(text: str, rng: random.Random | None = None) -> str:
    """随机展开一条变体。没有 spintax 标记也没有转义时原样返回。"""
    if not _needs_parse(text):
        return text
    return _render(_compile(text), rng or random)


def count(text: str) -> int:
    """可生成的变体总数（用于提示"变体太少会被判重"）。"""
    if not _needs_parse(text):
        return 1
    return _count(_compile(text))


def variants(text: str, limit: int = 20) -> list[str]:
    """枚举前 limit 条变体，供 UI 预览。"""
    if not _needs_parse(text):
        return [text]
    out: list[str] = []
    for s in _iter_all(_compile(text)):
        out.append(s)
        if len(out) >= limit:
            break
    return out


def validate(text: str) -> dict:
    """给 UI/接口用的一次性校验 + 预览。"""
    try:
        total = count(text)
    except SpintaxError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "variants": total,
        "preview": variants(text, 5),
        "warning": "变体数过少，群发时仍可能被判定为重复内容" if total < 5 else None,
    }
