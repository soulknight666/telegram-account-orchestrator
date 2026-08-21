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
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) tam/1.2"
_TAG = re.compile(r"<[^>]+>")
# 优先匹配带关键词的验证码，再回退到独立 5~6 位数字
KEYED = re.compile(
    r"(?:login\s*code|code|验证码|登录码|コード)\D{0,20}?(\d{5,6})", re.I)
LOOSE = re.compile(r"(?<!\d)(\d{5,6})(?!\d)")


def strip_html(raw: str) -> str:
    text = _TAG.sub(" ", raw)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _extract_json_code(value: object) -> str | None:
    if isinstance(value, dict):
        lowered = {str(key).lower(): item for key, item in value.items()}
        for key in ("code", "otp", "login_code", "verification_code"):
            item = lowered.get(key)
            if isinstance(item, (str, int)):
                match = LOOSE.search(str(item))
                if match:
                    return match.group(1)
        for key in ("sms", "message", "text", "content"):
            item = lowered.get(key)
            if isinstance(item, str):
                match = KEYED.search(item) or LOOSE.search(item)
                if match:
                    return match.group(1)
        for key in ("data", "result", "payload"):
            if key in lowered:
                code = _extract_json_code(lowered[key])
                if code:
                    return code
        return None
    if isinstance(value, list):
        for item in value:
            code = _extract_json_code(item)
            if code:
                return code
    return None


def extract_code(raw: str) -> str | None:
    """从页面文本中抽取验证码。若多个则取最前面的（平台通常最新在上）。"""
    if raw.lstrip().startswith(("{", "[")):
        try:
            return _extract_json_code(json.loads(raw))
        except json.JSONDecodeError:
            pass
    text = strip_html(raw)
    m = KEYED.search(text)
    if m:
        return m.group(1)
    m = LOOSE.search(text)
    return m.group(1) if m else None


def fetch_text(url: str, timeout: float = 15.0, proxy: str | None = None, *,
               method: str = "GET", headers: dict[str, str] | None = None) -> str:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    )
    request_headers = {"User-Agent": UA, "Accept": "*/*", **(headers or {})}
    req = urllib.request.Request(url, headers=request_headers, method=method)
    with opener.open(req, timeout=timeout) as resp:
        data = resp.read()
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "ignore")


def _monitor_urls(url: str, page: str) -> tuple[str, str] | None:
    """识别同源动态取码页，返回 latest/restart 两个端点。"""
    latest_path = "/client-login/latest-code?linkToken="
    restart_path = "/client-login/restart-monitor?linkToken="
    if latest_path not in page or restart_path not in page:
        return None
    parsed = urllib.parse.urlsplit(url)
    token = urllib.parse.parse_qs(parsed.query).get("linkToken", [""])[0]
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not token:
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}"
    query = urllib.parse.urlencode({"linkToken": token})
    return (
        f"{origin}/client-login/latest-code?{query}",
        f"{origin}/client-login/restart-monitor?{query}",
    )


@dataclass(frozen=True)
class CodeProvider:
    name: str
    label: str
    requires_prepare: bool = False

    def matches(self, url: str, page: str) -> bool:
        return True

    def read(self, url: str, page: str) -> str | None:
        return extract_code(page)

    async def prepare(self, url: str, page: str, timeout: float,
                      proxy: str | None) -> dict[str, object]:
        return {"prepared": False, "kind": self.name}


class TghaoProvider(CodeProvider):
    def __init__(self) -> None:
        super().__init__("tghao", "TGHao 动态监控", True)

    def matches(self, url: str, page: str) -> bool:
        return _monitor_urls(url, page) is not None

    async def prepare(self, url: str, page: str, timeout: float,
                      proxy: str | None) -> dict[str, object]:
        endpoints = _monitor_urls(url, page)
        if endpoints is None:
            raise RuntimeError("取码服务页面结构已变化，无法定位监控接口")

        _, restart_url = endpoints
        headers = {
            "X-Requested-With": "manual-start",
            "Cache-Control": "no-cache",
        }
        try:
            raw = await asyncio.to_thread(
                fetch_text,
                restart_url,
                timeout,
                proxy,
                method="POST",
                headers=headers,
            )
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {}
            remaining = payload.get("remaining")
            message = payload.get("message") or f"HTTP {exc.code}"
            if remaining:
                message = f"{message}（约 {remaining} 秒后可重试）"
            raise RuntimeError(f"取码平台启动监控失败：{message}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("取码平台启动监控失败：响应不是 JSON") from exc
        if not payload.get("success"):
            message = payload.get("message") or "平台未返回成功状态"
            raise RuntimeError(f"取码平台启动监控失败：{message}")
        return {
            "prepared": True,
            "kind": self.name,
            "poll_seconds": payload.get("poll_seconds") or payload.get("seconds"),
        }


class GenericProvider(CodeProvider):
    def __init__(self) -> None:
        super().__init__("generic", "通用直链", False)


PROVIDERS: tuple[CodeProvider, ...] = (TghaoProvider(), GenericProvider())


def detect_provider(url: str, page: str) -> CodeProvider:
    return next(provider for provider in PROVIDERS if provider.matches(url, page))


def list_providers() -> list[dict[str, object]]:
    return [
        {
            "name": provider.name,
            "label": provider.label,
            "requires_prepare": provider.requires_prepare,
        }
        for provider in PROVIDERS
    ]


def _response_type(raw: str) -> str:
    stripped = raw.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            json.loads(raw)
            return "json"
        except json.JSONDecodeError:
            pass
    if re.search(r"<!doctype\s+html|<html\b|<body\b", raw, re.I):
        return "html"
    return "text"


async def probe_code_source(url: str, timeout: float = 15.0,
                            proxy: str | None = None) -> dict[str, object]:
    """只读探测取码链接；不启动监控、不发送 Telegram 验证码、不返回验证码。"""
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("取码链接必须是有效的 HTTP/HTTPS URL")
    raw = await asyncio.to_thread(fetch_text, url, timeout, proxy)
    provider = detect_provider(url, raw)
    return {
        "ok": True,
        "provider": provider.name,
        "provider_label": provider.label,
        "response_type": _response_type(raw),
        "code_present": bool(provider.read(url, raw)),
        "requires_prepare": provider.requires_prepare,
    }


async def prepare_code_source(url: str, timeout: float = 15.0,
                              proxy: str | None = None) -> dict[str, object]:
    """在 Telegram 发码前按 provider 执行必要准备。"""
    page = await asyncio.to_thread(fetch_text, url, timeout, proxy)
    provider = detect_provider(url, page)
    return await provider.prepare(url, page, timeout, proxy)


async def read_code(url: str, timeout: float = 15.0, proxy: str | None = None) -> str | None:
    """拉一次，返回当前页面上的验证码（可能是旧码）。"""
    raw = await asyncio.to_thread(fetch_text, url, timeout, proxy)
    return detect_provider(url, raw).read(url, raw)


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
