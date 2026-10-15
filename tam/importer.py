r"""批量导入卡商/发卡平台给的初始账号行。

支持格式（每行一个账号）：
    +10000000000|https://example.invalid/code/sample/GetHTML

容错：
- 分隔符可为 | 、\| 、｜、制表符、逗号、分号
- 允许 手机号|取码链接|备注 第三段（当作 label）
- 允许两段顺序颠倒（链接在前）
- 允许 Markdown 表格、序号列、反引号/粗体/尖括号包裹
- 忽略空行与 # 开头的注释行；手机号自动补 +
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlsplit, urlunsplit
from dataclasses import dataclass
from typing import Any, Iterable

from .db import Account, Database

SEP = re.compile(r"\s*(?:\\\||\||\uff5c|\t|,|;|-{2,}\s*(?=\[?https?://))\s*")
PHONE = re.compile(r"^\+?\d[\d\s\-()]{5,}$")
TABLE_RULE = re.compile(r"^:?-{3,}:?$")
ORDINAL = re.compile(r"^(?:#\s*)?\d+[.)]?$", re.I)

def normalize_proxy(value: str | None) -> str | None:
    """Canonicalize proxy spelling so re-uploaded CSV nodes match existing ones."""
    if not value: return None
    node = str(value).strip().strip('"').strip()
    parts = node.split(':', 3)
    if len(parts) == 4 and parts[1].isdigit() and '://' not in node:
        host, port, user, secret = parts
        node = f"socks5://{quote(user, safe='')}:{quote(secret, safe='')}@{host}:{port}"
    if "://" not in node and re.fullmatch(r"(?:\[[^\]]+\]|[^:\s]+):\d+", node):
        node = "socks5://" + node
    try:
        p = urlsplit(node)
        if p.scheme and p.hostname:
            host = p.hostname.lower()
            netloc = f"[{host}]" if ":" in host else host
            if p.port: netloc += f':{p.port}'
            if p.username:
                # urlsplit exposes already percent-escaped credentials; quote
                # only raw delimiters to keep normalization idempotent.
                from urllib.parse import unquote
                user = quote(unquote(p.username), safe='')
                pwd = quote(unquote(p.password or ''), safe='')
                netloc = f"{user}:{pwd}@{netloc}"
            return urlunsplit((p.scheme.lower(), netloc, '', '', ''))
    except ValueError: pass
    return node


@dataclass
class ParsedAccount:
    phone: str
    code_url: str | None = None
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"phone": self.phone, "code_url": self.code_url, "label": self.label}


def _clean_part(part: str) -> str:
    part = part.strip()
    if len(part) >= 2 and part.startswith("`") and part.endswith("`"):
        part = part.strip("`").strip()
    if len(part) >= 4 and (
        (part.startswith("**") and part.endswith("**"))
        or (part.startswith("__") and part.endswith("__"))
    ):
        part = part[2:-2].strip()
    if len(part) >= 2 and part.startswith("<") and part.endswith(">"):
        part = part[1:-1].strip()
    if part.startswith("["):
        part = part[1:].strip()
    if part.endswith("]"):
        part = part[:-1].strip()
    return part


def _is_table_meta(parts: list[str]) -> bool:
    if parts and all(TABLE_RULE.fullmatch(part.replace(" ", "")) for part in parts):
        return True
    names = {re.sub(r"[\s_/-]", "", part).lower() for part in parts}
    phone_names = {"phone", "phonenumber", "mobile", "手机号", "号码"}
    url_names = {"url", "link", "codeurl", "取码链接", "链接"}
    return bool(names & phone_names) and bool(names & url_names)


def parse_line(line: str) -> ParsedAccount | None:
    line = line.strip().lstrip("\ufeff")
    if not line or line.startswith("#"):
        return None
    parts = [_clean_part(p) for p in SEP.split(line)]
    parts = [p for p in parts if p]
    if _is_table_meta(parts):
        return None
    phone = code_url = label = None
    extras: list[str] = []
    for part in parts:
        if part.lower().startswith(("http://", "https://")):
            code_url = code_url or part
        elif PHONE.fullmatch(part) and 7 <= len(re.sub(r"\D", "", part)) <= 15:
            phone = phone or re.sub(r"[\s\-()]", "", part)
        elif ORDINAL.fullmatch(part):
            continue
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

def _phone_key(value: str | None) -> str:
    """Compare phone numbers independent of Telegram's optional leading '+'."""
    return re.sub(r"\D", "", str(value or ""))


def import_accounts(db: Database, text: str, tags: Iterable[str] = (),
                    proxy: str | None = None, dry_run: bool = False,
                    proxy_pool: Iterable[str] = (), links_per_proxy: int = 0,
                    default_proxy: str | None = None) -> dict[str, Any]:
    """幂等导入：手机号已存在则只补写 code_url，不重复建号。"""
    items, bad = parse_text(text)
    parsed_input_count = len(items)
    # 同一批次重复手机号只处理一次，避免虚增节点容量计数。
    unique_items = []
    duplicates: list[dict[str, Any]] = []
    seen = set()
    for item in items:
        if item.phone in seen:
            duplicates.append({"phone": item.phone, "reason": "duplicate_in_batch"})
            continue
        seen.add(item.phone); unique_items.append(item)
    items = unique_items
    tags = list(tags)
    added: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    existing_skipped: list[dict[str, Any]] = []

    all_existing = db.list()
    existing = {_phone_key(a.phone): a for a in all_existing if a.phone}
    used_labels = {a.label for a in all_existing if a.label}
    pool = []
    for value in proxy_pool:
        node = normalize_proxy(value)
        # 兼容常见代理商 CSV：host:port:user:password；默认转换为 SOCKS5。
        parts = node.split(":", 3)
        if len(parts) == 4 and parts[1].isdigit() and "://" not in node:
            host, port, user, secret = parts
            node = f"socks5://{quote(user, safe='')}:{quote(secret, safe='')}@{host}:{port}"
        if node and node not in pool:
            pool.append(node)
    # 显式节点池优先；proxy 仅作为无节点池时的单一回退节点。
    # 这样批量导入不会把回退代理意外加入容量池。
    if proxy and not pool:
        pool.append(proxy)
    if links_per_proxy < 0:
        raise ValueError("links_per_proxy 不能为负数")
    # 全局容量：统计数据库内现有账号，并在试运行时模拟占用。
    usage = {node: sum(1 for a in all_existing
                        if normalize_proxy(a.proxy or default_proxy) == node) for node in pool}
    assignment_index = 0
    capacity_blocked = 0
    pending_accounts: list[Account] = []
    for item in items:
        old = existing.get(_phone_key(item.phone))
        if old is not None:
            if item.code_url and old.code_url != item.code_url:
                if not dry_run:
                    db.update(old.id, code_url=item.code_url)
                updated.append({"id": old.id, "phone": item.phone})
            else:
                existing_skipped.append({"id": old.id, "phone": item.phone, "reason": "already_exists"})
            continue
        label = item.label or item.phone
        n = 2
        while label in used_labels:
            label, n = f"{item.label or item.phone}-{n}", n + 1
        used_labels.add(label)
        if pool and links_per_proxy:
            candidates = [node for node in pool if usage.get(node, 0) < links_per_proxy]
            if not candidates:
                capacity_blocked += 1
                continue
            assigned_proxy = candidates[assignment_index % len(candidates)]
            assignment_index += 1
            usage[assigned_proxy] = usage.get(assigned_proxy, 0) + 1
        else:
            assigned_proxy = (pool[assignment_index % len(pool)] if pool else (proxy or default_proxy))
            if assigned_proxy in usage:
                usage[assigned_proxy] += 1
            assignment_index += 1
        if dry_run:
            added.append({"id": None, "phone": item.phone, "label": label})
            continue
        acc = Account(label=label, phone=item.phone, code_url=item.code_url,
                      proxy=assigned_proxy, tags=tags)
        pending_accounts.append(acc)
        existing[_phone_key(item.phone)] = acc
        added.append({"id": None, "phone": acc.phone, "label": acc.label})
    if pending_accounts and not dry_run:
        db.add_accounts_bulk(pending_accounts)
        by_label = {a.label: a for a in pending_accounts}
        for row in added:
            acc = by_label.get(row["label"])
            if acc: row["id"] = acc.id

    return {"dry_run": dry_run, "parsed": len(items), "parsed_input": parsed_input_count,
            "added": added,
            "updated": updated, "skipped": len(items) - len(added) - len(updated),
            "duplicates": duplicates, "existing": existing_skipped,
            "errors": bad, "proxy_pool_size": len(pool),
            "links_per_proxy": links_per_proxy, "capacity_blocked": capacity_blocked,
            "proxy_usage": [{"proxy": node, "used": usage.get(node, 0), "limit": links_per_proxy}
                            for node in pool]}
