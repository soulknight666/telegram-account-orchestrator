"""从发卡平台的取码链接（如 .../GetHTML）拉取 Telegram 登录验证码。

链接返回的是一页 HTML（或 JSON/纯文本），里面含最近收到的短信/官方消息。
本模块只用标准库（urllib），不引入额外依赖；阻塞读取放到线程里执行。

设计要点：
- 先取基线（send_code 之前页面上已有的旧码），避免把上一次的验证码当成新码。
- 轮询至出现新码或超时。
"""
from __future__ import annotations

import asyncio
import html
import re
import time
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) tam/1.2"
_TAG = re.compile(r"<[^>]+>")
# 优先匹配带关键词的验证码，再回退到独立 5~6 位数字
KEYED = re.compile(
    r"(?:login\s*code|code|验证码|登录码|コード)\D{0,20}?(\d{5,6})", re.I)
LOOSE = re.compile(r"(?<!\d)(\d{5,6})(?!\d)")


def strip_html(raw: str) -> str:
    text = _TAG.sub(" ", raw)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def extract_code(raw: str) -> str | None:
    """从页面文本中抽取验证码。若多个则取最前面的（平台通常最新在上）。"""
    text = strip_html(raw)
    m = KEYED.search(text)
    if m:
        return m.group(1)
    m = LOOSE.search(text)
    return m.group(1) if m else None


def fetch_text(url: str, timeout: float = 15.0, proxy: str | None = None) -> str:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with opener.open(req, timeout=timeout) as resp:
        data = resp.read()
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "ignore")


async def read_code(url: str, timeout: float = 15.0, proxy: str | None = None) -> str | None:
    """拉一次，返回当前页面上的验证码（可能是旧码）。"""
    raw = await asyncio.to_thread(fetch_text, url, timeout, proxy)
    return extract_code(raw)


async def wait_for_code(url: str, exclude: str | None = None, timeout: float = 120.0,
                        interval: float = 5.0, proxy: str | None = None) -> str:
    """轮询直到出现与 exclude 不同的新验证码。超时抛 TimeoutError。"""
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            code = await read_code(url, proxy=proxy)
            if code and code != exclude:
                return code
        except Exception as exc:  # 网络抖动不中断轮询
            last_err = exc
        await asyncio.sleep(interval)
    raise TimeoutError(
        f"{timeout:.0f}s 内未从取码链接获得新验证码"
        + (f"（最后错误：{last_err!r}）" if last_err else "")
    )
