"""FastAPI 服务：REST 接口 + 内置 Web 控制台。

安全：
- 默认只监听 127.0.0.1；
- 所有 /api/* 请求需携带 Authorization: Bearer <TAM_WEB_TOKEN>；
- 接口返回体默认不含 session 密文；显式调用 /export 接口时除外（仅人工、需鉴权）。
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Header, Request
from fastapi import Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .config import Settings
from .db import Account, Database
from .leads import LeadStore
from .manager import AccountManager
from .tasks import TaskRunner, TaskStore
from .tools import ToolContext, call_tool, list_tools
from . import toolbox

settings = Settings.load()
db = Database(settings.db_path)
manager = AccountManager(settings, db)
tasks = TaskStore(db.conn)
leads = LeadStore(db.conn)
runner = TaskRunner(tasks)
app = FastAPI(
    title="Telegram 账号管理器",
    version="1.8",
    description=(
        "自托管多账号管理。Agent 接入请优先用 /api/tools 与 /api/tools/call；"
        "OpenAPI 规格在 /openapi.json，调试页在 /docs。"
    ),
)


_bg_tasks: list[asyncio.Task[Any]] = []


@app.on_event("startup")
async def _start_background() -> None:
    """启动登录满 N 小时自动清设备的后台循环。"""
    from . import autokick

    if settings.auto_kick_hours > 0 and not settings.readonly:
        _bg_tasks.append(asyncio.create_task(autokick.loop(manager)))


@app.on_event("shutdown")
async def _stop_background() -> None:
    for t in _bg_tasks:
        t.cancel()
    _bg_tasks.clear()


def _append_error_file(line: str) -> None:
    """额外写一份滚动文件日志，DB 挂了也能留痕。"""
    try:
        log_dir = Path(settings.data_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        fp = log_dir / "errors.log"
        with open(fp, "a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
        # 简单截断：超过约 2MB 留尾部
        if fp.stat().st_size > 2_000_000:
            data = fp.read_text(encoding="utf-8", errors="ignore")
            fp.write_text(data[-1_000_000:], encoding="utf-8")
    except Exception:
        pass


def record_error(
    message: str,
    *,
    level: str = "error",
    source: str = "server",
    path: str | None = None,
    traceback_text: str | None = None,
    meta: dict[str, Any] | None = None,
) -> int | None:
    """统一写入错误事件（DB + 文件）。"""
    import datetime as _dt

    eid = None
    try:
        eid = db.add_error(
            message,
            level=level,
            source=source,
            path=path,
            traceback=traceback_text,
            meta=meta,
        )
    except Exception:
        eid = None
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _append_error_file(
        f"[{ts}] [{level}] [{source}] {path or '-'} | {message}"
        + (f"\n{traceback_text}" if traceback_text else "")
    )
    return eid


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常：记入错误日志并回传可读原因。"""
    import traceback

    if isinstance(exc, HTTPException):
        # FastAPI 仍可能走到这里；4xx 不记为系统错误
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    tb = traceback.format_exc()
    tb_lines = tb.strip().splitlines()
    eid = record_error(
        f"{type(exc).__name__}: {exc}",
        level="error",
        source="server",
        path=str(request.url.path),
        traceback_text=tb,
        meta={
            "method": request.method,
            "query": str(request.url.query)[:300],
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"{type(exc).__name__}: {exc}",
            "where": request.url.path,
            "trace": tb_lines[-8:],
            "error_id": eid,
        },
    )


@app.middleware("http")
async def error_log_middleware(request: Request, call_next):
    """记录 5xx 响应与处理耗时异常。"""
    t0 = time.time()
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        # 交给 exception_handler；这里再兜一层
        raise
    # 5xx 且非已由 handler 写过的路径仍记一条简报
    if response.status_code >= 500 and not request.url.path.startswith("/api/system/errors"):
        try:
            record_error(
                f"HTTP {response.status_code} {request.method} {request.url.path}",
                level="error",
                source="server",
                path=str(request.url.path),
                meta={"status": response.status_code, "ms": int((time.time() - t0) * 1000)},
            )
        except Exception:
            pass
    return response


_restart_lock = False


async def auth(authorization: str = Header(default="")) -> None:
    """完整权限。"""
    if not settings.web_token:
        return
    if authorization != f"Bearer {settings.web_token}":
        raise HTTPException(status_code=401, detail="unauthorized")


async def auth_scoped(authorization: str = Header(default="")) -> bool:
    """返回是否为只读会话。只读令牌专供 Agent 使用。"""
    if not settings.web_token and not settings.readonly_token:
        return settings.readonly
    if settings.web_token and authorization == f"Bearer {settings.web_token}":
        return settings.readonly
    if settings.readonly_token and authorization == f"Bearer {settings.readonly_token}":
        return True
    raise HTTPException(status_code=401, detail="unauthorized")


class AccountIn(BaseModel):
    label: str
    phone: str | None = None
    proxy: str | None = None
    device_model: str | None = None
    app_version: str | None = None
    system_version: str | None = None
    lang_code: str = "zh"
    tags: list[str] = []


class CodeIn(BaseModel):
    code: str
    password: str | None = None


class SessionIn(BaseModel):
    session: str


class MessageIn(BaseModel):
    peer: str
    text: str


class BatchIn(BaseModel):
    account_ids: list[int] = []
    tag: str | None = None
    concurrency: int = 3


class BatchMessageIn(BatchIn):
    peer: str
    text: str
    spintax: bool = True
    healthy_only: bool = True


class SpintaxIn(BaseModel):
    text: str


class ProxyCheckIn(BaseModel):
    urls: list[str] = []
    concurrency: int = 10
    timeout: float = 10.0


class WarmupIn(BatchIn):
    online: bool = True
    read: bool = True
    chat: bool = True
    rounds: int = 1


def _resolve(ids: list[int], tag: str | None) -> list[int]:
    if ids:
        return ids
    return [a.id for a in db.list(tag=tag) if a.id is not None]


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """浏览器会自动请求图标，没路由就会刷 404 日志。直接回一个内联 SVG。"""
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        b'<rect width="64" height="64" rx="14" fill="#2783DE"/>'
        b'<path d="M14 32.5 47 19l-5 27-9.5-8-5.5 5-1-8.5z" fill="#fff"/></svg>'
    )
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")


@app.get("/api/accounts", dependencies=[Depends(auth)])
async def list_accounts(status: str | None = None, tag: str | None = None) -> list[dict[str, Any]]:
    """账号列表。顺便带上自动清设备的计划（kick）。

    不在列表里下发明文二验密码（防令牌泄露扫库）；仅标记 has_twofa_saved。
    点眼睛时走 GET /api/accounts/{id}/twofa 按需揭密。
    """
    from . import autokick

    now = time.time()
    hrs = settings.auto_kick_hours
    return [{**a.public(), "kick": autokick.plan(a, hrs, now), "server_now": now}
            for a in db.list(status=status, tag=tag)]



class TwofaSaveIn(BaseModel):
    password: str | None = None  # 空字符串或 null = 清除已保存密码


@app.get("/api/accounts/{account_id}/twofa", dependencies=[Depends(auth)])
async def reveal_twofa(account_id: int) -> dict[str, Any]:
    """按需揭密：仅在用户点击眼睛时调用，不进列表。"""
    from .crypto import decrypt

    acc = db.get(account_id)
    if acc is None:
        raise HTTPException(404, "账号不存在")
    if not acc.twofa_enc:
        return {"ok": True, "id": account_id, "has_2fa": acc.has_2fa,
                "saved": False, "twofa": ""}
    try:
        plain = decrypt(settings.master_key, acc.twofa_enc)
    except Exception as exc:
        raise HTTPException(500, f"二验密码解密失败：{exc}") from exc
    return {"ok": True, "id": account_id, "has_2fa": acc.has_2fa,
            "saved": True, "twofa": plain}


@app.post("/api/accounts/{account_id}/twofa", dependencies=[Depends(auth)])
async def save_twofa(account_id: int, body: TwofaSaveIn) -> dict[str, Any]:
    """补录/更新/清除库内保存的二验密码（不调用 Telegram，只写本地）。"""
    from .crypto import encrypt

    acc = db.get(account_id)
    if acc is None:
        raise HTTPException(404, "账号不存在")
    pwd = (body.password or "").strip()
    if not pwd:
        db.update(account_id, twofa_enc=None)
        return {"ok": True, "id": account_id, "saved": False, "has_2fa": acc.has_2fa}
    db.update(
        account_id,
        twofa_enc=encrypt(settings.master_key, pwd),
        has_2fa=1 if acc.has_2fa is None else acc.has_2fa,
    )
    # 补录时若尚未检查过，默认记为「有二验」
    if acc.has_2fa is None:
        db.update(account_id, has_2fa=1)
    db.log(account_id, "twofa_save", True, "local")
    return {"ok": True, "id": account_id, "saved": True, "has_2fa": 1}


@app.post("/api/accounts", dependencies=[Depends(auth)])
async def create_account(body: AccountIn) -> dict[str, Any]:
    if db.get_by_label(body.label):
        raise HTTPException(409, "别名已存在")
    acc = db.add_account(Account(**body.model_dump()))
    return acc.public()


@app.patch("/api/accounts/{account_id}", dependencies=[Depends(auth)])
async def patch_account(account_id: int, body: dict[str, Any]) -> dict[str, Any]:
    allowed = {"label", "phone", "proxy", "code_url", "device_model", "app_version",
               "system_version", "lang_code", "tags", "status_note", "auto_kick", "auto_kick_hours", "auto_kick_loop"}
    if "auto_kick" in body:
        body["auto_kick"] = 1 if body["auto_kick"] else 0
    if "auto_kick_loop" in body:
        body["auto_kick_loop"] = 1 if body["auto_kick_loop"] else 0
    if "auto_kick_hours" in body:
        v = body["auto_kick_hours"]
        if v is None or v == "" or v == 0:
            body["auto_kick_hours"] = None
        else:
            try:
                body["auto_kick_hours"] = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, "auto_kick_hours 须为数字（小时）")
    db.update(account_id, **{k: v for k, v in body.items() if k in allowed})
    acc = db.get(account_id)
    if acc is None:
        raise HTTPException(404, "not found")
    return acc.public()


@app.delete("/api/accounts/{account_id}", dependencies=[Depends(auth)])
async def delete_account(account_id: int) -> dict[str, Any]:
    db.delete(account_id)
    return {"ok": True}


@app.post("/api/accounts/{account_id}/login/code", dependencies=[Depends(auth)])
async def login_code(account_id: int) -> dict[str, Any]:
    return await manager.send_code(account_id)


@app.post("/api/accounts/{account_id}/login/verify", dependencies=[Depends(auth)])
async def login_verify(account_id: int, body: CodeIn) -> dict[str, Any]:
    return await manager.sign_in(account_id, body.code, body.password)


class QrWaitIn(BaseModel):
    password: str | None = None
    timeout: float = 55.0


@app.post("/api/accounts/{account_id}/login/qr", dependencies=[Depends(auth)])
async def login_qr_start(account_id: int) -> dict[str, Any]:
    """发起扫码登录，返回二维码 PNG（base64）与 tg:// URL。"""
    try:
        return await manager.qr_login_start(account_id)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/accounts/{account_id}/login/qr/wait", dependencies=[Depends(auth)])
async def login_qr_wait(account_id: int, body: QrWaitIn | None = None) -> dict[str, Any]:
    """等待扫码完成（可轮询）。超时返回 pending；需 2FA 时返回 need_password。"""
    body = body or QrWaitIn()
    try:
        return await manager.qr_login_wait(
            account_id, password=body.password, timeout=body.timeout,
        )
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/accounts/{account_id}/login/qr", dependencies=[Depends(auth)])
async def login_qr_cancel(account_id: int) -> dict[str, Any]:
    return await manager.qr_login_cancel(account_id)


@app.post("/api/accounts/{account_id}/session/import", dependencies=[Depends(auth)])
async def import_session(account_id: int, body: SessionIn) -> dict[str, Any]:
    return await manager.import_session(account_id, body.session)


@app.post("/api/accounts/{account_id}/logout", dependencies=[Depends(auth)])
async def logout(account_id: int) -> dict[str, Any]:
    try:
        return await manager.logout(account_id)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


class DeleteTelegramIn(BaseModel):
    reason: str = "User requested deletion"
    password: str | None = None
    purge_local: bool = True
    confirm: bool = False


@app.post("/api/accounts/{account_id}/delete-telegram", dependencies=[Depends(auth)])
async def delete_telegram(account_id: int, body: DeleteTelegramIn) -> dict[str, Any]:
    """向 Telegram 申请注销账号（真正删号，不可逆）。"""
    if not body.confirm:
        raise HTTPException(400, "请设置 confirm=true 以确认永久注销")
    try:
        return await manager.delete_telegram_account(
            account_id,
            reason=body.reason,
            password=body.password,
            purge_local=body.purge_local,
        )
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/accounts/{account_id}/check", dependencies=[Depends(auth)])
async def check(account_id: int) -> dict[str, Any]:
    return await manager.health_check(account_id)


@app.get("/api/accounts/{account_id}/dialogs", dependencies=[Depends(auth)])
async def dialogs(account_id: int, limit: int = 30) -> list[dict[str, Any]]:
    try:
        return await manager.get_dialogs(account_id, limit)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/accounts/{account_id}/devices", dependencies=[Depends(auth)])
async def devices(account_id: int) -> list[dict[str, Any]]:
    try:
        return await manager.list_sessions(account_id)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/accounts/{account_id}/devices/terminate", dependencies=[Depends(auth)])
async def terminate(account_id: int) -> dict[str, Any]:
    try:
        return await manager.terminate_other_sessions(account_id)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/accounts/{account_id}/message", dependencies=[Depends(auth)])
async def message(account_id: int, body: MessageIn) -> dict[str, Any]:
    try:
        return await manager.send_message(account_id, body.peer, body.text)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/accounts/{account_id}/spam-check", dependencies=[Depends(auth)])
async def spam_check(account_id: int) -> dict[str, Any]:
    """与 @SpamBot 对话，判定该号是否被限制。"""
    try:
        return await manager.check_spam_status(account_id)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


class OkpayBalanceIn(BaseModel):
    bot: str = "Okpay"   # 钱包机器人用户名，默认 @Okpay
    wait: float = 4.0


@app.post("/api/accounts/{account_id}/okpay-balance", dependencies=[Depends(auth)])
async def okpay_balance(account_id: int, body: OkpayBalanceIn | None = None) -> dict[str, Any]:
    """用该号私聊 OKPay 钱包机器人查询余额（类似限制检测）。"""
    body = body or OkpayBalanceIn()
    try:
        return await manager.check_okpay_balance(
            account_id, bot=body.bot, wait=body.wait)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


class BatchOkpayIn(BaseModel):
    ids: list[int]
    bot: str = "Okpay"
    wait: float = 4.0
    concurrency: int = 2


@app.post("/api/accounts/okpay-balance", dependencies=[Depends(auth)])
async def okpay_balance_batch(body: BatchOkpayIn) -> dict[str, Any]:
    """批量查 OKPay 余额。"""
    if not body.ids:
        raise HTTPException(400, "请选择账号")
    sem = asyncio.Semaphore(max(1, min(int(body.concurrency or 2), 5)))
    items: list[dict[str, Any]] = []

    async def one(aid: int) -> None:
        async with sem:
            try:
                r = await manager.check_okpay_balance(
                    aid, bot=body.bot, wait=body.wait)
                items.append(r)
            except Exception as exc:  # noqa: BLE001
                items.append({"ok": False, "account_id": aid, "error": str(exc)})

    await asyncio.gather(*(one(i) for i in body.ids))
    ok_n = sum(1 for x in items if x.get("ok"))
    return {"ok": True, "total": len(items), "succeeded": ok_n,
            "failed": len(items) - ok_n, "items": items}


class BatchTerminateIn(BaseModel):
    ids: list[int]


@app.post("/api/accounts/devices/terminate", dependencies=[Depends(auth)])
async def terminate_batch(body: BatchTerminateIn) -> dict[str, Any]:
    """批量踢掉选中账号的其它登录设备。"""
    if not body.ids:
        raise HTTPException(400, "请选择账号")
    items = []
    for aid in body.ids:
        try:
            r = await manager.terminate_other_sessions(aid)
            items.append({"ok": True, "id": aid, **(r if isinstance(r, dict) else {"result": r})})
        except Exception as exc:  # noqa: BLE001
            items.append({"ok": False, "id": aid, "error": str(exc)})
    ok_n = sum(1 for x in items if x.get("ok"))
    return {"ok": True, "total": len(items), "succeeded": ok_n,
            "failed": len(items) - ok_n, "items": items}


class ExportIn(BaseModel):
    """导出格式：string=StringSession 文本；session=.session 文件；
    pack=session+json 号包 zip；tdata=Telegram Desktop 目录 zip（需 opentele）。"""
    format: str = "pack"  # string | session | pack | tdata


@app.post("/api/accounts/{account_id}/export", dependencies=[Depends(auth)])
async def export_account(account_id: int, body: ExportIn | None = None) -> Any:
    """导出单个账号的会话（敏感操作，需完整令牌，不对 Agent 开放）。"""
    import io
    import json
    import tempfile
    import zipfile

    from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

    fmt = ((body.format if body else None) or "pack").strip().lower()
    acc = db.get(account_id)
    if acc is None:
        raise HTTPException(404, "账号不存在")
    if not acc.session_enc:
        raise HTTPException(400, "该账号尚未登录，没有可导出的会话")

    try:
        if fmt == "string":
            data = manager.export_session_string(account_id)
            return data
        if fmt == "session":
            with tempfile.TemporaryDirectory(prefix="tam_exp_") as tmp:
                safe = "".join(c for c in (acc.label or f"acc{acc.id}")
                               if c.isalnum() or c in "-_.") or f"acc{acc.id}"
                path = manager.export_session_file(
                    account_id, str(Path(tmp) / f"{safe}.session"))["path"]
                raw = Path(path).read_bytes()
            return Response(
                content=raw,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{safe}.session"',
                },
            )
        if fmt == "pack":
            with tempfile.TemporaryDirectory(prefix="tam_exp_") as tmp:
                info = manager.export_account_pack(account_id, tmp)
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.write(info["session"], f"{info['stem']}.session")
                    zf.write(info["json"], f"{info['stem']}.json")
                raw = buf.getvalue()
            safe = info["stem"]
            return Response(
                content=raw,
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{safe}.zip"',
                },
            )
        if fmt == "tdata":
            with tempfile.TemporaryDirectory(prefix="tam_exp_") as tmp:
                info = await manager.export_tdata(account_id, tmp)
                tdata = Path(info["path"])
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for fp in tdata.rglob("*"):
                        if fp.is_file():
                            zf.write(fp, f"tdata/{fp.relative_to(tdata).as_posix()}")
                raw = buf.getvalue()
            safe = "".join(c for c in (acc.label or f"acc{acc.id}")
                           if c.isalnum() or c in "-_.") or f"acc{acc.id}"
            return Response(
                content=raw,
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{safe}-tdata.zip"',
                },
            )
        raise HTTPException(400, f"不支持的格式：{fmt}（string/session/pack/tdata）")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


class BatchExportIn(BaseModel):
    ids: list[int]
    format: str = "pack"  # pack | session（批量只支持打包类）


@app.post("/api/accounts/export", dependencies=[Depends(auth)])
async def export_accounts_batch(body: BatchExportIn) -> Any:
    """批量导出选中账号为 zip（每个号一个 session+json，或仅 session）。"""
    import io
    import tempfile
    import zipfile

    if not body.ids:
        raise HTTPException(400, "请选择要导出的账号")
    fmt = (body.format or "pack").strip().lower()
    if fmt not in ("pack", "session"):
        raise HTTPException(400, "批量导出只支持 pack 或 session")

    buf = io.BytesIO()
    errors: list[dict[str, Any]] = []
    ok_n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        with tempfile.TemporaryDirectory(prefix="tam_bexp_") as tmp:
            for i, aid in enumerate(body.ids):
                acc = db.get(aid)
                if acc is None or not acc.session_enc:
                    errors.append({"id": aid, "error": "不存在或未登录"})
                    continue
                try:
                    if fmt == "session":
                        safe = "".join(c for c in (acc.label or f"acc{aid}")
                                       if c.isalnum() or c in "-_.") or f"acc{aid}"
                        path = manager.export_session_file(
                            aid, str(Path(tmp) / f"{i:03d}_{safe}.session"))["path"]
                        zf.write(path, f"{safe}.session")
                    else:
                        sub = Path(tmp) / f"a{aid}"
                        info = manager.export_account_pack(aid, str(sub))
                        zf.write(info["session"], f"{info['stem']}.session")
                        zf.write(info["json"], f"{info['stem']}.json")
                    ok_n += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append({"id": aid, "error": str(exc)})
    if ok_n == 0:
        msg = "没有成功导出任何账号"
        if errors:
            msg += "：" + "; ".join(
                f"#{e.get('id')} {e.get('error')}" for e in errors[:5])
        raise HTTPException(400, msg)
    raw = buf.getvalue()
    return Response(
        content=raw,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="accounts-export.zip"',
            "X-Export-Ok": str(ok_n),
            "X-Export-Failed": str(len(errors)),
        },
    )



class RegenerateIn(BaseModel):
    password: str | None = None  # 两步验证密码
    code_wait: float = 8.0       # 发码后等待秒数再读 777000


@app.post("/api/accounts/{account_id}/regenerate-session", dependencies=[Depends(auth)])
async def regenerate_session_api(account_id: int, body: RegenerateIn | None = None) -> dict[str, Any]:
    """防找回：新设备指纹重新登录，旧 session 注销，库内换成新会话。

    成功后可用 /export 再导出 .session / tdata。
    """
    body = body or RegenerateIn()
    try:
        return await manager.regenerate_session(
            account_id,
            password=body.password,
            code_wait=body.code_wait,
        )
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


class BatchRegenerateIn(BaseModel):
    ids: list[int]
    password: str | None = None
    code_wait: float = 8.0
    # 1=串行（默认，最稳）；>1 并行，上限 8。并行易触发发码 FloodWait
    concurrency: int = 1


@app.post("/api/accounts/regenerate-session", dependencies=[Depends(auth)])
async def regenerate_session_batch(body: BatchRegenerateIn, request: Request):
    """批量重生会话。

    三种模式（按优先级）：

    1. **异步作业** ``?async=1``（推荐）：立刻返回任务中心条目
       ``{"ok":true,"task":{...}}``，后台执行；``concurrency`` 控制并行度
       （默认 1 串行，最大 8）。前端轮询 ``GET /api/tasks/{id}``。
    2. **NDJSON 流**：query ``?stream=1`` 或 ``Accept: application/x-ndjson``，
       每完成一个号推一行（客户端断开则停止后续号；流式仍串行）。
    3. **同步汇总**（默认）：等全部跑完一次返回 JSON，支持 ``concurrency``。
    """
    import json as _json

    if not body.ids:
        raise HTTPException(400, "请选择账号")
    conc = max(1, min(8, int(body.concurrency or 1)))

    want_async = (
        (request.query_params.get("async") or "").strip().lower()
        in {"1", "true", "yes"}
    )
    want_stream = (
        (request.query_params.get("stream") or "").strip() in {"1", "true", "yes"}
        or "application/x-ndjson" in (request.headers.get("accept") or "").lower()
    )

    async def _run_one(aid: int) -> dict[str, Any]:
        try:
            r = await manager.regenerate_session(
                aid, password=body.password, code_wait=body.code_wait)
            if not isinstance(r, dict):
                r = {"ok": True, "id": aid, "result": r}
            r.setdefault("id", aid)
            return r
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "id": aid, "error": str(exc)}

    # ---- 异步：任务中心 + 后台 spawn（根治长连接依赖）----
    if want_async:
        # targets 用账号 id 字符串，便于明细里回查
        targets = [str(int(i)) for i in body.ids]
        labels: dict[str, str] = {}
        for aid in body.ids:
            acc = db.get(int(aid))
            if acc:
                labels[str(int(aid))] = acc.label or f"#{aid}"
        title = f"重生会话 · {len(targets)} 个号"
        task = tasks.create(
            "regenerate_session",
            title,
            targets,
            params={
                "password": body.password,
                "code_wait": body.code_wait,
                "concurrency": conc,
                "ids": list(body.ids),
                "labels": labels,
            },
        )

        async def handler(row: dict[str, Any]) -> dict[str, Any]:
            aid = int(row["target"])
            # 单号硬超时：防止某一号连接卡死拖住整批
            try:
                r = await asyncio.wait_for(
                    manager.regenerate_session(
                        aid, password=body.password, code_wait=body.code_wait),
                    timeout=150.0,
                )
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    "重生超时（150s），请检查代理/网络后重试该号"
                ) from exc
            # 失败由 RuntimeError 抛出 → TaskRunner 记 fail
            detail_parts = []
            if r.get("old_logged_out"):
                detail_parts.append("旧授权已注销")
            else:
                detail_parts.append("旧授权注销未确认")
            if r.get("device_model"):
                detail_parts.append(f"设备={r['device_model']}")
            if r.get("adopted_at"):
                detail_parts.append("接管时间已重置")
            if r.get("phone"):
                detail_parts.append(f"phone={r['phone']}")
            return {
                "account_id": aid,
                "detail": "；".join(detail_parts) or "ok",
            }

        # 并行度>1 时缩短号间固定间隔；串行仍保留 ≥2s 缓冲降低 FloodWait
        # 重生发码不宜用「批量动作 8~25s」那么大的间隔，否则看起来像卡住
        delay = float(getattr(settings, "action_min_delay", 0) or 0)
        if conc <= 1:
            delay = min(max(delay, 2.0), 5.0)
        else:
            delay = min(max(delay, 0.5), 3.0)
        # 单号超时：任务执行器层再兜一层（handler 内还有 150s）
        runner.spawn(
            task.id, handler, concurrency=conc, delay=delay,
            target_timeout=180.0,
        )
        t = tasks.get(task.id)
        d = (t or task).public()
        d["live"] = runner.is_running(task.id)
        return {
            "ok": True,
            "async": True,
            "job": task.id,
            "concurrency": conc,
            "task": d,
        }

    if not want_stream:
        if conc <= 1:
            items = [await _run_one(aid) for aid in body.ids]
        else:
            sem = asyncio.Semaphore(conc)
            async def _one(aid: int) -> dict[str, Any]:
                async with sem:
                    return await _run_one(aid)
            items = list(await asyncio.gather(*(_one(a) for a in body.ids)))
        ok_n = sum(1 for x in items if x.get("ok"))
        return {
            "ok": True, "total": len(items), "succeeded": ok_n,
            "failed": len(items) - ok_n, "concurrency": conc, "items": items,
        }

    async def ndjson():
        total = len(body.ids)
        yield _json.dumps({"event": "start", "total": total},
                          ensure_ascii=False) + "\n"
        ok_n = 0
        items = []
        for i, aid in enumerate(body.ids, 1):
            # 客户端断开则停止，避免空跑
            if await request.is_disconnected():
                break
            row = await _run_one(aid)
            if row.get("ok"):
                ok_n += 1
            items.append(row)
            payload = {
                "event": "item",
                "index": i,
                "total": total,
                "id": aid,
                "ok": bool(row.get("ok")),
                "label": row.get("label"),
                "error": row.get("error"),
                "old_logged_out": row.get("old_logged_out"),
                "phone": row.get("phone"),
                "username": row.get("username"),
            }
            yield _json.dumps(payload, ensure_ascii=False) + "\n"
        yield _json.dumps({
            "event": "done",
            "total": total,
            "succeeded": ok_n,
            "failed": total - ok_n,
            "items": items,
        }, ensure_ascii=False) + "\n"

    return StreamingResponse(
        ndjson(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/spintax/preview", dependencies=[Depends(auth)])
async def spintax_preview(body: SpintaxIn) -> dict[str, Any]:
    """校验 {a|b} 文案，返回变体总数与前几条示例。"""
    from .spintax import validate

    return validate(body.text)


@app.post("/api/proxies/check", dependencies=[Depends(auth)])
async def proxies_check(body: ProxyCheckIn) -> dict[str, Any]:
    """批量探测代理到 Telegram DC 的连通性与出口 IP 去重。"""
    from .proxycheck import check_many

    if not body.urls:
        raise HTTPException(400, "urls 为空")
    return await check_many(body.urls,
                            concurrency=max(1, min(body.concurrency, 50)),
                            timeout=max(1.0, min(body.timeout, 60.0)))


@app.get("/api/proxies/audit", dependencies=[Depends(auth)])
async def proxies_audit(concurrency: int = 10) -> dict[str, Any]:
    """代理体检：逐账号检查代理可用性，并找出多号共用同一出口 IP 的关联风险。"""
    from .proxycheck import check_accounts

    return await check_accounts(db, settings.default_proxy,
                                concurrency=max(1, min(concurrency, 50)))


@app.get("/api/autokick", dependencies=[Depends(auth)])
async def autokick_status() -> dict[str, Any]:
    """自动清设备的开关、周期与待处理数量。"""
    from . import autokick

    return autokick.status(manager)


class KickRunIn(BaseModel):
    # 本轮失败后隔多久重试，不传则用已保存的设置。写法：45s / 10m / 2h
    retry: str | None = None


class KickRetryIn(BaseModel):
    # 空字符串或 null = 恢复成 .env / 默认值
    value: str | None = None


@app.post("/api/autokick/run", dependencies=[Depends(auth)])
async def autokick_run(body: KickRunIn | None = None) -> dict[str, Any]:
    """立即扫一轮（不等后台定时器）。"""
    from . import autokick

    return await autokick.run_once(manager, retry_gap=(body.retry if body else None))


@app.post("/api/autokick/retry", dependencies=[Depends(auth)])
async def autokick_set_retry(body: KickRetryIn) -> dict[str, Any]:
    """设置“清设备失败后隔多久重试”，支持 45s / 10m / 2h / 1h30m。"""
    from . import autokick

    try:
        return autokick.set_retry(manager, body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/batch/warmup", dependencies=[Depends(auth)])
async def batch_warmup(body: WarmupIn) -> dict[str, Any]:
    """养号：保活在线 + 随机已读 + 账号之间互聊。"""
    from .warmup import warmup

    ids = manager.healthy_ids(body.account_ids, body.tag)
    if not ids:
        raise HTTPException(400, "没有可用的健康账号（需 status=active 且不在 spam 封锁期）")
    return await warmup(manager, ids, online=body.online, read=body.read,
                        chat=body.chat, rounds=body.rounds,
                        concurrency=body.concurrency)


@app.post("/api/batch/check", dependencies=[Depends(auth)])
async def batch_check(body: BatchIn) -> list[dict[str, Any]]:
    ids = _resolve(body.account_ids, body.tag)
    return await manager.run_batch(
        ids, manager.health_check, concurrency=body.concurrency, stagger=False
    )


@app.post("/api/batch/message", dependencies=[Depends(auth)])
async def batch_message(body: BatchMessageIn) -> list[dict[str, Any]]:
    ids = (manager.healthy_ids(body.account_ids, body.tag) if body.healthy_only
           else _resolve(body.account_ids, body.tag))
    if not ids:
        raise HTTPException(400, "没有可用的健康账号；如需强制发送请关闭 healthy_only")

    async def task(aid: int) -> Any:
        return await manager.send_message(aid, body.peer, body.text,
                                          spintax=body.spintax)

    return await manager.run_batch(ids, task, concurrency=body.concurrency, stagger=True)


class ImportIn(BaseModel):
    text: str
    tags: list[str] = []
    proxy: str | None = None
    dry_run: bool = False


class SessionFilesIn(BaseModel):
    """批量导入 .session 文件（服务端本地路径，可以是单个文件或目录）。"""
    path: str
    scan: bool = True        # 目录时是否递归子目录
    label: str | None = None
    proxy: str | None = None
    tags: list[str] = []


class SessionTextIn(BaseModel):
    """批量导入 StringSession 文本，一行一个，支持 `标签|session`。"""
    text: str
    label: str | None = None
    proxy: str | None = None
    tags: list[str] = []


class TdataIn(BaseModel):
    path: str
    scan: bool = False
    label: str | None = None
    password: str | None = None
    proxy: str | None = None
    tags: list[str] = []
    own_api: bool = False
    debug: bool = False


class AutoLoginIn(BaseModel):
    password: str | None = None
    timeout: float = 120.0


class BatchAutoLoginIn(BatchIn):
    password: str | None = None
    timeout: float = 120.0


@app.post("/api/accounts/import", dependencies=[Depends(auth)])
async def import_accounts_endpoint(body: ImportIn) -> dict[str, Any]:
    """批量导入 手机号|取码链接 清单（dry_run=true 为试运行预览）。"""
    from .importer import import_accounts

    return import_accounts(db, body.text, tags=body.tags,
                           proxy=body.proxy, dry_run=body.dry_run)



def _want_ndjson(request: Request) -> bool:
    q = (request.query_params.get("stream") or "").strip().lower()
    if q in {"1", "true", "yes"}:
        return True
    accept = (request.headers.get("accept") or "").lower()
    return "application/x-ndjson" in accept


def _ndjson_stream(producer):
    """producer: async generator yielding dict events -> NDJSON StreamingResponse."""
    import json as _json

    async def gen():
        try:
            async for ev in producer:
                yield _json.dumps(ev, ensure_ascii=False, default=str) + "\n"
        except Exception as exc:  # noqa: BLE001
            yield _json.dumps({"event": "error", "error": f"{type(exc).__name__}: {exc}"},
                              ensure_ascii=False) + "\n"

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _progress_queue_import(run_fn):
    """run_fn(on_progress) -> list items；边跑边通过 queue 推送 NDJSON 事件。"""
    import asyncio

    q: asyncio.Queue = asyncio.Queue()
    items_box: list = []

    async def on_progress(index: int, total: int, item: dict) -> None:
        if index == 1:
            await q.put({"event": "start", "total": int(total)})
        items_box.append(item)
        await q.put({
            "event": "item",
            "index": int(index),
            "total": int(total),
            "ok": bool(item.get("ok")),
            "item": item,
        })

    async def runner():
        try:
            items = await run_fn(on_progress)
            if not items_box and items:
                # 无回调触发（空列表等）
                await q.put({"event": "start", "total": len(items)})
                for i, it in enumerate(items, 1):
                    await q.put({"event": "item", "index": i, "total": len(items),
                                 "ok": bool(it.get("ok")), "item": it})
            ok_n = sum(1 for x in (items or items_box) if x.get("ok"))
            total = len(items or items_box)
            await q.put({
                "event": "done",
                "total": total,
                "succeeded": ok_n,
                "failed": total - ok_n,
                "items": items or items_box,
            })
        except Exception as exc:  # noqa: BLE001
            await q.put({"event": "error", "error": f"{type(exc).__name__}: {exc}"})
        finally:
            await q.put(None)

    task = asyncio.create_task(runner())
    try:
        while True:
            ev = await q.get()
            if ev is None:
                break
            yield ev
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except Exception:
                pass


@app.post("/api/accounts/import-sessions", dependencies=[Depends(auth)])
async def import_session_files_endpoint(body: SessionFilesIn, request: Request):
    """批量导入 .session 文件（服务端本地路径）。

    ``?stream=1`` 或 ``Accept: application/x-ndjson`` 时按号推送进度。
    """
    path = body.path.strip().strip('"').strip("'")

    async def _run(on_progress):
        return await manager.import_session_files(
            path, label=body.label, proxy=body.proxy, tags=body.tags, scan=body.scan,
            on_progress=on_progress,
        )

    if _want_ndjson(request):
        return _ndjson_stream(_progress_queue_import(_run))
    try:
        items = await _run(None)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok = sum(1 for i in items if i.get("ok"))
    return {"ok": True, "total": len(items), "succeeded": ok,
            "failed": len(items) - ok, "items": items}


@app.post("/api/accounts/import-session-strings", dependencies=[Depends(auth)])
async def import_session_strings_endpoint(body: SessionTextIn, request: Request):
    """批量导入 StringSession 文本，一行一个。支持 NDJSON 流式进度。"""

    async def _run(on_progress):
        return await manager.import_session_strings(
            body.text, label=body.label, proxy=body.proxy, tags=body.tags,
            on_progress=on_progress,
        )

    if _want_ndjson(request):
        return _ndjson_stream(_progress_queue_import(_run))
    try:
        items = await _run(None)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok = sum(1 for i in items if i.get("ok"))
    return {"ok": True, "total": len(items), "succeeded": ok,
            "failed": len(items) - ok, "items": items}


@app.post("/api/accounts/import-session-upload", dependencies=[Depends(auth)])
async def import_session_upload(
    request: Request,
    label: str | None = None,
    proxy: str | None = None,
    tags: str = "",
    filename: str | None = Header(default=None, alias="X-Filename"),
) -> dict[str, Any]:
    """浏览器直接上传 .session 或含多个 .session 的 zip（原始请求体，不用 multipart）。

    前端：fetch(url + '?label=...', {method:'POST', body: file, headers:{'X-Filename': file.name}})
    """
    import tempfile
    import zipfile
    import shutil

    body = await request.body()
    if not body:
        raise HTTPException(400, "没收到文件内容")
    if len(body) > 256 * 1024 * 1024:
        raise HTTPException(413, "文件超过 256MB")

    name = (filename or request.headers.get("x-filename") or "upload.bin").strip()
    # 防路径穿越：只取 basename
    name = Path(name).name or "upload.bin"
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]

    tmp = tempfile.mkdtemp(prefix="tam_sess_")
    root = Path(tmp)
    try:
        lower = name.lower()
        if lower.endswith(".zip") or body[:2] == b"PK":
            zpath = root / "pack.zip"
            zpath.write_bytes(body)
            try:
                with zipfile.ZipFile(zpath, "r") as zf:
                    taken: set[str] = set()
                    for m in zf.infolist():
                        fn = m.filename.replace("\\", "/").lstrip("/")
                        if not fn or fn.endswith("/") or ".." in fn.split("/"):
                            continue
                        low = fn.lower()
                        if not (low.endswith(".session") or low.endswith(".json")):
                            continue
                        # 扁平化时保唯一名：不同目录同名 session 不能互相覆盖
                        base = Path(fn).name
                        stem, suf = Path(base).stem, Path(base).suffix
                        final = base
                        n = 2
                        while final.lower() in taken:
                            final = f"{stem}_{n}{suf}"
                            n += 1
                        taken.add(final.lower())
                        dest = root / "extracted" / final
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(m) as src, open(dest, "wb") as out:
                            shutil.copyfileobj(src, out)
            except zipfile.BadZipFile as exc:
                raise HTTPException(400, f"不是有效的 zip：{exc}") from exc
            target = str(root / "extracted")
            if not any(Path(target).rglob("*.session")):
                raise HTTPException(400, "zip 里没有找到 .session 文件")
        elif lower.endswith(".session") or body[:15] == b"SQLite format 3":
            dest = root / (name if name.lower().endswith(".session") else "upload.session")
            dest.write_bytes(body)
            target = str(dest)
        else:
            raise HTTPException(
                400,
                "请上传 .session 文件，或包含 .session 的 zip；"
                "StringSession 文本请用「文本」模式粘贴",
            )

        async def _run(on_progress):
            return await manager.import_session_files(
                target, label=label or None, proxy=proxy or None,
                tags=tag_list, scan=True, on_progress=on_progress,
            )

        if _want_ndjson(request):
            async def producer():
                async for ev in _progress_queue_import(_run):
                    if ev.get("event") == "done":
                        ev = dict(ev)
                        ev["filename"] = name
                    yield ev
            # StreamingResponse 在 with 块内返回；ASGI 会在响应完成前保持调用栈，
            # 但 TemporaryDirectory 在函数返回时会清理。改为先读完再走同步路径时无流；
            # 流式时把目录移到更长生命周期不划算——改为在 generator 内持有路径拷贝已完成提取。
            # 这里同步跑完队列仍在 with 内通过 StreamingResponse 边生成边发。
            async def producer_wrap():
                try:
                    async for ev in producer():
                        yield ev
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
            return _ndjson_stream(producer_wrap())

        try:
            items = await _run(None)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ok = sum(1 for i in items if i.get("ok"))
        return {"ok": True, "total": len(items), "succeeded": ok,
                "failed": len(items) - ok, "items": items,
                "filename": name}
    finally:
        if not _want_ndjson(request):
            shutil.rmtree(tmp, ignore_errors=True)


@app.post("/api/accounts/import-tdata", dependencies=[Depends(auth)])
async def import_tdata_endpoint(body: TdataIn) -> list[dict[str, Any]]:
    """导入 Telegram Desktop tdata 目录（服务端本地路径）。"""
    from .tdata import find_tdata_dirs, inspect_tdata, is_tdata_dir

    root = Path(body.path.strip().strip('"').strip("'"))
    if not root.exists():
        raise HTTPException(status_code=400, detail=f"路径不存在：{root}")
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"不是目录：{root}")

    if body.scan:
        dirs = find_tdata_dirs(root)
        if not dirs:
            raise HTTPException(
                status_code=400,
                detail=f"在 {root} 下（最多 3 层）没有找到任何 tdata 目录",
            )
    else:
        if not is_tdata_dir(root):
            hint = "；若该目录下放着多份号包，请勾选“递归扫描”" if any(
                c.is_dir() for c in root.iterdir()
            ) else ""
            raise HTTPException(
                status_code=400,
                detail=f"不是有效的 tdata 目录（应含 key_datas 文件）：{root}{hint}",
            )
        dirs = [root]

    out = []
    for d in dirs:
        try:
            res = await manager.import_tdata(
                str(d), label=body.label if len(dirs) == 1 else None,
                password=body.password, proxy=body.proxy, tags=body.tags,
                use_desktop_api=not body.own_api, debug=body.debug,
            )
        except BaseException as exc:  # noqa: BLE001 - opentele 会抛 BaseException
            res = [{"ok": False, "error": f"{type(exc).__name__}: {exc}"}]
        entry: dict[str, Any] = {"path": str(d), "accounts": res}
        if body.debug or not any(a.get("ok") for a in res):
            entry["debug"] = inspect_tdata(str(d), body.password)
        out.append(entry)
    return out



@app.post("/api/accounts/import-tdata-upload", dependencies=[Depends(auth)])
async def import_tdata_upload(
    request: Request,
    label: str | None = None,
    password: str | None = None,
    proxy: str | None = None,
    tags: str = "",
    scan: bool = True,
    debug: bool = False,
    own_api: bool = False,
    filename: str | None = Header(default=None, alias="X-Filename"),
) -> dict[str, Any]:
    """浏览器上传 tdata 目录打成的 zip（或含多份 tdata 的号包 zip）。

    解压时保留目录结构（tdata 依赖相对路径），并防 zip-slip。
    """
    import shutil
    import tempfile
    import zipfile

    from .tdata import find_tdata_dirs, inspect_tdata, is_tdata_dir

    body = await request.body()
    if not body:
        raise HTTPException(400, "没收到文件内容")
    if len(body) > 512 * 1024 * 1024:
        raise HTTPException(413, "文件超过 512MB")

    name = (filename or request.headers.get("x-filename") or "tdata.zip").strip()
    name = Path(name).name or "tdata.zip"
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]

    if not (name.lower().endswith(".zip") or body[:2] == b"PK"):
        raise HTTPException(400, "请上传 zip（把 tdata 文件夹压缩后上传）")

    with tempfile.TemporaryDirectory(prefix="tam_tdata_up_") as tmp:
        root = Path(tmp)
        zpath = root / "pack.zip"
        zpath.write_bytes(body)
        extracted = root / "extracted"
        extracted.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zpath, "r") as zf:
                for m in zf.infolist():
                    fn = m.filename.replace("\\", "/").lstrip("/")
                    if not fn or fn.endswith("/"):
                        continue
                    parts = fn.split("/")
                    if any(p == ".." for p in parts):
                        continue
                    # 剔盘符
                    if len(parts[0]) == 2 and parts[0][1] == ":":
                        parts = parts[1:]
                        fn = "/".join(parts)
                        if not fn:
                            continue
                    dest = (extracted / fn).resolve()
                    try:
                        dest.relative_to(extracted.resolve())
                    except ValueError:
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(m) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
        except zipfile.BadZipFile as exc:
            raise HTTPException(400, f"不是有效的 zip：{exc}") from exc

        # 找 tdata：优先整包就是一份；否则递归扫描
        dirs: list[Path] = []
        if is_tdata_dir(extracted):
            dirs = [extracted]
        else:
            # 常见：zip 根下直接是 tdata/，或多号包
            cand = extracted / "tdata"
            if is_tdata_dir(cand):
                dirs = [cand]
            elif scan or True:
                dirs = find_tdata_dirs(extracted)
        if not dirs:
            raise HTTPException(
                400,
                "zip 里没有找到 tdata 目录（需含 key_datas）。"
                "请把整个 tdata 文件夹压缩后再上传。",
            )

        out: list[dict[str, Any]] = []
        for d in dirs:
            try:
                res = await manager.import_tdata(
                    str(d),
                    label=label if len(dirs) == 1 else None,
                    password=password or None,
                    proxy=proxy or None,
                    tags=tag_list,
                    use_desktop_api=not own_api,
                    debug=debug,
                )
            except BaseException as exc:  # noqa: BLE001
                res = [{"ok": False, "error": f"{type(exc).__name__}: {exc}"}]
            entry: dict[str, Any] = {"path": str(d), "accounts": res}
            if debug or not any(a.get("ok") for a in res):
                entry["debug"] = inspect_tdata(str(d), password or None)
            out.append(entry)

        ok_acc = sum(
            1 for e in out for a in (e.get("accounts") or []) if a.get("ok")
        )
        return {
            "ok": True,
            "filename": name,
            "tdata_dirs": len(dirs),
            "succeeded": ok_acc,
            "items": out,
        }


@app.post("/api/tdata/inspect", dependencies=[Depends(auth)])
async def inspect_tdata_endpoint(body: TdataIn) -> dict[str, Any]:
    """只体检不导入：逐步骤诊断 tdata 目录，导入失败时先跑这个。"""
    from .tdata import find_tdata_dirs, inspect_tdata, is_tdata_dir

    root = Path(body.path.strip().strip('"').strip("'"))
    if not root.exists():
        raise HTTPException(status_code=400, detail=f"路径不存在：{root}")
    dirs = find_tdata_dirs(root) if body.scan else (
        [root] if is_tdata_dir(root) else find_tdata_dirs(root)
    )
    if not dirs:
        return {"reports": [inspect_tdata(str(root), body.password)]}
    return {"reports": [inspect_tdata(str(d), body.password) for d in dirs]}


@app.post("/api/accounts/{account_id}/login/auto", dependencies=[Depends(auth)])
async def login_auto(account_id: int, body: AutoLoginIn) -> dict[str, Any]:
    """从取码链接自动拉验证码并完成登录。"""
    return await manager.auto_login(account_id, password=body.password, timeout=body.timeout)


@app.post("/api/batch/auto-login", dependencies=[Depends(auth)])
async def batch_auto_login(body: BatchAutoLoginIn) -> list[dict[str, Any]]:
    ids = body.account_ids or [
        a.id for a in db.list(tag=body.tag) if a.code_url and not a.session_enc
    ]

    async def task(aid: int) -> Any:
        return await manager.auto_login(aid, password=body.password, timeout=body.timeout)

    return await manager.run_batch(ids, task, concurrency=body.concurrency, stagger=True)


class ToolCallIn(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


@app.get("/api/tools", operation_id="list_tools", summary="列出可供 Agent 调用的工具及 JSON Schema")
async def tools(readonly: bool = Depends(auth_scoped)) -> dict[str, Any]:
    return {"readonly": readonly, "dry_run": settings.dry_run,
            "tools": list_tools(readonly=readonly)}


@app.post("/api/tools/call", operation_id="call_tool", summary="统一工具调用入口（永返 200 + 结构化结果）")
async def tools_call(body: ToolCallIn, readonly: bool = Depends(auth_scoped)) -> dict[str, Any]:
    ctx = ToolContext(settings, db, manager, readonly=readonly)
    return await call_tool(ctx, body.name, body.arguments)


@app.get("/api/logs", dependencies=[Depends(auth)])
async def logs(account_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    return db.logs(account_id, limit)


@app.get("/api/doctor", dependencies=[Depends(auth)])
async def doctor_get() -> dict[str, Any]:
    """一键体检（不修改任何东西）。"""
    from .doctor import run_doctor

    return run_doctor(fix=False)


@app.post("/api/doctor/fix", dependencies=[Depends(auth)])
async def doctor_fix() -> dict[str, Any]:
    """一键体检并自动修复（装依赖、补密钥、打 opentele 兼容补丁等）。"""
    from .doctor import run_doctor

    return run_doctor(fix=True)



@app.get("/api/system/opentele", dependencies=[Depends(auth)])
async def system_opentele_status() -> dict[str, Any]:
    """是否已安装 opentele（导出 tdata / session→tdata 需要）。"""
    try:
        import opentele  # noqa: F401
        ver = getattr(opentele, "__version__", "unknown")
        return {"ok": True, "installed": True, "version": ver}
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "installed": False, "error": f"{type(exc).__name__}: {exc}"}


@app.post("/api/system/install-opentele", dependencies=[Depends(auth)])
async def system_install_opentele() -> dict[str, Any]:
    """一键安装 opentele（导出 tdata 用）。在当前解释器里 pip install。"""
    import subprocess
    import sys

    try:
        import opentele  # noqa: F401
        return {"ok": True, "installed": True, "already": True,
                "message": "已安装，无需重复安装"}
    except Exception:
        pass
    cmd = [sys.executable, "-m", "pip", "install", "opentele>=1.15"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(Path(__file__).resolve().parents[1]),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "安装超时（>300s），请在服务器上手动: pip install opentele")
    if proc.returncode != 0:
        # 镜像重试
        cmd2 = cmd + ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
        proc = subprocess.run(
            cmd2, capture_output=True, text=True, timeout=300,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-800:]
        raise HTTPException(500, f"安装失败：{tail}")
    # 可选：打 3.13 补丁
    try:
        from . import opentele_patch
        if opentele_patch.needs_patch():
            opentele_patch.apply_patch()
    except Exception:
        pass
    try:
        import opentele  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"pip 成功但 import 失败：{exc}") from exc
    return {"ok": True, "installed": True, "already": False,
            "message": "opentele 已安装，可重新导出 tdata"}




@app.get("/api/system/errors", dependencies=[Depends(auth)])
async def system_errors_list(
    limit: int = 100,
    source: str | None = None,
    level: str | None = None,
) -> dict[str, Any]:
    """错误日志列表 + 统计。"""
    items = db.list_errors(limit=limit, source=source, level=level)
    return {"ok": True, "stats": db.error_stats(), "items": items}


@app.post("/api/system/errors", dependencies=[Depends(auth)])
async def system_errors_report(request: Request) -> dict[str, Any]:
    """前端 / 用户上报错误。"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    msg = str(body.get("message") or body.get("error") or "client error")[:4000]
    tb = body.get("traceback") or body.get("stack")
    eid = record_error(
        msg,
        level=str(body.get("level") or "error")[:16],
        source=str(body.get("source") or "client")[:16],
        path=str(body.get("path") or body.get("url") or "")[:500] or None,
        traceback_text=str(tb)[:20000] if tb else None,
        meta={
            "user_agent": request.headers.get("user-agent", "")[:200],
            "href": body.get("href"),
            "extra": body.get("extra"),
        },
    )
    return {"ok": True, "error_id": eid}


@app.delete("/api/system/errors", dependencies=[Depends(auth)])
async def system_errors_clear(older_than_hours: float | None = None) -> dict[str, Any]:
    """清空错误日志。可选只删 N 小时以前的。"""
    older = None
    if older_than_hours is not None and older_than_hours > 0:
        older = time.time() - float(older_than_hours) * 3600
    n = db.clear_errors(older_than=older)
    return {"ok": True, "deleted": n}


@app.get("/api/system/errors/export", dependencies=[Depends(auth)])
async def system_errors_export(limit: int = 200) -> Response:
    """导出错误报告（便于发给维护者）。"""
    import json as _json

    items = db.list_errors(limit=limit)
    payload = {
        "exported_at": time.time(),
        "stats": db.error_stats(),
        "items": items,
        "app": "telegram-account-manager",
        "version": app.version,
    }
    data = _json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return Response(
        content=data,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="tam-error-report.json"'},
    )


@app.post("/api/system/restart", dependencies=[Depends(auth)])
async def system_restart(request: Request) -> dict[str, Any]:
    """真实重启当前进程（热重载）：exec 同一解释器与命令行。

    会中断所有进行中的导入/任务；约 1 秒后进程被替换。前端应轮询直到服务恢复。
    """
    import sys

    global _restart_lock
    if settings.readonly:
        raise HTTPException(403, "只读模式禁止重启")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if not body.get("confirm"):
        raise HTTPException(400, "必须 confirm=true")
    if _restart_lock:
        return {"ok": True, "message": "重启已在进行中"}
    _restart_lock = True
    record_error(
        "用户触发热重载（进程即将重启）",
        level="info",
        source="system",
        path="/api/system/restart",
        meta={"argv": sys.argv[:8]},
    )

    async def _reexec() -> None:
        await asyncio.sleep(0.9)
        try:
            # 刷新审计
            try:
                db.conn.commit()
            except Exception:
                pass
            argv = [sys.executable] + sys.argv
            os.environ["TAM_RESTARTED_AT"] = str(time.time())
            os.execv(sys.executable, argv)
        except Exception as exc:  # noqa: BLE001
            # exec 失败则退化为退出，交给外部进程管理器拉起
            record_error(f"execv 失败，改为退出：{exc}", level="error", source="system")
            os._exit(42)

    asyncio.create_task(_reexec())
    return {
        "ok": True,
        "message": "服务将在约 1 秒后真实重启，请稍候自动恢复",
        "pid": os.getpid(),
    }



@app.get("/api/stats", dependencies=[Depends(auth)])
async def stats() -> dict[str, Any]:
    accounts = db.list()
    counts: dict[str, int] = {}
    for a in accounts:
        counts[a.status] = counts.get(a.status, 0) + 1
    return {"total": len(accounts), "by_status": counts}


def serve(host: str = "127.0.0.1", port: int = 8848) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)



# --------------------------- 任务系统 ---------------------------

HREF_RE = 'href=["\\\']([^"\\\']+)["\\\']'
PLAIN_URL_RE = 'https?://\\S+'



class TaskMessageIn(BaseModel):
    """群发任务：逐目标进度与失败原因，可随时停止。"""
    peers: list[str] = []
    lead_source: str | None = None
    lead_days: float | None = None
    lead_limit: int = 500
    text: str
    html: bool = False
    files: list[str] = []
    spintax: bool = True
    link_preview: bool = True
    variables_common: dict[str, Any] = {}
    variables: dict[str, dict[str, Any]] = {}
    account_ids: list[int] = []
    tag: str | None = None
    healthy_only: bool = True
    concurrency: int = 1
    delay: float = 0.0
    title: str | None = None


class TaskCollectIn(BaseModel):
    """最近发言人采集任务。"""
    chats: list[str]
    account_id: int | None = None
    days: float = 7.0
    limit: int = 300
    scan: int = 3000
    skip_bots: bool = True
    skip_premium: bool = False
    tags: list[str] = []
    title: str | None = None
    capture_messages: bool = False   # 顺手存下扫到的发言，用于还原对话
    text_limit: int = 4000           # 每个群最多存多少条，防止库撑爆


class PreviewIn(BaseModel):
    text: str
    spintax: bool = True
    html: bool = False
    variables: dict[str, Any] = {}
    samples: int = 3


def _pick_accounts(ids: list[int], tag: str | None, healthy_only: bool) -> list[int]:
    picked = manager.healthy_ids(ids, tag) if healthy_only else _resolve(ids, tag)
    if not picked:
        raise HTTPException(400, "没有可用账号；如需强制执行请关闭 healthy_only")
    return picked


@app.get("/api/tasks", dependencies=[Depends(auth)])
async def list_tasks_api(limit: int = 30, status: str | None = None) -> list[dict[str, Any]]:
    out = []
    for t in tasks.list(limit=limit, status=status):
        d = t.public()
        d["live"] = runner.is_running(t.id)
        out.append(d)
    return out


@app.get("/api/tasks/{task_id}", dependencies=[Depends(auth)])
async def get_task_api(task_id: int, target_status: str | None = None,
                       target_limit: int = 500) -> dict[str, Any]:
    t = tasks.get(task_id)
    if t is None:
        raise HTTPException(404, "任务不存在")
    d = t.public()
    d["live"] = runner.is_running(task_id)
    d["targets"] = tasks.targets(task_id, status=target_status, limit=target_limit)
    return d


@app.post("/api/tasks/{task_id}/stop", dependencies=[Depends(auth)])
async def stop_task_api(task_id: int) -> dict[str, Any]:
    if tasks.get(task_id) is None:
        raise HTTPException(404, "任务不存在")
    ok = await runner.stop(task_id)
    t = tasks.get(task_id)
    return {"ok": ok, "task": t.public() if t else None}


@app.delete("/api/tasks/{task_id}", dependencies=[Depends(auth)])
async def delete_task_api(task_id: int) -> dict[str, Any]:
    if runner.is_running(task_id):
        raise HTTPException(400, "任务还在跑，请先停止")
    tasks.delete(task_id)
    return {"ok": True}


@app.post("/api/tasks/cleanup", dependencies=[Depends(auth)])
async def cleanup_tasks_api(keep: int = 50) -> dict[str, Any]:
    return {"ok": True, "removed": tasks.cleanup(keep=keep)}


@app.post("/api/tasks/message", dependencies=[Depends(auth)])
async def create_message_task(body: TaskMessageIn) -> dict[str, Any]:
    peers = list(body.peers)
    if body.lead_source is not None:
        since = (time.time() - body.lead_days * 86400) if body.lead_days else None
        peers += leads.targets(source=body.lead_source or None, since=since,
                               limit=body.lead_limit)
    peers = list(dict.fromkeys(x.strip() for x in peers if x and x.strip()))
    if not peers:
        raise HTTPException(400, "没有发送目标")
    account_ids = _pick_accounts(body.account_ids, body.tag, body.healthy_only)

    title = body.title or f"群发 {len(peers)} 个目标"
    task = tasks.create("send", title, peers, params={
        "accounts": account_ids, "html": body.html, "files": body.files,
        "spintax": body.spintax, "concurrency": body.concurrency, "delay": body.delay,
    })

    seen = {"i": 0}

    async def handler(row: dict[str, Any]) -> dict[str, Any]:
        aid = account_ids[seen["i"] % len(account_ids)]
        seen["i"] += 1
        vars_ = body.variables.get(row["target"]) or body.variables_common or None
        res = await manager.send_message(
            aid, row["target"], body.text, spintax=body.spintax, html=body.html,
            files=body.files or None, link_preview=body.link_preview, variables=vars_,
        )
        return {"account_id": aid,
                "detail": f"账号#{aid} 已发送 message_id={res.get('message_id')}"}

    delay = body.delay or float(getattr(settings, "action_min_delay", 0) or 0)
    runner.spawn(task.id, handler, concurrency=body.concurrency, delay=delay)
    return {"ok": True, "task": task.public(), "accounts": account_ids}


@app.get("/api/chats", dependencies=[Depends(auth)])
async def list_chats_api(account_id: int | None = None, kind: str = "group",
                         q: str | None = None, limit: int = 200) -> dict[str, Any]:
    """拉取账号的群组列表，采集发言人时直接勾选，不用手敲群名。"""
    aid = account_id
    if aid is None:
        healthy = manager.healthy_ids()
        if not healthy:
            raise HTTPException(400, "没有可用的健康账号")
        aid = healthy[0]
    res = await manager.list_groups(aid, limit=min(limit, 500), kind=kind)
    if q:
        key = q.strip().lower()
        res["items"] = [x for x in res["items"]
                        if key in (x["title"] or "").lower()
                        or key in (x["username"] or "").lower()]
        res["count"] = len(res["items"])
    return res


@app.post("/api/tasks/collect", dependencies=[Depends(auth)])
async def create_collect_task(body: TaskCollectIn) -> dict[str, Any]:
    chats = [c.strip() for c in body.chats if c and c.strip()]
    if not chats:
        raise HTTPException(400, "没有要采集的群组")
    aid = body.account_id
    if aid is None:
        healthy = manager.healthy_ids()
        if not healthy:
            raise HTTPException(400, "没有可用的健康账号")
        aid = healthy[0]

    title = body.title or f"采集最近 {body.days:g} 天发言人·{len(chats)} 个群"
    task = tasks.create("collect_speakers", title, chats, params={
        "account_id": aid, "days": body.days, "limit": body.limit, "tags": body.tags,
    })

    async def handler(row: dict[str, Any]) -> dict[str, Any]:
        res = await manager.collect_recent_speakers(
            aid, row["target"], days=body.days, limit=body.limit, scan=body.scan,
            skip_bots=body.skip_bots, skip_premium=body.skip_premium,
            capture_text=body.capture_messages, text_limit=body.text_limit,
        )
        users = res.get("users", [])
        source = res.get("title") or row["target"]
        stat = leads.upsert_many(users, source=source, tags=body.tags)
        detail = (f"扫 {res.get('scanned', 0)} 条消息，采集到 {len(users)} 人"
                  f"（入库新增 {stat['added']}，更新 {stat['updated']}）")
        if body.capture_messages:
            msgs = res.get("messages", [])
            mstat = leads.add_messages(msgs, source=source)
            detail += f"；对话记录新增 {mstat['added']} 条"
        return {"account_id": aid, "detail": detail}

    runner.spawn(task.id, handler, concurrency=1, delay=2.0)
    return {"ok": True, "task": task.public(), "account_id": aid}


@app.post("/api/message/preview", dependencies=[Depends(auth)])
async def preview_message(body: PreviewIn) -> dict[str, Any]:
    """发送前预览：spintax 展开 + 变量替换 + 超链接提取。"""
    from .manager import render_variables
    from .spintax import expand

    samples = []
    for _ in range(max(1, min(body.samples, 10))):
        out = expand(body.text) if body.spintax else body.text
        samples.append(render_variables(out, body.variables))
    pattern = HREF_RE if body.html else PLAIN_URL_RE
    links = re.findall(pattern, samples[0])
    return {"ok": True, "samples": samples, "links": links,
            "length": len(samples[0]), "html": body.html}


# --------------------------- 线索库 ---------------------------

@app.get("/api/leads", dependencies=[Depends(auth)])
async def list_leads_api(source: str | None = None, days: float | None = None,
                         has_username: bool = False, limit: int = 500) -> dict[str, Any]:
    since = (time.time() - days * 86400) if days else None
    rows = leads.list(source=source, since=since, has_username=has_username, limit=limit)
    return {"count": len(rows), "items": rows}


@app.get("/api/leads/messages", dependencies=[Depends(auth)])
async def list_lead_messages_api(user_id: int | None = None, source: str | None = None,
                                 limit: int = 200) -> dict[str, Any]:
    """某个线索（或整个群）的对话记录，正序返回。"""
    return {"messages": leads.messages(user_id=user_id, source=source, limit=limit),
            "stats": leads.message_stats(source)}


@app.get("/api/leads/sources", dependencies=[Depends(auth)])
async def lead_sources_api() -> list[dict[str, Any]]:
    return leads.sources()


@app.delete("/api/leads", dependencies=[Depends(auth)])
async def clear_leads_api(source: str | None = None) -> dict[str, Any]:
    return {"ok": True, "removed": leads.clear(source)}




# ---------------------------------------------------------------------------
# ZIP 工具箱（网页端）
#
# 和机器人共用 tam/gaf/core/ 里的同一套内核，这边只负责收文件、发文件。
#
# 上传故意用原始请求体而不是 UploadFile：UploadFile 要求装 python-multipart，
# 而它与另一个名为 multipart 的包占用同一个导入名，两者同时存在时 FastAPI
# 会直接报错。为了不给部署凭空加一个易踩坑的硬依赖，这里直接收字节流，
# 前端 fetch(url, {method:'POST', body: file}) 即可。
# ---------------------------------------------------------------------------

_UNPACK_DIR = Path(settings.db_path).parent / "unpack"
_MAX_UPLOAD_MB = 1024


def _unpack_job_dir(job: str) -> Path:
    """把 job 名字限死在十六进制，避免拿用户输入拼路径造成目录穿越。"""
    if not re.fullmatch(r"[0-9a-f]{8,32}", job or ""):
        raise HTTPException(400, "任务号不合法")
    return _UNPACK_DIR / job


async def _read_upload(request: Request) -> bytes:
    body = await request.body()
    if not body:
        raise HTTPException(400, "没收到文件内容，请直接把 zip 作为请求体上传")
    if len(body) > _MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"文件超过 {_MAX_UPLOAD_MB}MB")
    if not body.startswith(b"PK"):
        raise HTTPException(400, "这不是 zip 文件")
    return body


@app.post("/api/tools/unpack/analyze", dependencies=[Depends(auth)])
async def tools_unpack_analyze(request: Request) -> dict[str, Any]:
    """只分析不拆：告诉前端包里有多少个号，好让用户决定怎么拆。"""
    import tempfile

    from .gaf.core import chaibao as core

    body = await _read_upload(request)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "in.zip"
        p.write_bytes(body)
        try:
            info = await asyncio.to_thread(core.analyze, str(p))
        except core.UnpackError as exc:
            raise HTTPException(400, str(exc))
    return {"ok": True, **info}


@app.post("/api/tools/unpack", dependencies=[Depends(auth)])
async def tools_unpack(request: Request, fmt: str,
                       workers: int | None = None) -> dict[str, Any]:
    """拆包。fmt 如 `-9-`（每包 9 个）或 `5,5,5`（逐包指定）。

    结果先落在服务器上，前端再按返回的 url 逐个下载。
    """
    import tempfile
    import uuid

    from .gaf.core import chaibao as core

    body = await _read_upload(request)
    job = uuid.uuid4().hex[:16]
    out = _UNPACK_DIR / job
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "in.zip"
        p.write_bytes(body)
        try:
            # 拆包是纯 CPU/磁盘活，丢线程里做，不能堵住事件循环
            result = await asyncio.to_thread(
                core.unpack, str(p), str(out), fmt, None, "pack", workers)
        except (core.UnpackError, ValueError) as exc:
            shutil_rmtree(out)
            raise HTTPException(400, str(exc))

    for pk in result["packs"]:
        pk["url"] = f"/api/tools/unpack/{job}/{pk['filename']}"
        pk.pop("path", None)          # 服务器本地路径不必告诉前端
    db.log(None, "tools.unpack", True,
           f"拆包 {result['total']} 个号 -> {result['pack_count']} 个包")
    return {"ok": True, "job": job, **result}


@app.get("/api/tools/unpack/{job}/{filename}", dependencies=[Depends(auth)])
async def tools_unpack_download(job: str, filename: str) -> Any:
    from fastapi.responses import FileResponse

    d = _unpack_job_dir(job)
    if not re.fullmatch(r"[\w.\-]+\.zip", filename or ""):
        raise HTTPException(400, "文件名不合法")
    target = (d / filename).resolve()
    if not str(target).startswith(str(d.resolve())) or not target.is_file():
        raise HTTPException(404, "文件不存在或已被清理")
    return FileResponse(str(target), media_type="application/zip",
                        filename=filename)


@app.delete("/api/tools/unpack/{job}", dependencies=[Depends(auth)])
async def tools_unpack_cleanup(job: str) -> dict[str, Any]:
    """拿完结果就删。号包是敏感物，不能长期赖在服务器上。"""
    d = _unpack_job_dir(job)
    existed = d.is_dir()
    shutil_rmtree(d)
    return {"ok": True, "removed": existed}


def shutil_rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# 参数面板
#
# 所有可调参数都存在 .env 里，这里用 doctor.set_env 读写（它会保留原有的注释
# 和行顺序，不会把用户手写的配置文件冲烂）。
#
# 有两类参数：
#   - 热生效：每次用到时才读 os.environ（并发度、解压上限、注册时间端点等），
#     改完立刻生效；
#   - 要重启：启动时就烤进 Settings 对象了（限速、延迟、自动踢设备等），
#     改完会在返回里标 restart_required，前端要提示用户。
# 两类都会同步写进 os.environ，这样热生效的那批不用重启。
#
# 密钥类只出「是否已设置」，绝不回显明文。
# ---------------------------------------------------------------------------

# (key, 类型, 默认值, 分组, 说明, 是否需要重启)
_SETTING_SPECS: list[tuple] = [
    # 并发与速率
    ("TAM_WORKERS", "int", "4", "并发与速率", "ZIP 处理并发数（1-32）", False),
    ("TAM_BATCH_CONCURRENCY", "int", "3", "并发与速率",
     "批量操作默认并行数（1-32，网页批量/工具箱共用）", False),
    ("TAM_REGEN_CONCURRENCY", "int", "1", "并发与速率",
     "批量重生会话默认并行数（1-8，过大易触发发码限流）", False),
    ("TAM_UI_OP_TIMEOUT", "int", "120", "并发与速率",
     "单个账号操作超时秒数，超时自动跳过继续下一个（15-600）", False),
    ("TAM_RATE", "float", "0.5", "并发与速率", "每账号每秒最大请求数", True),
    ("TAM_MIN_DELAY", "float", "8", "并发与速率", "批量动作最小间隔（秒）", True),
    ("TAM_MAX_DELAY", "float", "25", "并发与速率", "批量动作最大间隔（秒）", True),
    # 自动清设备
    ("TAM_AUTO_KICK_HOURS", "float", "24", "自动清设备",
     "默认周期（小时）：接管满多久踢其它设备（0=全局关闭）", True),
    ("TAM_AUTO_KICK_LOOP", "bool", "1", "自动清设备",
     "默认是否循环：踢成功后按周期再次计时（账号可单独改）", True),
    ("TAM_KICK_RETRY", "duration", "1h", "自动清设备",
     "踢出失败后隔多久重试，可写 45s / 10m / 2h", True),
    # ZIP 工具
    ("TAM_MAX_EXTRACT_MB", "int", "512", "ZIP 工具", "解压体积上限（MB）", False),
    # 注册时间查询
    ("TAM_REGTIME_ENDPOINT", "str", "", "注册时间查询",
     "留空=完全离线，不向任何第三方发送账号信息", False),
    ("TAM_REGTIME_UUID", "str", "", "注册时间查询", "部分第三方接口需要的标识", False),
    ("TAM_REGTIME_TIMEOUT", "int", "10", "注册时间查询", "单次查询超时（秒）", False),
    ("TAM_REGTIME_VERIFY_SSL", "bool", "1", "注册时间查询",
     "证书校验，关掉会有中间人风险", False),
    # 安全
    ("TAM_READONLY", "bool", "0", "安全", "只读模式：拒绝一切写入类操作", True),
    ("TAM_DRY_RUN", "bool", "0", "安全", "干跑模式：写操作只返回预览", True),
    ("TAM_PEER_ALLOWLIST", "str", "", "安全",
     "发送对象白名单，逗号分隔，留空=不限制", True),
    ("TAM_DEFAULT_PROXY", "str", "", "安全",
     "默认代理，形如 socks5://user:pass@host:1080", True),
    # 部署
    ("TAM_HOST", "str", "127.0.0.1", "部署", "监听地址", True),
    ("TAM_PORT", "int", "8848", "部署", "监听端口", True),
    ("TAM_DEPLOY", "choice:local,server", "local", "部署", "本地还是服务器", True),
    ("TAM_FRONTEND", "choice:web,bot,both", "web", "部署",
     "网页 / 机器人 / 两个都开", True),
    ("TAM_NO_MENU", "bool", "0", "部署", "跳过启动时的交互式选单", True),
]

# 这些只出「是否已设置」，不回显明文
_SECRET_KEYS = {"TAM_MASTER_KEY", "TAM_WEB_TOKEN", "TAM_API_HASH",
                "TAM_READONLY_TOKEN", "TAM_BOT_TOKEN"}

_SPEC_BY_KEY = {s[0]: s for s in _SETTING_SPECS}


def _coerce_setting(key: str, kind: str, raw: Any) -> str:
    """把前端传来的值校验并规范成要写进 .env 的字符串。"""
    if kind == "bool":
        if isinstance(raw, bool):
            return "1" if raw else "0"
        return "1" if str(raw).strip().lower() in {"1", "true", "yes", "on"} else "0"

    if kind == "int":
        try:
            v = int(str(raw).strip())
        except ValueError:
            raise HTTPException(400, f"{key} 要填整数，收到的是 {raw!r}")
        # 并发度有硬上限，别让人填个 5000 出来
        if key in ("TAM_WORKERS", "TAM_BATCH_CONCURRENCY"):
            v = max(1, min(v, 32))
        elif key == "TAM_REGEN_CONCURRENCY":
            v = max(1, min(v, 8))
        elif key == "TAM_UI_OP_TIMEOUT":
            v = max(15, min(v, 600))
        elif key == "TAM_PORT":
            if not (1 <= v <= 65535):
                raise HTTPException(400, "端口要在 1-65535 之间")
        elif v < 0:
            raise HTTPException(400, f"{key} 不能是负数")
        return str(v)

    if kind == "float":
        try:
            v = float(str(raw).strip())
        except ValueError:
            raise HTTPException(400, f"{key} 要填数字，收到的是 {raw!r}")
        if v < 0:
            raise HTTPException(400, f"{key} 不能是负数")
        return ("%g" % v)

    if kind == "duration":
        # 复用启动时那套解析，写法不对要当场拒绝，别等到重启才炸
        from .config import _duration_env  # noqa: PLC0415
        s = str(raw).strip() or "0"
        os.environ["_TAM_DUR_PROBE"] = s
        try:
            _duration_env("_TAM_DUR_PROBE", 3600.0, minimum=10.0)
        except Exception:
            raise HTTPException(400, f"{key} 写法不对：{raw!r}，应形如 45s / 10m / 2h")
        finally:
            os.environ.pop("_TAM_DUR_PROBE", None)
        return s

    if kind.startswith("choice:"):
        allowed = kind.split(":", 1)[1].split(",")
        s = str(raw).strip()
        if s not in allowed:
            raise HTTPException(400, f"{key} 只能是 {'/'.join(allowed)} 之一")
        return s

    return str(raw).strip()


@app.get("/api/settings", dependencies=[Depends(auth)])
async def settings_get() -> dict[str, Any]:
    """读出当前所有可调参数，按分组返回，给前端直接渲染面板。"""
    from . import doctor  # noqa: PLC0415

    env = doctor.read_env()
    groups: dict[str, list[dict[str, Any]]] = {}
    for key, kind, default, group, desc, restart in _SETTING_SPECS:
        # 环境变量优先于 .env 文件：进程里实际生效的是前者
        cur = os.environ.get(key, env.get(key, default))
        groups.setdefault(group, []).append({
            "key": key, "type": kind, "value": cur, "default": default,
            "desc": desc, "restart_required": restart,
        })

    secrets_state = [
        {"key": k, "set": bool((os.environ.get(k) or env.get(k) or "").strip())}
        for k in sorted(_SECRET_KEYS)
    ]
    return {"groups": groups, "secrets": secrets_state,
            "env_path": str(doctor.ENV_PATH)}


@app.post("/api/settings", dependencies=[Depends(auth)])
async def settings_save(request: Request) -> dict[str, Any]:
    """保存参数。只认白名单里的键，每个值都按类型校验过才写。"""
    from . import doctor  # noqa: PLC0415

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体要是一个对象")
    incoming = body.get("values")
    if not isinstance(incoming, dict) or not incoming:
        raise HTTPException(400, "没有要保存的参数")

    to_write: dict[str, str] = {}
    restart_keys: list[str] = []
    for key, raw in incoming.items():
        if key in _SECRET_KEYS:
            # 密钥允许改，但空值当作「不动」，避免前端把掩码回传导致清空
            if not str(raw).strip():
                continue
            to_write[key] = str(raw).strip()
            restart_keys.append(key)
            continue
        spec = _SPEC_BY_KEY.get(key)
        if spec is None:
            raise HTTPException(400, f"不认识的参数：{key}")
        to_write[key] = _coerce_setting(key, spec[1], raw)
        if spec[5]:
            restart_keys.append(key)

    if not to_write:
        return {"saved": 0, "restart_required": [], "values": {}}

    doctor.set_env(to_write)
    # 同步进当前进程，热生效的那批立刻就能用上，不用等重启
    for k, v in to_write.items():
        os.environ[k] = v

    db.log(None, "settings.save", True, ",".join(sorted(to_write)))
    return {
        "saved": len(to_write),
        "restart_required": sorted(set(restart_keys)),
        "values": {k: ("***" if k in _SECRET_KEYS else v)
                   for k, v in to_write.items()},
    }


@app.post("/api/settings/reset", dependencies=[Depends(auth)])
async def settings_reset(request: Request) -> dict[str, Any]:
    """把指定参数恢复默认值（不传 keys 就是全部恢复）。密钥永远不动。"""
    from . import doctor  # noqa: PLC0415

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 允许空请求体
        body = {}
    keys = (body or {}).get("keys")
    specs = (_SETTING_SPECS if not keys
             else [s for s in _SETTING_SPECS if s[0] in set(keys)])
    if not specs:
        raise HTTPException(400, "没有匹配到要恢复的参数")

    values = {s[0]: s[2] for s in specs}
    doctor.set_env(values)
    for k, v in values.items():
        os.environ[k] = v
    db.log(None, "settings.reset", True, ",".join(sorted(values)))
    return {"reset": len(values),
            "restart_required": sorted(s[0] for s in specs if s[5])}


# ---------------------------------------------------------------------------
# 工具箱：把 GAF 那批功能直接作用于库里已有的号
# ---------------------------------------------------------------------------


class ToolboxBatchIn(BaseModel):
    account_ids: list[int]
    params: dict[str, Any] | None = None
    concurrency: int | None = None


class ToolboxOneIn(BaseModel):
    params: dict[str, Any] | None = None


@app.get("/api/toolbox/ops", dependencies=[Depends(auth)])
async def toolbox_ops() -> dict[str, Any]:
    """列出所有可用操作。网页靠这张表动态渲染表单，后端加新 op 不用改前端。"""
    return {"ops": toolbox.OP_SPECS}


@app.post("/api/accounts/{account_id}/toolbox/{op}",
          dependencies=[Depends(auth)])
async def toolbox_one(account_id: int, op: str,
                      body: ToolboxOneIn) -> dict[str, Any]:
    """对单个号执行一项工具箱操作。"""
    if settings.readonly or settings.dry_run:
        raise HTTPException(403, "当前是只读/干跑模式，不执行工具箱操作")
    try:
        result = await toolbox.run_op(manager, account_id, op, body.params)
    except toolbox.ToolboxError as exc:
        raise HTTPException(400, str(exc))
    db.log(account_id, "toolbox.one", True, op)
    return {"account_id": account_id, "op": op, "result": result}


@app.post("/api/toolbox/{op}/batch", dependencies=[Depends(auth)])
async def toolbox_batch(op: str, body: ToolboxBatchIn) -> dict[str, Any]:
    """批量执行。并发度不传走 TAM_BATCH_CONCURRENCY（参数面板里可调）。"""
    if settings.readonly or settings.dry_run:
        raise HTTPException(403, "当前是只读/干跑模式，不执行工具箱操作")
    try:
        result = await toolbox.run_op_batch(
            manager, body.account_ids, op, body.params,
            concurrency=body.concurrency)
    except toolbox.ToolboxError as exc:
        raise HTTPException(400, str(exc))
    db.log(None, "toolbox.batch", True, f"{op} x{len(body.account_ids)}")
    return result


# ---------------------------------------------------------------------------
# 网页端：整合（合并多个号包）与注册时间分类
#
# 与拆包共用同一个作业目录 _UNPACK_DIR/{job}，所以结果下载和清理
# 直接复用已有的 /api/tools/unpack/{job}/{filename} 与 DELETE 路由，没重写。
# ---------------------------------------------------------------------------

_MAX_MERGE_PARTS = 50


@app.post("/api/tools/merge/add", dependencies=[Depends(auth)])
async def tools_merge_add(request: Request, job: str | None = None) -> dict[str, Any]:
    """往一个合并作业里添一个包。

    上传走原始请求体（一次一个文件），而合并天然要多个包，
    所以做成两步：分次上传攒到同一个 job，再触发 run。
    首次不传 job，拿返回的 job 接着传后面的。
    """
    import uuid

    body = await _read_upload(request)

    if job:
        d = _unpack_job_dir(job)
        if not d.is_dir():
            raise HTTPException(404, "作业不存在或已被清理")
    else:
        job = uuid.uuid4().hex[:16]
        d = _UNPACK_DIR / job

    src = d / "src"
    src.mkdir(parents=True, exist_ok=True)

    parts = sorted(p for p in src.glob("src*.zip"))
    if len(parts) >= _MAX_MERGE_PARTS:
        raise HTTPException(400, f"一次最多合并 {_MAX_MERGE_PARTS} 个包")

    # 不拿用户传的文件名拼路径，一律存成序号名
    (src / f"src{len(parts):03d}.zip").write_bytes(body)
    return {"ok": True, "job": job, "count": len(parts) + 1}


@app.post("/api/tools/merge/{job}/run", dependencies=[Depends(auth)])
async def tools_merge_run(job: str, workers: int | None = None) -> dict[str, Any]:
    """真正合并。workers 不传则读 TAM_WORKERS。"""
    from .gaf.core import zhenghe as core

    d = _unpack_job_dir(job)
    src = d / "src"
    parts = sorted(str(p) for p in src.glob("src*.zip")) if src.is_dir() else []
    if not parts:
        raise HTTPException(400, "这个作业里还没有上传任何包")

    out = d / "merged.zip"
    try:
        result = await asyncio.to_thread(
            core.merge, parts, str(out), None, workers)
    except core.MergeError as exc:
        raise HTTPException(400, str(exc))
    finally:
        # 源包合完立即删。号包是敏感物，不能赖在服务器上。
        shutil_rmtree(src)

    result.pop("out", None)           # 服务器本地路径不必告诉前端
    db.log(None, "tools.merge", True,
           f"合并 {result['sources']} 个包 -> {result['total']} 个号，"
           f"改名 {result['renamed']}")
    return {"ok": True, "job": job,
            "url": f"/api/tools/unpack/{job}/merged.zip", **result}


@app.post("/api/tools/regtime", dependencies=[Depends(auth)])
async def tools_regtime(request: Request, workers: int | None = None) -> dict[str, Any]:
    """按注册日期分类打包。

    默认**完全离线**：只用包里 json 已有的日期字段，一个字节不外发。
    只有配了 TAM_REGTIME_ENDPOINT 才会联网查。
    """
    import tempfile
    import uuid

    from .gaf.core import shaireg as core

    body = await _read_upload(request)
    job = uuid.uuid4().hex[:16]
    d = _UNPACK_DIR / job
    d.mkdir(parents=True, exist_ok=True)
    out = d / "regtime.zip"

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "in.zip"
        p.write_bytes(body)
        try:
            result = await asyncio.to_thread(
                core.regtime, str(p), str(out),
                core.resolver_from_env(), None, workers)
        except core.RegTimeError as exc:
            shutil_rmtree(d)
            raise HTTPException(400, str(exc))

    result.pop("out", None)
    db.log(None, "tools.regtime", True,
           f"注册时间分类 {result['total']} 个号，解出 {result['resolved']}")
    return {"ok": True, "job": job,
            "url": f"/api/tools/unpack/{job}/regtime.zip", **result}


@app.post("/api/tools/convert", dependencies=[Depends(auth)])
async def tools_convert(
    request: Request,
    mode: str = "session_to_tdata",
    password: str | None = None,
    filename: str | None = Header(default=None, alias="X-Filename"),
) -> Any:
    """格式互转：session↔tdata（上传 zip，下载结果 zip）。"""
    import time as _t

    from .convert_tool import ConvertError, session_zip_to_tdata_zip, tdata_zip_to_session_zip

    body = await request.body()
    if not body:
        raise HTTPException(400, "没收到文件")
    if len(body) > 512 * 1024 * 1024:
        raise HTTPException(413, "超过 512MB")
    mode = (mode or "session_to_tdata").strip().lower()
    name = Path((filename or "pack.zip")).name
    job = f"cvt_{int(_t.time())}_{os.getpid()}"
    d = _unpack_job_dir(job)
    d.mkdir(parents=True, exist_ok=True)
    try:
        if mode in ("session_to_tdata", "s2t", "session2tdata"):
            raw, meta = await session_zip_to_tdata_zip(
                body, api_id=settings.api_id, api_hash=settings.api_hash or "")
            out_name = "session-to-tdata.zip"
        elif mode in ("tdata_to_session", "t2s", "tdata2session"):
            raw, meta = await tdata_zip_to_session_zip(
                body, password=password, api_id=settings.api_id,
                api_hash=settings.api_hash or "")
            out_name = "tdata-to-session.zip"
        else:
            raise HTTPException(400, f"未知 mode：{mode}")
    except ConvertError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc
    (d / out_name).write_bytes(raw)
    db.log(None, "tools.convert", True, f"{mode} {meta.get('succeeded')}/{meta.get('total')}")
    return {
        "ok": True, "job": job, "filename": out_name, "mode": mode,
        "url": f"/api/tools/unpack/{job}/{out_name}",
        **meta,
    }


@app.post("/api/tools/passkey", dependencies=[Depends(auth)])
async def tools_passkey(
    request: Request,
    mode: str = "create",
    filename: str | None = Header(default=None, alias="X-Filename"),
) -> Any:
    """Passkey：对 session 号包批量初始化注册（下载凭证 zip）。"""
    import time as _t

    from .passkey_tool import PasskeyError, create_passkeys_from_session_zip

    body = await request.body()
    if not body:
        raise HTTPException(400, "没收到文件")
    mode = (mode or "create").strip().lower()
    if mode not in ("create", "register"):
        raise HTTPException(400, "目前网页仅支持 mode=create（创建/初始化）")
    job = f"pk_{int(_t.time())}_{os.getpid()}"
    d = _unpack_job_dir(job)
    d.mkdir(parents=True, exist_ok=True)
    try:
        raw, meta = await create_passkeys_from_session_zip(
            body, api_id=settings.api_id, api_hash=settings.api_hash or "")
    except PasskeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc
    out_name = "passkey-create.zip"
    (d / out_name).write_bytes(raw)
    db.log(None, "tools.passkey", True, f"create {meta.get('succeeded')}/{meta.get('total')}")
    return {
        "ok": True, "job": job, "filename": out_name, "mode": "create",
        "url": f"/api/tools/unpack/{job}/{out_name}",
        **meta,
    }


@app.post("/api/tools/toapi", dependencies=[Depends(auth)])
async def tools_toapi(
    request: Request,
    mode: str = "from_json",
    password: str | None = None,
    api_base: str | None = None,
    tdata_passcode: str | None = None,
) -> dict[str, Any]:
    """转 API：上传 session/tdata 号包 ZIP，生成 api.json + 取码链接 + 重命名 session。

    mode: no_2fa | manual | from_json
    逻辑对齐 GAFBot zhuanapi（MIT，见 NOTICE.GAFBot）。
    """
    import secrets

    from .toapi_tool import ToApiError, convert_zip

    body = await request.body()
    if not body:
        raise HTTPException(400, "请上传号包 ZIP")
    if len(body) > 80 * 1024 * 1024:
        raise HTTPException(400, "ZIP 超过 80MB 上限")

    job = secrets.token_hex(8)
    d = _unpack_job_dir(job)
    d.mkdir(parents=True, exist_ok=True)
    src = d / "input.zip"
    src.write_bytes(body)
    out = d / "toapi.zip"
    try:
        result = convert_zip(
            src,
            out,
            mode=mode,
            manual_2fa=password,
            api_base=api_base,
            default_api_id=settings.api_id or None,
            default_api_hash=settings.api_hash or None,
            tdata_passcode=tdata_passcode,
        )
    except ToApiError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc

    db.log(None, "tools.toapi", True, f"mode={mode} total={result.get('total')}")
    return {
        "job": job,
        "url": f"/api/tools/unpack/{job}/toapi.zip",
        **result,
    }


# --------------------------- AI 对话面板 ---------------------------

class AiChatIn(BaseModel):
    messages: list[dict[str, Any]] = []


@app.get("/api/ai/config", dependencies=[Depends(auth)])
async def ai_config_get() -> dict[str, Any]:
    from . import ai_panel
    cfg = ai_panel.load_config(db)
    return {"ok": True, **ai_panel.public_config(cfg)}


@app.put("/api/ai/config", dependencies=[Depends(auth)])
async def ai_config_put(request: Request) -> dict[str, Any]:
    from . import ai_panel
    if settings.readonly:
        raise HTTPException(403, "只读模式不能改 AI 配置")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    cfg = ai_panel.save_config(db, body)
    db.log(None, "ai.config", True, f"preset={cfg.get('preset')} enabled={cfg.get('enabled')}")
    return {"ok": True, **ai_panel.public_config(cfg)}


@app.post("/api/ai/chat", dependencies=[Depends(auth)])
async def ai_chat(body: AiChatIn) -> dict[str, Any]:
    from . import ai_panel
    if settings.readonly:
        raise HTTPException(403, "只读模式禁用 AI 对话")
    cfg = ai_panel.load_config(db)
    if not cfg.get("enabled"):
        raise HTTPException(400, "请先在 AI 配置中启用面板")
    try:
        result = await ai_panel.run_chat(
            db=db, settings=settings, manager=manager,
            user_messages=body.messages or [],
            cfg=cfg,
        )
    except Exception as exc:  # noqa: BLE001
        record_error(f"AI chat: {exc}", level="error", source="server", path="/api/ai/chat")
        raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "AI 调用失败")
    db.log(None, "ai.chat", True, f"tools={len(result.get('trace') or [])}")
    return result



if __name__ == "__main__":
    serve()
