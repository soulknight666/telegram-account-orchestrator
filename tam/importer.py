r"""批量导入卡商/发卡平台给的初始账号行。

支持格式（每行一个账号）：
    +18129773632|https://tgapi.puonl.com/@cof333/8dc96736-.../GetHTML

容错：
- 分隔符可为 | 、\| 、｜、制表符、逗号、分号
- 允许 手机号|取码链接|备注 第三段（当作 label）
- 允许两段顺序颠倒（链接在前）
- 忽略空行与 # 开头的注释行；手机号自动补 +
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .db import Account, Database

SEP = re.compile(r"\s*(?:\\\||\||\uff5c|\t|,|;)\s*")
PHONE = re.compile(r"^\+?\d[\d\s\-()]{5,}$")


@dataclass
class ParsedAccount:
    phone: str
    code_url: str | None = None
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"phone": self.phone, "code_url": self.code_url, "label": self.label}


def parse_line(line: str) -> ParsedAccount | None:
    line = line.strip().lstrip("\ufeff")
    if not line or line.startswith("#"):
        return None
    parts = [p for p in SEP.split(line) if p]
    phone = code_url = label = None
    extras: list[str] = []
    for part in parts:
        if part.lower().startswith(("http://", "https://")):
            code_url = code_url or part
        elif PHONE.match(part):
            phone = phone or re.sub(r"[\s\-()]", "", part)
        else:
            extras.append(part)
    if not phone:
        raise ValueError(f"无法识别手机号：{line}")
    if not phone.startswith("+"):
        phone = "+" + phone
    if extras:
        label = extras[0]
    return ParsedAccount(phone=phone, code_url=code_url, label=label)


def parse_text(text: str) -> tuple[list[ParsedAccount], list[dict[str, str]]]:
    """返回（解析成功列表, 错误列表），单行出错不中断整批。"""
    ok: list[ParsedAccount] = []
    bad: list[dict[str, str]] = []
    for i, raw in enumerate(text.splitlines(), 1):
        try:
            item = parse_line(raw)
        except ValueError as exc:
            bad.append({"line": str(i), "raw": raw.strip(), "error": str(exc)})
            continue
        if item is not None:
            ok.append(item)
    return ok, bad


def import_accounts(db: Database, text: str, tags: Iterable[str] = (),
                    proxy: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """幂等导入：手机号已存在则只补写 code_url，不重复建号。"""
    items, bad = parse_text(text)
    tags = list(tags)
    added: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []

    existing = {a.phone: a for a in db.list() if a.phone}
    for item in items:
        old = existing.get(item.phone)
        if old is not None:
            if item.code_url and old.code_url != item.code_url:
                if not dry_run:
                    db.update(old.id, code_url=item.code_url)
                updated.append({"id": old.id, "phone": item.phone})
            continue
        label = item.label or item.phone
        n = 2
        while db.get_by_label(label):
            label, n = f"{item.label or item.phone}-{n}", n + 1
        if dry_run:
            added.append({"id": None, "phone": item.phone, "label": label})
            continue
        acc = db.add_account(Account(label=label, phone=item.phone,
                                     code_url=item.code_url, proxy=proxy, tags=tags))
        existing[item.phone] = acc
        added.append({"id": acc.id, "phone": acc.phone, "label": acc.label})

    return {"dry_run": dry_run, "parsed": len(items), "added": added,
            "updated": updated, "skipped": len(items) - len(added) - len(updated),
            "errors": bad}
