"""代理池体检。

行业常见坑（调研所得）：
1. 卖家把同一个 IP 切成多个端口冒充"1000 条住宅代理"，实际 1 proxy 应 = 1 unique IP；
2. 代理在在线检测工具里能通，到 Telethon 里却 timeout——因为真正要连的是
   Telegram 的 DC 而不是 HTTP 站点。所以这里直接拿 DC 地址做 TCP 採探。

不依赖 Telethon，只用 python-socks（已在 requirements 里）；未安装时降级为直连探测。
"""
from __future__ import annotations

import asyncio
import socket
import time
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

# Telegram 各 DC 入口（与 tdata_native 保持一致），探测默认用 DC2
DC_PROBE = ("149.154.167.51", 443)


def parse(url: str) -> dict[str, Any]:
    """socks5://user:pass@host:1080 -> 结构体（与 manager.parse_proxy 同语义）。"""
    p = urlparse(url.strip())
    scheme = (p.scheme or "socks5").lower()
    if scheme not in {"socks5", "socks4", "http"}:
        raise ValueError(f"不支持的代理协议: {scheme}")
    if not p.hostname:
        raise ValueError("代理地址缺少主机名")
    return {
        "scheme": scheme,
        "host": p.hostname,
        "port": p.port or (1080 if scheme.startswith("socks") else 8080),
        "username": p.username,
        "password": p.password,
    }


async def _resolve(host: str) -> str | None:
    """把代理主机名解析成 IP，用于去重（域名不同不代表 IP 不同）。"""
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, family=socket.AF_INET), timeout=5
        )
        return infos[0][4][0] if infos else None
    except Exception:
        return None


async def check_one(url: str, timeout: float = 10.0,
                    target: tuple[str, int] = DC_PROBE) -> dict[str, Any]:
    """单条代理：能否穿过它连到 Telegram DC，耗时多少。"""
    out: dict[str, Any] = {"proxy": url, "ok": False}
    try:
        cfg = parse(url)
    except ValueError as exc:
        out["error"] = str(exc)
        return out
    out["scheme"] = cfg["scheme"]
    out["host"] = cfg["host"]
    out["port"] = cfg["port"]
    out["ip"] = await _resolve(cfg["host"])

    started = time.monotonic()
    try:
        from python_socks.async_.asyncio import Proxy  # type: ignore

        proxy = Proxy.from_url(url)
        sock = await asyncio.wait_for(
            proxy.connect(dest_host=target[0], dest_port=target[1]), timeout=timeout
        )
        sock.close()
    except ImportError:
        out["error"] = "未安装 python-socks[asyncio]，无法探测代理"
        return out
    except asyncio.TimeoutError:
        out["error"] = f"超时 {timeout:g}s（代理不可达或被墙）"
        return out
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    out["ok"] = True
    out["latency_ms"] = round((time.monotonic() - started) * 1000)
    return out


def _annotate_duplicates(results: list[dict[str, Any]]) -> None:
    """标记出口 IP 重复的代理（卖家切池的典型特征）。"""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        if r.get("ip"):
            groups[r["ip"]].append(r)
    for r in results:
        r["duplicate_ip"] = False
        r["duplicate_count"] = 1 if r.get("ip") else 0
    for ip, items in groups.items():
        if len(items) > 1:
            for r in items:
                r["duplicate_ip"] = True
                r["duplicate_count"] = len(items)


async def check_many(urls: list[str], concurrency: int = 10,
                     timeout: float = 10.0) -> dict[str, Any]:
    """批量体检 + 去重分析。"""
    seen: set[str] = set()
    uniq = [u for u in (x.strip() for x in urls) if u and not (u in seen or seen.add(u))]
    sem = asyncio.Semaphore(max(1, concurrency))

    async def worker(u: str) -> dict[str, Any]:
        async with sem:
            return await check_one(u, timeout=timeout)

    results = await asyncio.gather(*(worker(u) for u in uniq))
    results = list(results)
    _annotate_duplicates(results)

    alive = [r for r in results if r["ok"]]
    dup = [r for r in results if r.get("duplicate_ip")]
    latencies = sorted(r["latency_ms"] for r in alive) if alive else []
    return {
        "total": len(results),
        "alive": len(alive),
        "dead": len(results) - len(alive),
        "unique_ips": len({r["ip"] for r in results if r.get("ip")}),
        "duplicate_ips": len(dup),
        "median_latency_ms": latencies[len(latencies) // 2] if latencies else None,
        "results": results,
    }


async def check_accounts(db: Any, default_proxy: str | None = None,
                         concurrency: int = 10) -> dict[str, Any]:
    """体检库里每个账号绑定的代理，并指出多号共用同一代理的情况。

    一号一代理是降关联的基本盘，多号共用一个出口等于主动告诉 Telegram 它们是一伙的。
    """
    accounts = db.list()
    by_proxy: dict[str, list[str]] = defaultdict(list)
    urls: list[str] = []
    no_proxy: list[str] = []
    for a in accounts:
        url = a.proxy or default_proxy
        if not url:
            no_proxy.append(a.label)
            continue
        by_proxy[url].append(a.label)
        urls.append(url)

    report = await check_many(urls, concurrency=concurrency) if urls else {
        "total": 0, "alive": 0, "dead": 0, "unique_ips": 0,
        "duplicate_ips": 0, "median_latency_ms": None, "results": [],
    }
    for r in report["results"]:
        labels = by_proxy.get(r["proxy"], [])
        r["accounts"] = labels
        if len(labels) > 1:
            r["shared_by_accounts"] = len(labels)
    report["accounts_without_proxy"] = no_proxy
    report["shared_proxies"] = [
        {"proxy": p, "accounts": ls} for p, ls in by_proxy.items() if len(ls) > 1
    ]
    return report
