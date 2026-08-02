"""注册时间内核：扫号包 -> 取注册日期 -> 按日期分类打包。

关于联网：原实现把每个号的 user_id + dc_id 发到第三方接口
（regtime.miha.uk，还带着写死的 uuid），并且用 TCPConnector(ssl=False)
关掉了证书校验。这等于把你的号库清单交给别人，还是明文可中断的。

所以这里把联网做成显式注入的 resolver：
- 不传 resolver = 完全离线，只用 json 里已有的日期字段，一个字节不外发；
- 要联网必须自己造一个 HttpResolver 并写明端点，且默认校验证书。
"""
from __future__ import annotations

import json
import os
import sqlite3
import zipfile
from pathlib import Path
from typing import Any, Callable

from .chaibao import (UnpackError, _cap_bytes, get_total_size,
                      resolve_workers, run_parallel, safe_extract)

__all__ = ["RegTimeError", "HttpResolver", "read_dc_id", "find_tdata",
           "scan_accounts", "regtime"]

# 日期要当目录名用，不能直接拿远端返回的字符串去拼路径
SAFE_DATE = "0123456789-_."


class RegTimeError(UnpackError):
    """注册时间处理失败。"""


def _safe_label(v: Any) -> str:
    """把任意返回值洗成安全的目录名。远端可能返回 ../.. 之类的东西。"""
    s = str(v or "").strip()
    out = "".join(ch for ch in s if ch.isalnum() or ch in SAFE_DATE)
    out = out.strip(".").strip()
    return out or "unknown"


def read_dc_id(session_path: str) -> int | None:
    """从 Telethon 的 .session（SQLite）里读 dc_id。"""
    if not session_path or not os.path.exists(session_path):
        return None
    conn = None
    try:
        # 只读打开，避免给别人的号包文件写入 -wal / -shm
        conn = sqlite3.connect(f"file:{session_path}?mode=ro", uri=True)
        row = conn.execute("SELECT dc_id FROM sessions LIMIT 1").fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001 - 不是合法 session 就当没有
        return None
    finally:
        if conn is not None:
            conn.close()


def find_tdata(dirpath: str) -> str | None:
    """同目录下找 tdata 文件夹（靠 key_datas / map 认）。

    输出结构是 <date>/<phone>/{json,session,tdata}，不能只打包 json 和
    session —— 那会把 tdata 整个弄丢，而且丢得静默无声。
    """
    cand = os.path.join(dirpath, "tdata")
    try:
        if os.path.isdir(cand):
            inner = os.listdir(cand)
            if any(f in inner for f in ("key_datas", "map")):
                return cand
        if os.path.basename(dirpath) == "tdata":
            inner = os.listdir(dirpath)
            if any(f in inner for f in ("key_datas", "map")):
                return dirpath
    except OSError:
        return None
    return None


def scan_accounts(root: str) -> list[dict[str, Any]]:
    """扫出每个号的 json / session / tdata / user_id / dc_id。"""
    out: list[dict[str, Any]] = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if not fn.endswith(".json"):
                continue
            jp = os.path.join(dirpath, fn)
            try:
                with open(jp, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:  # noqa: BLE001 - 坏 json 跳过，不要整批挂掉
                continue
            if not isinstance(data, dict):
                continue
            stem = os.path.splitext(fn)[0]
            sp = os.path.join(dirpath, stem + ".session")
            if not os.path.exists(sp):
                sp = None
            dc = read_dc_id(sp) if sp else None
            if dc is None:
                dc = data.get("dc_id")
            out.append({
                "json": jp,
                "session": sp,
                "tdata": find_tdata(dirpath),
                "name": stem,
                "phone": str(data.get("phone") or stem),
                "user_id": data.get("user_id"),
                "dc_id": dc,
                "local_date": data.get("date") or data.get("register_time")
                or data.get("registration_date"),
            })
    out.sort(key=lambda a: a["json"])
    return out


class HttpResolver:
    """联网查注册日期。必须显式构造才会发生网络请求。

    用标准库 urllib，不引入 aiohttp 依赖；默认校验证书（原实现关掉了）。
    """

    def __init__(self, endpoint: str, uuid: str = "", timeout: int = 10,
                 verify_ssl: bool = True) -> None:
        if not endpoint:
            raise RegTimeError("要联网查注册时间，必须显式指定端点")
        self.endpoint = endpoint
        self.uuid = uuid
        self.timeout = timeout
        self.verify_ssl = verify_ssl

    def __call__(self, user_id: Any, dc_id: Any) -> str | None:
        import ssl
        import urllib.request

        body = json.dumps({"uuid": self.uuid, "user_id": str(user_id),
                           "dc_id": str(dc_id)}).encode()
        req = urllib.request.Request(
            self.endpoint, data=body,
            headers={"Content-Type": "application/json"})
        ctx = None if self.verify_ssl else ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=ctx) as resp:
                if resp.status != 200:
                    return None
                return (json.loads(resp.read().decode()) or {}).get("date")
        except Exception:  # noqa: BLE001 - 单个号查失败不影响其他
            return None


def regtime(zip_path: str, out_zip: str,
            resolver: Callable[[Any, Any], str | None] | None = None,
            max_extract_mb: int | None = None,
            workers: int | None = None) -> dict[str, Any]:
    """主入口。resolver 为 None 则完全离线。

    :param workers: 并发查询数，不传则读 TAM_WORKERS（默认 4），传 0 = 不并发。
        原版这里是写死的 CONCURRENT_REQUESTS = 3，现在可调。

    返回 {total, resolved, unknown, groups, out, online, workers}。
    """
    import tempfile

    cap = _cap_bytes(max_extract_mb)
    out = Path(out_zip)
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        ex = os.path.join(tmp, "extracted")
        safe_extract(zip_path, ex)
        size = get_total_size(ex)
        if size > cap:
            raise RegTimeError(
                f"解压后太大（{size // 1024 // 1024}MB > {cap // 1024 // 1024}MB）"
            )

        accounts = scan_accounts(ex)
        if not accounts:
            raise RegTimeError("压缩包里没找到 .json 账号信息")

        def _one(a):
            date = a.get("local_date")
            if not date and resolver is not None and a.get("user_id") \
                    and a.get("dc_id") is not None:
                date = resolver(a["user_id"], a["dc_id"])
            return _safe_label(date)

        # 全流程里最值得并发的就是这一处：纯网络往返，不碰 zip。
        # 包里自带日期时 _one 压根不会发请求，并发也就白花几微秒。
        # run_parallel 保序，所以日期不会安到别的号头上。
        labels = run_parallel(_one, accounts, workers)
        resolved = 0
        for a, label in zip(accounts, labels):
            a["date"] = label
            if label != "unknown":
                resolved += 1

        groups: dict[str, int] = {}
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for a in accounts:
                d = a["date"]
                groups[d] = groups.get(d, 0) + 1
                base = f"{d}/{_safe_label(a['phone']) or a['name']}"
                zf.write(a["json"], f"{base}/{os.path.basename(a['json'])}")
                if a["session"]:
                    zf.write(a["session"],
                             f"{base}/{os.path.basename(a['session'])}")
                # tdata 是整个目录，得递归写进去；排序是为了结果可复现
                td = a.get("tdata")
                if td and os.path.isdir(td):
                    for r, _d, fs in os.walk(td):
                        for fn in sorted(fs):
                            fp = os.path.join(r, fn)
                            rel = os.path.relpath(fp, td)
                            zf.write(fp, f"{base}/tdata/{rel}")

        return {"total": len(accounts), "resolved": resolved,
                "unknown": len(accounts) - resolved, "groups": groups,
                "out": str(out), "online": resolver is not None,
                "workers": resolve_workers(workers)}
