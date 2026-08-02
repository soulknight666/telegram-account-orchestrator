"""账号管理核心：基于 Telethon (MTProto) 的多账号会话管理。

关键设计：
- 会话使用 StringSession（字符串形式），由 crypto 模块加密后入库，避免明文 .session 文件泄漏。
- 同一个账号同一时刻只允许一个活跃客户端（Telegram 对并发相同 authkey 敏感）。
- 每个账号可绑定独立代理与设备指纹，降低关联风险。
- 登录采用两步式：send_code -> sign_in(code[, 2FA 密码])，中间态保存在内存。
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Awaitable
from urllib.parse import urlparse

from .config import Settings
from .crypto import decrypt, encrypt
from .db import Account, Database
from .ratelimit import RateLimiter, human_delay


def render_variables(text: str, variables: dict[str, Any] | None) -> str:
    """把 {name} 替换成个性化字段。不用 str.format，免与 spintax 花括号冲突。"""
    if not variables:
        return text
    out = text
    for k, v in variables.items():
        out = out.replace("{" + str(k) + "}", "" if v is None else str(v))
    return out


# Telegram Desktop 官方 API 凭据：tdata 导入的会话本身就是桌面端授权，
# 用同一套指纹连接风控最低，也免去 my.telegram.org 逐个申请。
DESKTOP_API_ID = 2040
DESKTOP_API_HASH = "b18441a1ff607e10a989891a5462e627"


def parse_proxy(url: str | None) -> dict[str, Any] | None:
    """socks5://user:pass@host:1080 -> Telethon python-socks 参数。"""
    if not url:
        return None
    p = urlparse(url)
    scheme = (p.scheme or "socks5").lower()
    if scheme not in {"socks5", "socks4", "http"}:
        raise ValueError(f"不支持的代理协议: {scheme}")
    proxy: dict[str, Any] = {
        "proxy_type": scheme,
        "addr": p.hostname,
        "port": p.port or (1080 if scheme.startswith("socks") else 8080),
        "rdns": True,
    }
    if p.username:
        proxy["username"] = p.username
        proxy["password"] = p.password or ""
    return proxy


# OKPay 菜单常见币种标签 → 标准键
_OKPAY_CURRENCY_ALIASES: dict[str, tuple[str, ...]] = {
    "USDT": ("USDT", "U币", "TETHER", "USD₮"),
    "TRX": ("TRX", "波场", "TRON"),
    "CNY": ("CNY", "人民币", "RMB", "￥", "元"),
}


def _okpay_match_currency(label: str) -> str | None:
    u = (label or "").upper().strip()
    if not u:
        return None
    # 去掉 emoji 后再匹配
    compact = "".join(ch for ch in u if ch.isalnum() or ch in "%.")
    for key, aliases in _OKPAY_CURRENCY_ALIASES.items():
        for a in aliases:
            if a.upper() in u or a.upper() in compact:
                return key
    if compact in {"USDT", "TRX", "CNY", "RMB"}:
        return "CNY" if compact == "RMB" else compact
    return None


def _okpay_parse_balances(text: str) -> dict[str, str]:
    """从机器人回复里抽各币种金额。

    优先匹配 OKPay 总览行：``USDT : 0``。避免把「年化利率 8%」误当成余额。
    """
    import re

    balances: dict[str, str] = {}
    if not text:
        return balances

    def _not_percent(m: re.Match) -> bool:
        end = m.end(1)
        tail = text[end:end + 3]
        return not tail.lstrip().startswith("%")

    # 1) 币种 + 冒号 + 金额；同币种取最后一次（总览常在文末）
    line_specs = {
        "USDT": r"(?im)(?:💰\s*)?(?:USDT|U币|Tether)\s*[:：]\s*(\d+(?:\.\d+)?)",
        "TRX": r"(?im)(?:💰\s*)?(?:TRX|波场|TRON)\s*[:：]\s*(\d+(?:\.\d+)?)",
        "CNY": r"(?im)(?:💰\s*)?(?:CNY|人民币|RMB)\s*[:：]\s*(\d+(?:\.\d+)?)",
    }
    for key, pat in line_specs.items():
        found = None
        for m in re.finditer(pat, text):
            if _not_percent(m):
                found = m.group(1)
        if found is not None:
            balances[key] = found

    # 2) 兜底：排除 % 与「10 至 500000」
    if len(balances) < 3:
        loose = {
            "USDT": r"(?i)(?:USDT|U币|Tether)[^\d%]{0,12}(\d+(?:\.\d+)?)(?!\s*%)",
            "TRX": r"(?i)(?:TRX|波场|TRON)[^\d%]{0,12}(\d+(?:\.\d+)?)(?!\s*%)",
            "CNY": r"(?i)(?:CNY|人民币|RMB|￥)[^\d%]{0,12}(\d+(?:\.\d+)?)(?!\s*%)",
        }
        for key, pat in loose.items():
            if key in balances:
                continue
            for m in re.finditer(pat, text):
                if not _not_percent(m):
                    continue
                after = text[m.end(1):m.end(1) + 12]
                if re.match(r"\s*(?:至|到|~|—|–|-)\s*\d", after):
                    continue
                balances[key] = m.group(1)
                break

    return balances

async def probe_okpay_balance(
    client: Any, bot: str = "Okpay", wait: float = 4.0,
) -> dict[str, Any]:
    """对已连接的 Telethon client：与 OKPay 交互并解析多币种余额。

    会尝试点选菜单里的 USDT / TRX / CNY 按钮各一次。
    """
    import asyncio
    import re

    bot = (bot or "Okpay").lstrip("@").strip() or "Okpay"
    wait = max(2.0, min(float(wait or 4.0), 20.0))
    texts: list[str] = []
    clicked: list[str] = []
    balances: dict[str, str] = {}

    async def _inbound(limit: int = 8) -> list[str]:
        out: list[str] = []
        async for m in client.iter_messages(bot, limit=limit):
            if m.out:
                continue
            t = (m.message or "").strip()
            if t:
                out.append(t)
        return out

    async def _latest_btn_msg() -> Any:
        async for m in client.iter_messages(bot, limit=12):
            if m.out:
                continue
            if getattr(m, "buttons", None):
                return m
            rm = getattr(m, "reply_markup", None)
            if rm and getattr(rm, "rows", None):
                return m
        return None

    def _btn_labels(msg: Any) -> list[str]:
        labels: list[str] = []
        rows = getattr(msg, "buttons", None)
        if rows:
            for row in rows:
                for b in row:
                    t = (getattr(b, "text", None) or "").strip()
                    if t:
                        labels.append(t)
            return labels
        rm = getattr(msg, "reply_markup", None)
        for row in getattr(rm, "rows", None) or []:
            for b in getattr(row, "buttons", None) or []:
                t = (getattr(b, "text", None) or "").strip()
                if t:
                    labels.append(t)
        return labels

    try:
        await client.send_message(bot, "/start")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"无法向 @{bot} 发消息：{exc}") from exc
    await asyncio.sleep(wait)

    # 若有「余额/钱包」类按钮先点一次
    msg = await _latest_btn_msg()
    if msg:
        for lab in _btn_labels(msg):
            if re.search(r"余额宝|理财|存款|基金", lab):
                continue
            if re.search(r"余额|钱包|balance|资产|总览", lab, re.I):
                try:
                    await msg.click(text=lab)
                    clicked.append(lab)
                    await asyncio.sleep(max(2.0, wait * 0.5))
                except Exception:  # noqa: BLE001
                    pass
                break
    try:
        await client.send_message(bot, "余额")
    except Exception:  # noqa: BLE001
        pass
    await asyncio.sleep(max(2.0, wait * 0.5))

    # 先收一波文案：OKPay 主界面通常已带「USDT : 0」总览
    texts.extend(await _inbound(8))
    for k, v in _okpay_parse_balances("\n".join(texts)).items():
        balances[k] = v

    # 收集币种按钮并逐个点选
    msg = await _latest_btn_msg()
    currency_btns: list[tuple[str, str]] = []  # (USDT, 按钮原文)
    if msg:
        seen: set[str] = set()
        for lab in _btn_labels(msg):
            cur = _okpay_match_currency(lab)
            if not cur or cur in seen:
                continue
            # 充值/提现等操作入口即使带币种字样也跳过，避免点进子流程
            if re.search(r"充值|提现|转账|收款|红包|闪兑|兑换", lab):
                continue
            seen.add(cur)
            currency_btns.append((cur, lab))

    if currency_btns:
        for cur, lab in currency_btns:
            try:
                m2 = await _latest_btn_msg() or msg
                await m2.click(text=lab)
                clicked.append(lab)
                await asyncio.sleep(max(2.0, wait * 0.65))
                chunk = await _inbound(6)
                texts.extend(chunk)
                # 优先用最新一条解析当前币种
                newest = "\n".join(chunk[:3])
                part = _okpay_parse_balances(newest)
                if cur in part:
                    balances[cur] = part[cur]
                elif "balance" in part and cur not in balances:
                    balances[cur] = part["balance"]
                else:
                    # 整段再扫一次该币种
                    full = _okpay_parse_balances("\n".join(chunk))
                    if cur in full:
                        balances[cur] = full[cur]
            except Exception as exc:  # noqa: BLE001
                texts.append(f"[点击 {lab} 失败: {type(exc).__name__}: {exc}]")
    else:
        texts.extend(await _inbound(8))

    # 去重保留顺序
    seen_t: set[str] = set()
    uniq: list[str] = []
    for t in texts:
        if t not in seen_t:
            seen_t.add(t)
            uniq.append(t)
    joined = "\n---\n".join(uniq)

    # 总览再补一遍漏掉的币种
    for k, v in _okpay_parse_balances(joined).items():
        if k not in balances:
            balances[k] = v

    return {
        "bot": bot,
        "balances": balances,
        "clicked": clicked,
        "reply": joined[:2000],
        "ok": True,
        "note": (
            f"已点选 {clicked}；解析到 {balances}"
            if balances
            else f"已点选 {clicked or '无按钮'}；未能解析金额，请看 reply"
        ),
    }


@dataclass
class PendingLogin:
    account_id: int
    phone: str
    phone_code_hash: str
    client: Any
    created_at: float


@dataclass
class PendingQrLogin:
    account_id: int
    client: Any
    qr: Any
    url: str
    expires: float
    created_at: float
    png_b64: str = ""


class AccountManager:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.s = settings
        self.db = db
        self.limiter = RateLimiter(settings.global_rate)
        self._pending: dict[int, PendingLogin] = {}
        self._pending_qr: dict[int, PendingQrLogin] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    # ---------- 内部工具 ----------
    def _lock(self, account_id: int) -> asyncio.Lock:
        return self._locks.setdefault(account_id, asyncio.Lock())

    def _build_client(self, acc: Account, session_str: str = "") -> Any:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        # 没填自己的 API 凭据时，沿用 Telegram Desktop 官方凭据：
        # tdata 导入的会话本来就是桌面端授权，指纹一致风控最低；
        # 手机号新登录（send_code）仍建议自己去 my.telegram.org 申请。
        api_id = self.s.api_id or DESKTOP_API_ID
        api_hash = self.s.api_hash or DESKTOP_API_HASH
        own_api = bool(self.s.api_id and self.s.api_hash)
        return TelegramClient(
            StringSession(session_str),
            api_id,
            api_hash,
            proxy=parse_proxy(acc.proxy or self.s.default_proxy),
            device_model=acc.device_model or ("Desktop" if own_api else "Desktop"),
            app_version=acc.app_version or ("5.3.1" if own_api else "5.3.1 x64"),
            system_version=acc.system_version or "Windows 11",
            lang_code=acc.lang_code or "zh",
            system_lang_code=acc.lang_code or "zh",
            connection_retries=3,
            auto_reconnect=True,
        )

    def _load_session(self, acc: Account) -> str:
        if not acc.session_enc:
            raise RuntimeError(f"账号 {acc.label} 尚未授权")
        return decrypt(self.s.master_key, acc.session_enc)

    def _require_session(self, account_id: int) -> Account:
        """业务动作前置：账号存在且本地有会话密文，否则立刻失败（避免连 TG 空转超时）。"""
        acc = self.db.get(account_id)
        if acc is None:
            raise RuntimeError("账号不存在")
        if not acc.session_enc:
            raise RuntimeError("该账号尚未登录或会话已清除，请先登录/导入")
        return acc

    def _save_session(self, acc: Account, session_str: str) -> None:
        fields: dict[str, Any] = {
            "session_enc": encrypt(self.s.master_key, session_str)
        }
        # login_at / adopted_at 只在首次拿到会话时写入；每次关闭会话都会回写 session，
        # 若每次刷新，接管满 24 小时的自动清设备就永远不会到期。
        # adopted_at = 本机接管时间，是清设备的计时起点；它不会被服务端时间覆盖。
        cur = self.db.get(acc.id) if acc.id else None
        if cur is not None and not cur.login_at:
            fields["login_at"] = time.time()
        if cur is not None and not cur.adopted_at:
            fields["adopted_at"] = time.time()
        self.db.update(acc.id, **fields)

    def _commit_takeover(
        self,
        account_id: int,
        session_str: str,
        *,
        device_model: str | None = None,
        app_version: str | None = None,
        system_version: str | None = None,
        user_id: int | None = None,
        username: str | None = None,
        phone: str | None = None,
        reset_device: bool = True,
    ) -> dict[str, Any]:
        """会话接管落库：强制刷新 session + 接管计时 +（可选）设备指纹。

        与 ``_save_session`` 的区别：
        - ``_save_session`` 只在空缺时补 ``adopted_at``（避免日常回写把 24h 清设备永远推后）；
        - 本方法用于「重生 / 导入覆盖」这类真正换授权的场景，必须：
          1. 覆盖 ``session_enc``
          2. **强制** ``adopted_at = login_at = now``（清设备从本机新接管重新计时）
          3. 清空 ``last_kick_at`` / ``kick_retry_at``（避免旧重试队列在 Telegram 24h 保护期内空跑）
          4. 可选写入新设备指纹，面板才能看出「已换设备」
        写完读回校验，防止只写了密文、元数据没落上。
        """
        now = time.time()
        fields: dict[str, Any] = {
            "session_enc": encrypt(self.s.master_key, session_str),
            "status": "active",
            "status_note": None,
            "adopted_at": now,
            "login_at": now,
            "last_kick_at": None,
            "kick_retry_at": None,
            "last_check_at": now,
        }
        if user_id is not None:
            fields["user_id"] = user_id
        if username is not None:
            fields["username"] = username
        if phone is not None:
            fields["phone"] = phone
        if reset_device:
            if device_model is not None:
                fields["device_model"] = device_model
            if app_version is not None:
                fields["app_version"] = app_version
            if system_version is not None:
                fields["system_version"] = system_version
        self.db.update(account_id, **fields)

        # 读回校验：元数据必须真的落库，否则自动清设备会按旧 adopted_at 在保护期内误触发
        cur = self.db.get(account_id)
        if cur is None or not cur.session_enc:
            raise RuntimeError("接管写入后读回失败（session 为空）")
        if cur.adopted_at is None or abs(float(cur.adopted_at) - now) > 5.0:
            self.db.update(account_id, adopted_at=now, login_at=now,
                           last_kick_at=None, kick_retry_at=None)
            cur = self.db.get(account_id)
        if reset_device and device_model and (cur.device_model or "") != device_model:
            self.db.update(account_id, device_model=device_model,
                           app_version=app_version, system_version=system_version)
            cur = self.db.get(account_id)
        return {
            "adopted_at": cur.adopted_at if cur else now,
            "login_at": cur.login_at if cur else now,
            "device_model": cur.device_model if cur else device_model,
            "app_version": cur.app_version if cur else app_version,
            "system_version": cur.system_version if cur else system_version,
            "last_kick_at": cur.last_kick_at if cur else None,
            "kick_retry_at": cur.kick_retry_at if cur else None,
        }

    # ---------- 登录流程 ----------
    async def send_code(self, account_id: int) -> dict[str, Any]:
        acc = self.db.get(account_id)
        if acc is None or not acc.phone:
            raise RuntimeError("账号不存在或未填写手机号")
        client = self._build_client(acc)
        await client.connect()
        sent = await client.send_code_request(acc.phone)
        self._pending[account_id] = PendingLogin(
            account_id, acc.phone, sent.phone_code_hash, client, time.time()
        )
        self.db.log(account_id, "send_code", True, acc.phone)
        return {"account_id": account_id, "phone": acc.phone, "code_sent": True}

    async def sign_in(
        self, account_id: int, code: str, password: str | None = None
    ) -> dict[str, Any]:
        from telethon.errors import SessionPasswordNeededError

        pending = self._pending.get(account_id)
        if pending is None:
            raise RuntimeError("没有待完成的登录，请先发送验证码")
        acc = self.db.get(account_id)
        client = pending.client
        try:
            try:
                await client.sign_in(
                    phone=pending.phone,
                    code=code,
                    phone_code_hash=pending.phone_code_hash,
                )
            except SessionPasswordNeededError:
                if not password:
                    return {"need_password": True}
                await client.sign_in(password=password)

            me = await client.get_me()
            self._save_session(acc, client.session.save())
            self.db.update(
                account_id,
                user_id=me.id,
                username=me.username,
                phone=me.phone or acc.phone,
                status="active",
                status_note=None,
                last_check_at=time.time(),
            )
            self.db.log(account_id, "sign_in", True, f"user_id={me.id}")
            return {"ok": True, "user_id": me.id, "username": me.username}
        except Exception as exc:
            self.db.log(account_id, "sign_in", False, repr(exc))
            raise
        finally:
            self._pending.pop(account_id, None)
            await client.disconnect()


    # ---------- 扫码登录（官方 App 扫码，无需本机收短信）----------
    @staticmethod
    def _qr_png_b64(url: str) -> str:
        """把 tg://login?token=... 编成 PNG data URL（base64）。"""
        import base64
        import io
        try:
            import qrcode
        except ImportError as exc:
            raise RuntimeError("缺少 qrcode 库，请 pip install qrcode pillow") from exc
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    async def _cleanup_qr(self, account_id: int) -> None:
        pend = self._pending_qr.pop(account_id, None)
        if pend is None:
            return
        try:
            if pend.client is not None and pend.client.is_connected():
                await pend.client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    async def qr_login_start(self, account_id: int) -> dict[str, Any]:
        """发起扫码登录：返回二维码 PNG（base64）与 tg:// URL，供网页展示。

        用手机上已登录的官方 Telegram 扫码即可授权本工具；
        不依赖手机号短信，适合无取码链接、或想快速接管的场景。
        """
        acc = self.db.get(account_id)
        if acc is None:
            raise RuntimeError("账号不存在")
        # 若已有会话，提醒但仍允许（会覆盖）
        had_session = bool(acc.session_enc)

        await self._cleanup_qr(account_id)
        # 清掉可能占用连接的验证码登录中间态
        old = self._pending.pop(account_id, None)
        if old is not None:
            try:
                if old.client is not None and old.client.is_connected():
                    await old.client.disconnect()
            except Exception:  # noqa: BLE001
                pass

        client = self._build_client(acc, session_str="")
        try:
            await asyncio.wait_for(client.connect(), timeout=25.0)
        except Exception as exc:  # noqa: BLE001
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                f"连接 Telegram 失败：{type(exc).__name__}: {exc}"
            ) from exc

        try:
            qr = await client.qr_login()
        except Exception as exc:  # noqa: BLE001
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            ename = type(exc).__name__
            if "Flood" in ename or "flood" in str(exc).lower():
                raise RuntimeError(
                    f"扫码登录触发限流：{ename}: {exc}。请稍后再试。"
                ) from exc
            raise RuntimeError(f"发起扫码失败：{ename}: {exc}") from exc

        url = getattr(qr, "url", None) or ""
        if not url:
            await self._cleanup_qr(account_id)
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError("Telegram 未返回扫码 URL")

        expires = getattr(qr, "expires", None)
        try:
            exp_ts = float(expires.timestamp()) if expires is not None else (time.time() + 30.0)
        except Exception:  # noqa: BLE001
            exp_ts = time.time() + 30.0

        try:
            png_b64 = self._qr_png_b64(url)
        except Exception as exc:  # noqa: BLE001
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"生成二维码图片失败：{exc}") from exc

        self._pending_qr[account_id] = PendingQrLogin(
            account_id=account_id,
            client=client,
            qr=qr,
            url=url,
            expires=exp_ts,
            created_at=time.time(),
            png_b64=png_b64,
        )
        self.db.log(account_id, "qr_login_start", True, f"expires={int(exp_ts)}")
        return {
            "ok": True,
            "account_id": account_id,
            "url": url,
            "qr_png_base64": png_b64,
            "expires_at": exp_ts,
            "expires_in": max(0, int(exp_ts - time.time())),
            "had_session": had_session,
            "note": "请用手机 Telegram：设置 → 设备 → 扫描二维码（或直接扫本页二维码）",
        }

    async def qr_login_wait(
        self,
        account_id: int,
        password: str | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """等待用户扫码完成。超时可再次调用；过期需重新 start。

        若账号开启两步验证，扫码后会返回 need_password=true，
        再带 password 调用本接口完成登录。
        """
        from telethon.errors import SessionPasswordNeededError

        pend = self._pending_qr.get(account_id)
        if pend is None:
            raise RuntimeError("没有进行中的扫码登录，请先获取二维码")

        # 二维码本身过期：Telethon 可 recreate
        if time.time() > float(pend.expires) + 5:
            try:
                await pend.qr.recreate()
                pend.url = getattr(pend.qr, "url", "") or pend.url
                exp = getattr(pend.qr, "expires", None)
                try:
                    pend.expires = float(exp.timestamp()) if exp else (time.time() + 30)
                except Exception:  # noqa: BLE001
                    pend.expires = time.time() + 30
                pend.png_b64 = self._qr_png_b64(pend.url)
            except Exception as exc:  # noqa: BLE001
                await self._cleanup_qr(account_id)
                raise RuntimeError(
                    f"二维码已过期且刷新失败，请重新发起扫码：{type(exc).__name__}: {exc}"
                ) from exc

        tmo = max(5.0, min(float(timeout or 60.0), 120.0))
        try:
            # 已在 2FA 阶段：只提交密码
            if password and pend.client is not None:
                try:
                    if not await pend.client.is_user_authorized():
                        await pend.client.sign_in(password=password)
                except Exception:
                    await pend.client.sign_in(password=password)
            else:
                try:
                    await asyncio.wait_for(pend.qr.wait(), timeout=tmo)
                except SessionPasswordNeededError:
                    # 自动尝试本地保存的二验
                    pwd = (password or "").strip()
                    if not pwd:
                        acc = self.db.get(account_id)
                        if acc and getattr(acc, "twofa_enc", None):
                            try:
                                from .crypto import decrypt
                                pwd = decrypt(self.s.master_key, acc.twofa_enc)
                            except Exception:  # noqa: BLE001
                                pwd = ""
                    if not pwd:
                        return {
                            "ok": False,
                            "need_password": True,
                            "account_id": account_id,
                            "note": "扫码成功，但该号开启了两步验证，请填写云密码后继续",
                        }
                    await pend.client.sign_in(password=pwd)
                except asyncio.TimeoutError:
                    return {
                        "ok": False,
                        "pending": True,
                        "account_id": account_id,
                        "expires_at": pend.expires,
                        "expires_in": max(0, int(pend.expires - time.time())),
                        "qr_png_base64": pend.png_b64,
                        "url": pend.url,
                        "note": f"{int(tmo)}s 内未扫码，可继续等待或刷新二维码",
                    }
        except SessionPasswordNeededError:
            return {
                "ok": False,
                "need_password": True,
                "account_id": account_id,
                "note": "需要两步验证密码",
            }
        except Exception as exc:  # noqa: BLE001
            ename = type(exc).__name__
            if "Flood" in ename:
                await self._cleanup_qr(account_id)
                raise RuntimeError(f"扫码登录限流：{ename}: {exc}") from exc
            # 其它错误：保留 pending 以便重试密码
            if "password" in str(exc).lower() or "Password" in ename:
                return {
                    "ok": False,
                    "need_password": True,
                    "account_id": account_id,
                    "error": f"{ename}: {exc}",
                    "note": "两步验证密码错误或需要重新填写",
                }
            await self._cleanup_qr(account_id)
            raise RuntimeError(f"扫码登录失败：{ename}: {exc}") from exc

        client = pend.client
        if not await client.is_user_authorized():
            await self._cleanup_qr(account_id)
            raise RuntimeError("扫码后仍未授权，请重试")

        me = await client.get_me()
        session_str = client.session.save()
        phone = None
        if getattr(me, "phone", None):
            phone = "+" + str(me.phone).lstrip("+")
        meta = self._commit_takeover(
            account_id,
            session_str,
            user_id=getattr(me, "id", None),
            username=getattr(me, "username", None),
            phone=phone,
            reset_device=False,
        )
        # 标记状态
        try:
            self.db.update(account_id, status="active", status_note="qr_login")
        except Exception:  # noqa: BLE001
            pass
        self.db.log(
            account_id, "qr_login", True,
            f"user_id={getattr(me, 'id', None)}",
        )
        await self._cleanup_qr(account_id)
        return {
            "ok": True,
            "account_id": account_id,
            "user_id": getattr(me, "id", None),
            "username": getattr(me, "username", None),
            "phone": phone,
            "adopted_at": meta.get("adopted_at"),
            "note": "扫码登录成功，会话已加密入库",
        }

    async def qr_login_cancel(self, account_id: int) -> dict[str, Any]:
        """取消进行中的扫码登录并断开临时连接。"""
        had = account_id in self._pending_qr
        await self._cleanup_qr(account_id)
        return {"ok": True, "cancelled": had, "account_id": account_id}


    async def auto_login(self, account_id: int, password: str | None = None,
                         timeout: float = 120.0) -> dict[str, Any]:
        """全自动登录：发码 -> 从发卡平台取码链接轮询新码 -> 完成登录。

        适配导入格式：+1812xxxxxxx|https://tgapi.xxx/@user/<uuid>/GetHTML
        若帐号开了两步验证，必须预先传 password。
        """
        from .codefetch import read_code, wait_for_code

        acc = self.db.get(account_id)
        if acc is None:
            raise RuntimeError("账号不存在")
        if not acc.code_url:
            raise RuntimeError("该账号未配置取码链接 code_url")
        proxy = acc.proxy or self.s.default_proxy
        try:  # 基线：记住发码前页面上的旧码，避免误用
            baseline = await read_code(acc.code_url, proxy=proxy)
        except Exception:
            baseline = None
        await self.send_code(account_id)
        try:
            code = await wait_for_code(acc.code_url, exclude=baseline,
                                       timeout=timeout, proxy=proxy)
        except TimeoutError as exc:
            self.db.log(account_id, "auto_login", False, str(exc))
            pending = self._pending.pop(account_id, None)
            if pending is not None:
                await pending.client.disconnect()
            raise
        self.db.log(account_id, "fetch_code", True, f"code_len={len(code)}")
        res = await self.sign_in(account_id, code, password)
        if res.get("need_password"):
            raise RuntimeError("该账号开启了两步验证，请带 password 重试")
        return res

    async def import_session(self, account_id: int, session_str: str) -> dict[str, Any]:
        """导入已有 StringSession（从其他工具迁入）并验证可用性。"""
        acc = self.db.get(account_id)
        client = self._build_client(acc, session_str)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise RuntimeError("session 无效或已失效")
            me = await client.get_me()
            self._save_session(acc, client.session.save())
            self.db.update(
                account_id, user_id=me.id, username=me.username,
                phone=me.phone, status="active", last_check_at=time.time(),
            )
            self.db.log(account_id, "import_session", True, f"user_id={me.id}")
            return {"ok": True, "user_id": me.id, "username": me.username}
        finally:
            await client.disconnect()

    @staticmethod
    def session_file_to_string(path: str) -> str:
        """把 Telethon 的 .session 文件（SQLite）转成 StringSession 字符串。

        不联网。用官方文档给的 `StringSession.save(session_instance)` 写法：
        它对任意 session 实例都成立，会自己把 dc_id / ip / port / auth_key
        序列化完整。不要手动 set_dc + 赋 auth_key，那条路要求 auth_key 必须是
        AuthKey 对象而不是裸 bytes，很容易造出一个看似正常实则连不上的 session。
        """
        from pathlib import Path

        from telethon.sessions import SQLiteSession, StringSession

        # SQLiteSession 接受不带 .session 后缀的路径
        stem = str(Path(path).with_suffix(""))
        src = SQLiteSession(stem)
        try:
            if not src.auth_key:
                # 没有 auth_key 时 StringSession.save 会返回空串，提前抦下来给人话
                raise RuntimeError("文件里没有 auth_key，不是已登录的会话")
            return StringSession.save(src)
        finally:
            # 必须关，否则 SQLite 文件锁不释放，批量导入时会撞 database is locked
            src.close()

    @staticmethod
    def string_to_session_file(session_str: str, path: str) -> str:
        """StringSession → Telethon SQLite .session 文件（不联网）。"""
        from pathlib import Path

        from telethon.sessions import SQLiteSession, StringSession

        p = Path(path)
        if p.suffix.lower() != ".session":
            p = p.with_suffix(".session")
        p.parent.mkdir(parents=True, exist_ok=True)
        # 清掉旧文件，避免 SQLite 追加脏状态
        for junk in (p, Path(str(p) + "-journal"), Path(str(p) + "-wal"),
                     Path(str(p) + "-shm")):
            try:
                junk.unlink()
            except OSError:
                pass
        stem = str(p.with_suffix(""))
        src = StringSession(session_str)
        if not getattr(src, "auth_key", None):
            raise RuntimeError("StringSession 无效：没有 auth_key")
        dst = SQLiteSession(stem)
        try:
            dst.set_dc(src.dc_id, src.server_address, src.port)
            dst.auth_key = src.auth_key
            dst.save()
        finally:
            dst.close()
        if not p.exists():
            raise RuntimeError("写出 .session 失败")
        return str(p)

    def export_session_string(self, account_id: int) -> dict[str, Any]:
        """导出明文 StringSession（敏感，仅人工操作）。"""
        acc = self.db.get(account_id)
        if acc is None:
            raise RuntimeError("账号不存在")
        sess = self._load_session(acc)
        self.db.log(acc.id, "export_session_string", True,
                    f"label={acc.label} user_id={acc.user_id}")
        return {
            "ok": True,
            "id": acc.id,
            "label": acc.label,
            "phone": acc.phone,
            "user_id": acc.user_id,
            "username": acc.username,
            "session": sess,
        }

    def export_session_file(self, account_id: int, out_path: str) -> dict[str, Any]:
        """导出单个 .session 文件到 out_path。"""
        acc = self.db.get(account_id)
        if acc is None:
            raise RuntimeError("账号不存在")
        sess = self._load_session(acc)
        path = self.string_to_session_file(sess, out_path)
        self.db.log(acc.id, "export_session_file", True, f"label={acc.label}")
        return {"ok": True, "id": acc.id, "label": acc.label, "path": path}

    def export_account_pack(self, account_id: int, out_dir: str) -> dict[str, Any]:
        """导出号包目录：{label}.session + {label}.json（兼容 ZIP 工具）。"""
        import json
        from pathlib import Path

        acc = self.db.get(account_id)
        if acc is None:
            raise RuntimeError("账号不存在")
        sess = self._load_session(acc)
        root = Path(out_dir)
        root.mkdir(parents=True, exist_ok=True)
        safe = "".join(c for c in (acc.label or f"acc{acc.id}")
                       if c.isalnum() or c in "-_.") or f"acc{acc.id}"
        session_path = self.string_to_session_file(sess, str(root / f"{safe}.session"))
        meta = {
            "app_id": self.s.api_id,
            "app_hash": self.s.api_hash,
            "sdk": acc.app_version or "",
            "device": acc.device_model or "",
            "app_version": acc.app_version or "",
            "system_version": "",
            "lang_code": acc.lang_code or "zh",
            "system_lang_pack": acc.lang_code or "zh",
            "user_id": acc.user_id,
            "phone": acc.phone or "",
            "username": acc.username or "",
            "session_file": safe,
            "label": acc.label,
            "id": acc.id,
            "has_2fa": acc.has_2fa,
        }
        twofa_plain = ""
        if getattr(acc, "twofa_enc", None):
            try:
                from .crypto import decrypt
                twofa_plain = decrypt(self.s.master_key, acc.twofa_enc)
            except Exception:
                twofa_plain = ""
        meta["twofa"] = twofa_plain
        meta["password"] = twofa_plain
        json_path = root / f"{safe}.json"
        json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        try:
            if twofa_plain:
                (root / "2fa.txt").write_text(twofa_plain + chr(10), encoding="utf-8")
            elif acc.has_2fa == 0:
                (root / "2fa.txt").write_text("无2FA密码。" + chr(10), encoding="utf-8")
        except Exception:
            pass
        self.db.log(acc.id, "export_session_pack", True, f"label={acc.label}")
        return {
            "ok": True, "id": acc.id, "label": acc.label,
            "session": session_path, "json": str(json_path), "stem": safe,
        }

    async def export_tdata(self, account_id: int, out_dir: str) -> dict[str, Any]:
        """导出 Telegram Desktop tdata（需要 opentele；失败给人话原因）。"""
        from pathlib import Path

        acc = self.db.get(account_id)
        if acc is None:
            raise RuntimeError("账号不存在")
        sess = self._load_session(acc)
        root = Path(out_dir)
        root.mkdir(parents=True, exist_ok=True)
        try:
            from opentele.api import API, UseCurrentSession
            from opentele.tl import TelegramClient
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "导出 tdata 需要 opentele。安装：pip install opentele"
                f"（当前：{type(exc).__name__}: {exc}）"
            ) from exc

        # 用临时 StringSession 客户端，再转 TDesktop
        import tempfile

        api = API.TelegramDesktop.Generate()
        if self.s.api_id:
            try:
                api.api_id = int(self.s.api_id)
            except (TypeError, ValueError):
                pass
        if self.s.api_hash:
            api.api_hash = self.s.api_hash

        with tempfile.TemporaryDirectory(prefix="tam_tdata_") as tmp:
            client = TelegramClient(
                str(Path(tmp) / "sess"),
                api=api,
                proxy=None,
                receive_updates=False,
            )
            # 注入已有 StringSession
            from telethon.sessions import StringSession as _SS
            client.session = _SS(sess)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    raise RuntimeError("会话未授权，无法导出 tdata")
                tdesk = await client.ToTDesktop(flag=UseCurrentSession)
                tdata_dir = root / "tdata"
                tdesk.SaveTData(str(tdata_dir))
            finally:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass

        if not (root / "tdata").exists():
            raise RuntimeError("tdata 未生成")
        self.db.log(acc.id, "export_tdata", True, f"label={acc.label}")
        return {"ok": True, "id": acc.id, "label": acc.label,
                "path": str(root / "tdata")}

    @staticmethod
    def _random_desktop_fp() -> tuple[str, str, str]:
        """生成一套新的桌面端设备指纹（尽量避开 linux 字样）。"""
        import random

        try:
            from opentele.api import API
            for _ in range(40):
                api = API.TelegramDesktop.Generate()
                dm = getattr(api, "device_model", "") or "Desktop"
                if "linux" in dm.lower():
                    continue
                return (
                    dm,
                    getattr(api, "app_version", None) or "5.3.1 x64",
                    getattr(api, "system_version", None) or "Windows 11",
                )
        except Exception:  # noqa: BLE001
            pass
        models = ["Desktop", "PC 64bit", "Chrome", "Edge"]
        systems = ["Windows 10", "Windows 11", "Windows 11 x64"]
        apps = ["5.3.1 x64", "5.4.0 x64", "5.2.3 x64"]
        return random.choice(models), random.choice(apps), random.choice(systems)

    async def regenerate_session(
        self,
        account_id: int,
        password: str | None = None,
        code_wait: float = 8.0,
    ) -> dict[str, Any]:
        """防找回核心：用新设备指纹重新登录，使旧 session 失效，库内写入新会话。

        流程与机器人 /norecover 一致：
        旧会话在线 → 新客户端对本机号码要码 → 旧会话读 777000 → 新客户端登录
        → 旧会话 log_out → 加密保存新 StringSession，并更新设备指纹。

        需要账号能拿到手机号（库里的 phone 或 get_me().phone）。
        开了两步验证必须传 password。
        """
        import asyncio
        import re
        import time as _time

        from telethon import TelegramClient
        from telethon.errors import (FloodWaitError, PhoneCodeExpiredError,
                                    PhoneCodeInvalidError,
                                    SessionPasswordNeededError)
        from telethon.sessions import StringSession

        acc = self.db.get(account_id)
        if acc is None:
            raise RuntimeError("账号不存在")
        old_str = self._load_session(acc)
        # 未显式传 2FA 时，尝试用本地加密保存的二验密码
        if not (password or "").strip() and getattr(acc, "twofa_enc", None):
            try:
                from .crypto import decrypt
                password = decrypt(self.s.master_key, acc.twofa_enc)
            except Exception:  # noqa: BLE001
                password = password

        device_model, app_version, system_version = self._random_desktop_fp()
        api_id = self.s.api_id or DESKTOP_API_ID
        api_hash = self.s.api_hash or DESKTOP_API_HASH
        proxy = parse_proxy(acc.proxy or self.s.default_proxy)

        # 重生自建客户端：少重试 + 短超时，避免批量时某一号连不上拖死整队
        _cli_kw = dict(
            proxy=proxy,
            lang_code=acc.lang_code or "zh",
            system_lang_code=acc.lang_code or "zh",
            connection_retries=1,
            retry_delay=1,
            auto_reconnect=False,
            receive_updates=False,
            timeout=15,
        )
        old_client = TelegramClient(
            StringSession(old_str),
            api_id,
            api_hash,
            device_model=acc.device_model or "Desktop",
            app_version=acc.app_version or "5.3.1 x64",
            system_version=acc.system_version or "Windows 11",
            **_cli_kw,
        )
        new_client = TelegramClient(
            StringSession(),
            api_id,
            api_hash,
            device_model=device_model,
            app_version=app_version,
            system_version=system_version,
            **_cli_kw,
        )

        async def _connect(client: Any, who: str) -> None:
            try:
                await asyncio.wait_for(client.connect(), timeout=25.0)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"{who}连接 Telegram 失败：{type(exc).__name__}: {exc}"
                ) from exc

        lock = self._lock(account_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=45.0)
        except asyncio.TimeoutError as exc:
            for c in (old_client, new_client):
                try:
                    if c.is_connected():
                        await c.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            raise RuntimeError(
                "账号正被其他操作占用（健康检查/导入等），请稍后重试重生"
            ) from exc
        try:
            try:
                await _connect(old_client, "旧会话")
                try:
                    authorized = await asyncio.wait_for(
                        old_client.is_user_authorized(), timeout=10.0)
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(
                        f"检查旧会话授权失败：{type(exc).__name__}: {exc}"
                    ) from exc
                if not authorized:
                    raise RuntimeError("当前会话未授权，无法重生")
                me = await asyncio.wait_for(old_client.get_me(), timeout=15.0)
                if not me:
                    raise RuntimeError("无法获取账号信息")
                phone = (me.phone or acc.phone or "").lstrip("+")
                if me.phone and not str(me.phone).startswith("+"):
                    phone = str(me.phone)
                elif me.phone:
                    phone = str(me.phone)
                else:
                    phone = (acc.phone or "").strip()
                if not phone:
                    raise RuntimeError("没有手机号，无法向本机发送验证码完成转移")
                if not str(phone).startswith("+"):
                    # Telethon 通常接受不带 +，但统一补上更稳
                    phone_e164 = "+" + str(phone).lstrip("+")
                else:
                    phone_e164 = str(phone)

                await _connect(new_client, "新会话")
                try:
                    await asyncio.wait_for(
                        new_client.send_code_request(phone_e164), timeout=30.0)
                except FloodWaitError as exc:
                    secs = int(getattr(exc, "seconds", 0) or 0)
                    if secs >= 3600:
                        wait_txt = f"约 {secs // 3600} 小时"
                    elif secs >= 60:
                        wait_txt = f"约 {secs // 60} 分钟"
                    else:
                        wait_txt = f"{secs or '?'} 秒"
                    raise RuntimeError(
                        f"发码触发 FloodWait，需等待 {wait_txt} 后再试"
                        f"（Telegram 限流，不是本机故障）"
                    ) from exc
                except asyncio.TimeoutError as exc:
                    raise RuntimeError("发码超时，请检查代理与网络") from exc
                except Exception as exc:  # noqa: BLE001
                    ename = type(exc).__name__
                    emsg = str(exc)
                    # 登录次数过多 / 发码被封：常见于短时间多次重生或错误 2FA
                    if (
                        "PhonePasswordFlood" in ename
                        or "PhoneNumberFlood" in ename
                        or "logging in too many times" in emsg.lower()
                        or "too many" in emsg.lower() and "login" in emsg.lower()
                    ):
                        raise RuntimeError(
                            "发码被拒绝：该号登录/要码次数过多"
                            "（PhonePasswordFlood）。"
                            "Telegram 会暂时禁止再发登录验证码，通常需等待"
                            "数小时到 24 小时后再试；期间请勿反复点「重生」。"
                            "若刚输错过多次 2FA，也算入此限流。"
                        ) from exc
                    raise RuntimeError(
                        f"发码失败：{ename}: {exc}"
                    ) from exc

                wait = max(3.0, min(float(code_wait or 8.0), 30.0))
                await asyncio.sleep(wait)

                code = None
                try:
                    messages = await asyncio.wait_for(
                        old_client.get_messages(777000, limit=5), timeout=20.0)
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(f"读取官方验证码失败：{exc}") from exc
                for msg in messages or []:
                    text = getattr(msg, "message", None) or getattr(msg, "text", None) or ""
                    m = re.search(r"(\d{5,6})", str(text))
                    if m:
                        code = m.group(1)
                        break
                if not code:
                    raise RuntimeError(
                        "未在 777000 读到验证码。可加大等待或确认旧会话仍能收服务通知"
                    )

                try:
                    await asyncio.wait_for(
                        new_client.sign_in(phone_e164, code), timeout=30.0)
                except SessionPasswordNeededError:
                    if not password:
                        raise RuntimeError(
                            "该账号开启了两步验证，请填写 2FA 密码后重试"
                        )
                    await asyncio.wait_for(
                        new_client.sign_in(password=password), timeout=30.0)
                except PhoneCodeInvalidError as exc:
                    raise RuntimeError("验证码错误或已失效，请重试") from exc
                except PhoneCodeExpiredError as exc:
                    raise RuntimeError("验证码已过期，请重试") from exc

                if not await new_client.is_user_authorized():
                    raise RuntimeError("新会话登录失败")
                new_me = await new_client.get_me()
                new_str = new_client.session.save()
                if not new_str:
                    raise RuntimeError("新会话为空，未写入")

                # 旧授权退出：前手拿着旧 session 也进不来
                old_logged_out = False
                try:
                    await old_client.log_out()
                    old_logged_out = True
                except Exception:  # noqa: BLE001
                    old_logged_out = False

                phone_out = (
                    ("+" + str(new_me.phone).lstrip("+"))
                    if getattr(new_me, "phone", None) else acc.phone
                )
                # 强制回写：session + 新设备指纹 + adopted_at/login_at 重置 + 清踢设备重试
                # （不可只写 session_enc，否则面板看似没变，且清设备会按旧接管时间在 24h 保护期内空跑）
                meta = self._commit_takeover(
                    account_id,
                    new_str,
                    device_model=device_model,
                    app_version=app_version,
                    system_version=system_version,
                    user_id=getattr(new_me, "id", None) or acc.user_id,
                    username=getattr(new_me, "username", None) or acc.username,
                    phone=phone_out,
                    reset_device=True,
                )
                self.db.log(
                    account_id, "regenerate_session", True,
                    f"old_logout={old_logged_out} device={device_model} "
                    f"adopted_at={meta.get('adopted_at')}",
                )
                return {
                    "ok": True,
                    "id": account_id,
                    "label": acc.label,
                    "user_id": getattr(new_me, "id", None),
                    "username": getattr(new_me, "username", None),
                    "phone": getattr(new_me, "phone", None),
                    "device_model": meta.get("device_model") or device_model,
                    "app_version": meta.get("app_version") or app_version,
                    "system_version": meta.get("system_version") or system_version,
                    "adopted_at": meta.get("adopted_at"),
                    "login_at": meta.get("login_at"),
                    "last_kick_at": meta.get("last_kick_at"),
                    "kick_retry_at": meta.get("kick_retry_at"),
                    "old_logged_out": old_logged_out,
                    "note": "已写入新会话并重置本机接管时间；旧授权已尝试注销。"
                            "需要 tdata / .session 文件请再用导出功能。",
                }
            finally:
                for c in (new_client, old_client):
                    try:
                        if c is not None and c.is_connected():
                            await c.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            try:
                lock.release()
            except RuntimeError:
                pass

    async def import_session_files(
        self, path: str, label: str | None = None, proxy: str | None = None,
        tags: Any = (), scan: bool = True,
        on_progress: Any = None,
    ) -> list[dict[str, Any]]:
        """批量导入 .session 文件（单个文件或整个目录）。

        与 tdata 导入同一套路子：转 StringSession -> 验证可用 -> 加密入库，
        失效的不留垃圾行，已存在的同一 user_id 合并而不重建。
        on_progress: 可选 async/callable(index, total, item)，便于流式进度。
        """
        from pathlib import Path

        root = Path(path)
        if not root.exists():
            raise RuntimeError(f"路径不存在：{root}")
        if root.is_dir():
            files = sorted(root.rglob("*.session") if scan else root.glob("*.session"))
        else:
            files = [root]
        if not files:
            raise RuntimeError(f"没找到 .session 文件：{root}")

        tags = list(tags)
        out: list[dict[str, Any]] = []
        total = len(files)

        async def _emit(idx: int, item: dict[str, Any]) -> None:
            out.append(item)
            if on_progress is None:
                return
            try:
                r = on_progress(idx, total, item)
                if hasattr(r, "__await__"):
                    await r
            except Exception:
                pass

        for i, f in enumerate(files, 1):
            base = label or f.stem
            try:
                sess = self.session_file_to_string(str(f))
            except Exception as exc:  # noqa: BLE001
                await _emit(i, {"ok": False, "file": f.name, "error": f"读取失败：{exc}"})
                continue
            lbl, n = base, 2
            while self.db.get_by_label(lbl):
                lbl, n = f"{base}-{n}", n + 1
            acc = self.db.add_account(Account(label=lbl, proxy=proxy, tags=tags))
            try:
                info = await self.import_session(acc.id, sess)
            except Exception as exc:  # noqa: BLE001
                self.db.delete(acc.id)
                await _emit(i, {"ok": False, "file": f.name, "label": lbl, "error": str(exc)})
                continue
            dup = next((a for a in self.db.list()
                        if a.user_id == info["user_id"] and a.id != acc.id), None)
            if dup is not None:
                fresh = self.db.get(acc.id)
                # 合并覆盖 = 本机重新接管该号：重置 adopted_at，避免清设备按旧时间触发
                if fresh and fresh.session_enc:
                    plain = decrypt(self.s.master_key, fresh.session_enc)
                    self._commit_takeover(
                        dup.id, plain,
                        user_id=fresh.user_id, username=fresh.username,
                        phone=fresh.phone, reset_device=False,
                    )
                else:
                    self.db.update(dup.id, session_enc=fresh.session_enc, status="active",
                                   username=fresh.username, phone=fresh.phone,
                                   last_check_at=time.time())
                self.db.delete(acc.id)
                await _emit(i, {"ok": True, "id": dup.id, "file": f.name,
                            "merged_into_existing": True, **info})
            else:
                await _emit(i, {"ok": True, "id": acc.id, "file": f.name, "label": lbl, **info})
        return out

    async def import_session_strings(
        self, text: str, label: str | None = None, proxy: str | None = None,
        tags: Any = (),
        on_progress: Any = None,
    ) -> list[dict[str, Any]]:
        """批量导入 StringSession 文本，一行一个。

        支持 `标签|session` 形式指定标签；不指定就自动编号。
        on_progress: 可选 async/callable(index, total, item)。
        """
        tags = list(tags)
        out: list[dict[str, Any]] = []
        lines = [ln.strip() for ln in (text or "").splitlines()]
        lines = [ln for ln in lines if ln and not ln.startswith("#")]
        if not lines:
            raise RuntimeError("没有可导入的 session")

        total = len(lines)

        async def _emit(i: int, item: dict[str, Any]) -> None:
            out.append(item)
            if on_progress is None:
                return
            try:
                r = on_progress(i, total, item)
                if hasattr(r, "__await__"):
                    await r
            except Exception:
                pass

        for idx, line in enumerate(lines, 1):
            if "|" in line:
                raw_label, sess = (p.strip() for p in line.split("|", 1))
            else:
                raw_label, sess = "", line
            base = raw_label or (f"{label}-{idx}" if label else f"session-{idx}")
            lbl, n = base, 2
            while self.db.get_by_label(lbl):
                lbl, n = f"{base}-{n}", n + 1
            acc = self.db.add_account(Account(label=lbl, proxy=proxy, tags=tags))
            try:
                info = await self.import_session(acc.id, sess)
            except Exception as exc:  # noqa: BLE001
                self.db.delete(acc.id)
                await _emit(idx, {"ok": False, "label": lbl, "error": str(exc)})
                continue
            dup = next((a for a in self.db.list()
                        if a.user_id == info["user_id"] and a.id != acc.id), None)
            if dup is not None:
                fresh = self.db.get(acc.id)
                if fresh and fresh.session_enc:
                    plain = decrypt(self.s.master_key, fresh.session_enc)
                    self._commit_takeover(
                        dup.id, plain,
                        user_id=fresh.user_id, username=fresh.username,
                        phone=fresh.phone, reset_device=False,
                    )
                else:
                    self.db.update(dup.id, session_enc=fresh.session_enc, status="active",
                                   username=fresh.username, phone=fresh.phone,
                                   last_check_at=time.time())
                self.db.delete(acc.id)
                await _emit(idx, {"ok": True, "id": dup.id, "merged_into_existing": True, **info})
            else:
                await _emit(idx, {"ok": True, "id": acc.id, "label": lbl, **info})
        return out

    async def import_tdata(self, path: str, label: str | None = None,
                           password: str | None = None, proxy: str | None = None,
                           tags: Any = (), use_desktop_api: bool = True,
                           debug: bool = False,
                           on_progress: Any = None) -> list[dict[str, Any]]:
        """导入 Telegram Desktop 的 tdata 目录（可含多个账号）。

        流程：tdata -> StringSession -> 验证可用 -> 加密入库；
        若已存在同一 user_id 的账号则合并更新，不重复建号。
        on_progress: 可选 async/callable(index, total, item)。
        """
        from pathlib import Path

        from .tdata import tdata_to_sessions

        sessions = await tdata_to_sessions(
            path, api_id=self.s.api_id, api_hash=self.s.api_hash,
            password=password, use_desktop_api=use_desktop_api, debug=debug,
        )
        base = label or Path(path).resolve().name
        tags = list(tags)
        out: list[dict[str, Any]] = []
        total = len(sessions) or 1

        async def _emit(i: int, item: dict[str, Any]) -> None:
            out.append(item)
            if on_progress is None:
                return
            try:
                r = on_progress(i, total, item)
                if hasattr(r, "__await__"):
                    await r
            except Exception:
                pass

        for idx, sess in enumerate(sessions, 1):
            lbl = base if len(sessions) == 1 else f"{base}-{idx}"
            n = 2
            while self.db.get_by_label(lbl):
                lbl, n = f"{base}-{idx}-{n}" if len(sessions) > 1 else f"{base}-{n}", n + 1
            acc = self.db.add_account(Account(label=lbl, proxy=proxy, tags=tags))
            try:
                info = await self.import_session(acc.id, sess)
            except Exception as exc:  # 失效会话不留垃圾行
                self.db.delete(acc.id)
                await _emit(idx, {"ok": False, "label": lbl, "error": str(exc)})
                continue
            dup = next((a for a in self.db.list()
                        if a.user_id == info["user_id"] and a.id != acc.id), None)
            if dup is not None:
                fresh = self.db.get(acc.id)
                if fresh and fresh.session_enc:
                    plain = decrypt(self.s.master_key, fresh.session_enc)
                    self._commit_takeover(
                        dup.id, plain,
                        user_id=fresh.user_id, username=fresh.username,
                        phone=fresh.phone, reset_device=False,
                    )
                else:
                    self.db.update(dup.id, session_enc=fresh.session_enc, status="active",
                                   username=fresh.username, phone=fresh.phone,
                                   last_check_at=time.time())
                self.db.delete(acc.id)
                await _emit(idx, {"ok": True, "id": dup.id, "merged_into_existing": True, **info})
            else:
                await _emit(idx, {"ok": True, "id": acc.id, "label": lbl, **info})
        return out

    async def logout(self, account_id: int) -> dict[str, Any]:
        """退出登录：尽量向 Telegram 注销，无论远端是否成功都清除本地会话。

        未授权 / 无 session / 连不上时不再抛 500，只清本地并返回说明。
        """
        acc = self.db.get(account_id)
        if acc is None:
            raise RuntimeError("账号不存在")
        remote = False
        note = "local_only"
        if acc.session_enc:
            try:
                async with self.session(account_id) as client:
                    await client.log_out()
                remote = True
                note = "remote"
            except Exception as exc:  # noqa: BLE001
                note = f"local_after_error:{type(exc).__name__}"
                self.db.log(account_id, "logout", False, repr(exc)[:400])
        self.db.update(account_id, session_enc=None, status="unauthorized",
                       login_at=None, adopted_at=None, last_kick_at=None,
                       kick_retry_at=None)
        self.db.log(account_id, "logout", True, note)
        return {"ok": True, "remote_logged_out": remote, "note": note}


    async def delete_telegram_account(
        self,
        account_id: int,
        *,
        reason: str = "User requested deletion",
        password: str | None = None,
        purge_local: bool = True,
    ) -> dict[str, Any]:
        """向 Telegram 申请注销账号（account.deleteAccount），不可逆。

        注意：官方可能进入「延迟删除」流程；成功后本地会话一律作废。
        部分号若开启云密码，Telegram 可能要求先校验 2FA（password）。
        """
        from telethon.tl.functions.account import DeleteAccountRequest
        from telethon.errors import (
            PasswordHashInvalidError,
            SessionPasswordNeededError,
        )

        acc = self._require_session(account_id)
        reason = (reason or "User requested deletion").strip()[:128] or "User requested deletion"
        remote_ok = False
        detail = ""
        try:
            async with self.session(account_id) as client:
                # 少数环境在删号前需已通过 2FA；尽量用库内密码或入参
                pwd = password
                if not pwd and getattr(acc, "twofa_enc", None):
                    try:
                        from .crypto import decrypt
                        pwd = decrypt(self.s.master_key, acc.twofa_enc)
                    except Exception:  # noqa: BLE001
                        pwd = None
                if pwd:
                    try:
                        from telethon.tl.functions.account import GetPasswordRequest
                        from telethon.password import compute_check
                        pwd_info = await client(GetPasswordRequest())
                        if getattr(pwd_info, "has_password", False):
                            # 仅确保能访问账号；删除请求本身通常只带 reason
                            pass
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    remote_ok = bool(await client(DeleteAccountRequest(reason=reason)))
                    detail = "deleteAccount=True" if remote_ok else "deleteAccount=False"
                except SessionPasswordNeededError as exc:
                    raise RuntimeError(
                        "注销需要两步验证密码，请在参数里填写 password 后重试"
                    ) from exc
                except PasswordHashInvalidError as exc:
                    raise RuntimeError("两步验证密码错误") from exc
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(
                        f"Telegram 拒绝注销：{type(exc).__name__}: {exc}"
                    ) from exc
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"注销失败：{type(exc).__name__}: {exc}") from exc

        if purge_local:
            self.db.update(
                account_id,
                session_enc=None,
                status="deleted",
                status_note="telegram account deletion requested",
                login_at=None,
                adopted_at=None,
                last_kick_at=None,
                kick_retry_at=None,
            )
        self.db.log(
            account_id, "delete_telegram_account", remote_ok,
            f"{detail}; reason={reason[:80]}",
        )
        return {
            "ok": remote_ok,
            "account_id": account_id,
            "remote_deleted": remote_ok,
            "local_purged": bool(purge_local),
            "reason": reason,
            "note": "已向 Telegram 提交注销；本地会话已清除" if purge_local
                    else "已向 Telegram 提交注销；本地记录未清",
        }

    # ---------- 会话上下文 ----------
    def session(self, account_id: int) -> "_SessionCtx":
        return _SessionCtx(self, account_id)

    # ---------- 业务动作 ----------
    async def health_check(self, account_id: int) -> dict[str, Any]:
        """检查授权状态、限制状态与基本信息。"""
        from telethon import functions
        from telethon.errors import (
            AuthKeyUnregisteredError, UserDeactivatedBanError, FloodWaitError,
        )

        acc = self.db.get(account_id)
        if acc is None:
            raise RuntimeError("账号不存在")
        if not acc.session_enc:
            self.db.update(account_id, status="unauthorized", last_check_at=time.time())
            return {"account_id": account_id, "status": "unauthorized"}
        try:
            async with self.session(account_id) as client:
                me = await client.get_me()
                full = await client(functions.users.GetFullUserRequest("me"))
                restricted = bool(getattr(me, "restricted", False))
                premium = bool(getattr(me, "premium", False))
                has_2fa = None
                try:
                    from telethon.tl.functions.account import GetPasswordRequest
                    pwd = await client(GetPasswordRequest())
                    has_2fa = int(bool(getattr(pwd, "has_password", False)))
                except Exception:
                    pass
                status = "restricted" if restricted else "active"
                self.db.update(
                    account_id, user_id=me.id, username=me.username,
                    status=status, status_note=None, premium=int(premium),
                    has_2fa=has_2fa, last_check_at=time.time(),
                )
                self.db.log(account_id, "health_check", True, status)
                return {
                    "account_id": account_id,
                    "status": status,
                    "user_id": me.id,
                    "username": me.username,
                    "premium": premium,
                    "has_2fa": has_2fa,
                    "about": getattr(full.full_user, "about", None),
                }
        except (AuthKeyUnregisteredError,) as exc:
            self._mark(account_id, "unauthorized", exc)
            return {"account_id": account_id, "status": "unauthorized"}
        except UserDeactivatedBanError as exc:
            self._mark(account_id, "banned", exc)
            return {"account_id": account_id, "status": "banned"}
        except FloodWaitError as exc:
            self._mark(account_id, "error", exc, note=f"FloodWait {exc.seconds}s")
            return {"account_id": account_id, "status": "flood_wait", "seconds": exc.seconds}
        except Exception as exc:  # 网络/代理类错误
            self._mark(account_id, "error", exc)
            return {"account_id": account_id, "status": "error", "error": repr(exc)}

    def _mark(self, account_id: int, status: str, exc: Exception, note: str | None = None) -> None:
        self.db.update(
            account_id, status=status, status_note=note or repr(exc)[:500],
            last_check_at=time.time(),
        )
        self.db.log(account_id, "health_check", False, note or repr(exc))

    async def get_dialogs(self, account_id: int, limit: int = 30) -> list[dict[str, Any]]:
        self._require_session(account_id)
        async with self.session(account_id) as client:
            out = []
            async for d in client.iter_dialogs(limit=limit):
                out.append({
                    "id": d.id,
                    "name": d.name,
                    "unread": d.unread_count,
                    "is_group": d.is_group,
                    "is_channel": d.is_channel,
                })
            return out

    async def list_groups(self, account_id: int, limit: int = 200,
                          kind: str = "group") -> dict[str, Any]:
        """列出账号所在的群组/频道，给采集界面做可勾选清单。

        kind: group=只要群（含超级群），channel=只要频道，all=全部。
        成员数、最后活跃时间都返回，方便先排序再选。
        """
        items: list[dict[str, Any]] = []
        async with self.session(account_id) as client:
            async for d in client.iter_dialogs(limit=limit):
                is_group = bool(d.is_group)
                is_channel = bool(d.is_channel) and not is_group
                if kind == "group" and not is_group:
                    continue
                if kind == "channel" and not is_channel:
                    continue
                if kind not in ("group", "channel", "all"):
                    kind = "all"
                if kind == "all" and not (is_group or is_channel):
                    continue
                ent = d.entity
                members = (getattr(ent, "participants_count", None)
                           or getattr(ent, "members_count", None))
                last_at = None
                msg = getattr(d, "message", None)
                if msg is not None and getattr(msg, "date", None):
                    last_at = msg.date.timestamp()
                username = getattr(ent, "username", None)
                items.append({
                    "id": d.id,
                    "title": d.name or str(d.id),
                    "username": username,
                    "peer": f"@{username}" if username else str(d.id),
                    "members": members,
                    "is_group": is_group,
                    "is_channel": is_channel,
                    "megagroup": bool(getattr(ent, "megagroup", False)),
                    "broadcast": bool(getattr(ent, "broadcast", False)),
                    "unread": d.unread_count,
                    "last_msg_at": last_at,
                })
        items.sort(key=lambda x: (x["last_msg_at"] or 0), reverse=True)
        self.db.log(account_id, "list_groups", True, f"kind={kind} count={len(items)}")
        return {"ok": True, "account_id": account_id, "kind": kind,
                "count": len(items), "items": items}

    async def check_spam_status(self, account_id: int) -> dict[str, Any]:
        """与 @SpamBot 对话判定是否被限制（Telethon 官方 FAQ 推荐的做法）。

        回复含 free -> 正常；含解除日期 -> 临时 spam block；否则视为长期限制。
        """
        import re

        self._require_session(account_id)
        reply = ""
        async with self.session(account_id) as client:
            await client.send_message("SpamBot", "/start")
            await asyncio.sleep(4)
            async for m in client.iter_messages("SpamBot", limit=3):
                if m.out:
                    continue
                reply = (m.message or "").strip()
                break

        low = reply.lower()
        status, until = "unknown", None
        if not reply:
            status = "unknown"
        elif "good news" in low or "no limits" in low or "free as a bird" in low:
            status = "active"
        elif re.search(r"until\s+\S", reply, re.I) or "limited until" in low:
            status = "spam_block"
            until = time.time() + 24 * 3600  # 保守估计，具体日期写入 note
        elif "limited" in low or "restricted" in low or "cannot" in low:
            status = "spam_block_perm"
        # 识别不了的回复一律当 unknown，宁可不写库，也不能误判为健康

        if status != "unknown":
            self.db.update(account_id, status=status, spam_until=until,
                           status_note=reply[:500] or None, last_check_at=time.time())
        self.db.log(account_id, "spam_check", True, status)
        return {"account_id": account_id, "spam_status": status,
                "until": until, "reply": reply}

    async def check_okpay_balance(
        self, account_id: int, bot: str = "Okpay", wait: float = 4.0,
    ) -> dict[str, Any]:
        """用该号私聊 OKPay 钱包机器人查余额。

        默认 @Okpay。流程：
        1. 发 /start，必要时点「余额」或发文本「余额」
        2. 识别内联键盘上的 USDT / TRX / CNY 等按钮，**逐个点选**
        3. 汇总回复，解析各币种金额

        解析不到时仍返回 reply 原文与 clicked 列表，便于人工核对。
        """
        self._require_session(account_id)
        async with self.session(account_id) as client:
            try:
                result = await probe_okpay_balance(client, bot=bot, wait=wait)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"OKPay 查询失败：{exc}") from exc
        result["account_id"] = account_id
        bal = result.get("balances") or {}
        self.db.log(
            account_id, "okpay_balance", True,
            f"bot={result.get('bot')} balances={bal} clicked={result.get('clicked')}",
        )
        return result

    def healthy_ids(self, account_ids: list[int] | None = None,
                    tag: str | None = None) -> list[int]:
        """只取健康号：已授权、status=active、不在 spam 封锁期。"""
        now = time.time()
        pool = ([self.db.get(i) for i in account_ids] if account_ids
                else self.db.list(tag=tag))
        out = []
        for a in pool:
            if a is None or not a.session_enc or a.status != "active":
                continue
            if a.spam_until and a.spam_until > now:
                continue
            out.append(a.id)
        return out

    async def send_message(self, account_id: int, peer: str | int, text: str,
                           spintax: bool = True, html: bool = False,
                           files: list[str] | None = None,
                           link_preview: bool = True,
                           variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """发消息。

        spintax=True 时每条独立展开 {a|b} 变体，降低重复内容判定；
        html=True 时支持富文本与可点击超链接（<b>、<a href="...">）；
        files 传本地路径则作为附件发送，正文作为图说。
        """
        body = text
        if spintax:
            from .spintax import expand

            body = expand(text)
        body = render_variables(body, variables)
        kwargs: dict[str, Any] = {"link_preview": link_preview}
        if html:
            kwargs["parse_mode"] = "html"
        async with self.session(account_id) as client:
            if files:
                msg = await client.send_file(
                    peer, files if len(files) > 1 else files[0], caption=body, **kwargs
                )
                if isinstance(msg, list):
                    msg = msg[-1]
            else:
                msg = await client.send_message(peer, body, **kwargs)
            self.db.log(account_id, "send_message", True,
                        f"{peer}{' html' if html else ''}"
                        f"{' files=' + str(len(files)) if files else ''}")
            return {"ok": True, "message_id": msg.id, "text": body,
                    "html": html, "files": len(files or [])}

    async def collect_recent_speakers(
        self, account_id: int, chat: str | int, days: float = 7.0,
        limit: int = 300, max_messages: int = 3000, skip_bots: bool = True,
        skip_premium: bool = False, scan: int | None = None,
        capture_text: bool = False, text_limit: int = 4000,
    ) -> dict[str, Any]:
        """按最近活跃情况采集群组发言人，避开长期不活跃成员的无效触达。

        只看最近 days 天的消息，最多扫 max_messages 条（scan 为别名）。
        """
        max_messages = int(scan or max_messages)
        since = time.time() - days * 86400
        found: dict[int, dict[str, Any]] = {}
        messages: list[dict[str, Any]] = []
        scanned = 0
        reached_window = False
        async with self.session(account_id) as client:
            entity = await client.get_entity(chat)
            title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(chat)
            async for msg in client.iter_messages(entity, limit=max_messages):
                scanned += 1
                ts = msg.date.timestamp() if msg.date else 0.0
                if ts and ts < since:
                    reached_window = True
                    break
                sender_id = getattr(msg, "sender_id", None)
                if not sender_id or sender_id < 0:
                    continue
                # 对话记录：消息此刻就在手里，顺手存下来，不需要二次拉取。
                # 放在去重分支之前，否则同一人的第二条起都会被 continue 掉。
                if capture_text and len(messages) < text_limit:
                    body = getattr(msg, "message", None) or getattr(msg, "text", None)
                    kind = "text"
                    if getattr(msg, "media", None) is not None:
                        kind = type(msg.media).__name__.replace("MessageMedia", "").lower() or "media"
                    if body or kind != "text":
                        messages.append({
                            "msg_id": getattr(msg, "id", None),
                            "user_id": sender_id,
                            "text": body,
                            "kind": kind,
                            "reply_to": getattr(msg, "reply_to_msg_id", None),
                            "date": ts or None,
                        })
                hit = found.get(sender_id)
                if hit:
                    hit["msg_count"] += 1
                    continue
                try:
                    sender = await msg.get_sender()
                except Exception:  # noqa: BLE001
                    sender = None
                if sender is None or getattr(sender, "deleted", False):
                    continue
                if skip_bots and getattr(sender, "bot", False):
                    continue
                if skip_premium and getattr(sender, "premium", False):
                    continue
                name = " ".join(x for x in [getattr(sender, "first_name", None),
                                            getattr(sender, "last_name", None)] if x)
                found[sender_id] = {
                    "user_id": sender_id,
                    "username": getattr(sender, "username", None),
                    "name": name or None,
                    "msg_count": 1,
                    "last_msg_at": ts or None,
                }
                if len(found) >= limit:
                    break
        users = sorted(found.values(), key=lambda x: x["last_msg_at"] or 0, reverse=True)
        self.db.log(account_id, "collect_recent", True,
                    f"{title} days={days:g} scanned={scanned} found={len(users)}")
        return {"ok": True, "chat": str(chat), "title": title, "days": days,
                "scanned": scanned, "count": len(users),
                "covered_window": reached_window, "users": users,
                "messages": messages}

    async def update_profile(
        self, account_id: int, first_name: str | None = None,
        last_name: str | None = None, about: str | None = None,
    ) -> dict[str, Any]:
        from telethon import functions

        async with self.session(account_id) as client:
            await client(functions.account.UpdateProfileRequest(
                first_name=first_name, last_name=last_name, about=about
            ))
            self.db.log(account_id, "update_profile", True)
            return {"ok": True}

    @staticmethod
    def _auth_ts(value: Any) -> float | None:
        """Telethon 返回 datetime，统一转成 epoch 秒。"""
        if value is None:
            return None
        if hasattr(value, "timestamp"):
            try:
                return float(value.timestamp())
            except Exception:
                return None
        try:
            return float(value)
        except Exception:
            return None

    # 早于这个时间的“登录时间”一定是脏数据（Telegram 偶尔会返回 0 / 空 date_created）
    MIN_SANE_TS = 1_262_304_000.0  # 2010-01-01

    def _apply_server_login_at(self, account_id: int, real: float | None) -> float | None:
        """把服务端的 date_created 回写成 login_at，并挡掉明显不合理的值。

        以前这里无条件覆盖：只要服务端返回 0 或未来时间，login_at 就会被写脏，
        UI 上“下次自动清设备”随即变成一个莫名其妙的过去时间。
        """
        if real is None:
            return None
        now = time.time()
        if real < self.MIN_SANE_TS or real > now + 300:
            self.db.log(account_id, "sync_login_at", False,
                        f"服务端返回的登录时间不合理（{real}），已忽略")
            return None
        acc = self.db.get(account_id)
        if acc is None:
            return real
        old = float(acc.login_at or 0.0)
        if not old or abs(old - real) > 60:
            self.db.update(account_id, login_at=real)
            if old and old - real > 3600:
                self.db.log(
                    account_id, "sync_login_at", True,
                    "登录时间按服务端 date_created 回拨 %.1f 小时（本地存的是导入时间，"
                    "服务端才是真实登录时间），下次清设备时间会随之提前"
                    % ((old - real) / 3600),
                )
        return real

    async def _sync_login_at_with(self, account_id: int, client: Any) -> float | None:
        """从 Telegram 拿当前会话的真实创建时间（date_created）并回写 login_at。

        导入 tdata / StringSession 时本地只知道“导入时间”，不是真正的登录时间；
        服务端的 authorization.date_created 才是权威值，重启也不会变。
        """
        from telethon import functions

        try:
            res = await client(functions.account.GetAuthorizationsRequest())
        except Exception:
            return None
        cur = next((a for a in res.authorizations if getattr(a, "current", False)), None)
        real = self._auth_ts(getattr(cur, "date_created", None)) if cur else None
        return self._apply_server_login_at(account_id, real)

    async def sync_login_at(self, account_id: int) -> dict[str, Any]:
        """单独拉一次真实登录时间（会建立一次连接）。"""
        async with self.session(account_id) as client:
            real = await self._sync_login_at_with(account_id, client)
        return {"account_id": account_id, "login_at": real}

    async def list_sessions(self, account_id: int) -> list[dict[str, Any]]:
        """列出该账号已登录的其他设备（安全审计用）。"""
        self._require_session(account_id)
        from telethon import functions

        async with self.session(account_id) as client:
            res = await client(functions.account.GetAuthorizationsRequest())
            cur = next((a for a in res.authorizations if getattr(a, "current", False)), None)
            real = self._auth_ts(getattr(cur, "date_created", None)) if cur else None
            self._apply_server_login_at(account_id, real)
            return [{
                "hash": a.hash,
                "device": a.device_model,
                "platform": a.platform,
                "app": f"{a.app_name} {a.app_version}",
                "ip": a.ip,
                "country": a.country,
                "current": a.current,
                "date_created": str(getattr(a, "date_created", "") or ""),
                "date_active": str(a.date_active),
            } for a in res.authorizations]

    @staticmethod
    def _others(auths: Any) -> dict[int, str]:
        """除本机外的会话：hash -> 人读描述。"""
        out: dict[int, str] = {}
        for a in auths:
            if getattr(a, "current", False):
                continue
            desc = " ".join(str(x) for x in (
                getattr(a, "app_name", "") or "", getattr(a, "device_model", "") or "",
                getattr(a, "platform", "") or "", getattr(a, "country", "") or "",
            ) if x).strip()
            out[getattr(a, "hash", 0)] = desc or "未知客户端"
        return out

    @staticmethod
    def _kick_report(before: dict[int, str], after: dict[int, str]) -> dict[str, Any]:
        """对比踢之前/之后的会话列表，得出“到底踢掉了没有”的硬结论。"""
        removed = [d for h, d in before.items() if h not in after]
        left = [d for h, d in after.items() if h in before]
        added = [d for h, d in after.items() if h not in before]
        return {
            "verified": not after,          # 服务端重查后确实一个外部会话都不剩
            "before_others": len(before),
            "after_others": len(after),
            "removed": removed,
            "left": left,
            "reappeared": added,            # 踢完又冲进来的，说明对方手里有密码/发卡平台在重登
        }

    async def terminate_other_sessions(self, account_id: int,
                                       verify: bool = True,
                                       settle_s: float = 2.0) -> dict[str, Any]:
        """踢掉除本机外的全部会话，并回拉一次会话列表确认真的踢掉了。

        ResetAuthorizations 返回 True 只代表“请求被受理”，不代表设备真的没了：
        24 小时保护期、对方手里有密码立刻重登、发卡平台自动重登，都会让“成功”变成假的。
        """
        self._require_session(account_id)
        from telethon import functions

        async with self.session(account_id) as client:
            before = self._others(
                (await client(functions.account.GetAuthorizationsRequest())).authorizations
            )
            if not before:
                self.db.log(account_id, "terminate_sessions", True, "本来就只有本机一个会话")
                return {"ok": True, "verified": True, "before_others": 0,
                        "after_others": 0, "removed": [], "left": [], "reappeared": []}

            try:
                await client(functions.auth.ResetAuthorizationsRequest())
            except Exception as exc:
                name = type(exc).__name__
                # 本机会话不足 24 小时，Telegram 拒绝执行——这是可重试的，不是硬错
                fresh = "FreshResetAuthorisationForbidden" in name or "FreshReset" in name
                self.db.log(account_id, "terminate_sessions", False, f"{name}: {exc}"[:500])
                return {"ok": False, "verified": False, "retryable": fresh,
                        "error": name, "error_detail": str(exc)[:300],
                        "before_others": len(before), "after_others": len(before),
                        "removed": [], "left": list(before.values()), "reappeared": []}

            if not verify:
                self.db.log(account_id, "terminate_sessions", True, "已发送清理请求（未校验）")
                return {"ok": True, "verified": None, "before_others": len(before)}

            await asyncio.sleep(max(0.0, settle_s))   # 给服务端一点生效时间
            after = self._others(
                (await client(functions.account.GetAuthorizationsRequest())).authorizations
            )

        rep = self._kick_report(before, after)
        if rep["verified"]:
            detail = "已清理并校验：踢掉 %d 个（%s），现只剩本机" % (
                len(rep["removed"]), "、".join(rep["removed"])[:200])
        else:
            detail = "校验未通过：还剩 %d 个外部会话（%s）%s" % (
                rep["after_others"], "、".join(rep["left"] + rep["reappeared"])[:200],
                "，其中有新出现的登录，对方可能持有密码或在自动重登" if rep["reappeared"] else "")
        self.db.log(account_id, "terminate_sessions", rep["verified"], detail)
        return {"ok": rep["verified"], "retryable": not rep["verified"], **rep}

    # ---------- 批量调度 ----------
    async def run_batch(
        self,
        account_ids: list[int],
        task: Callable[[int], Awaitable[Any]],
        concurrency: int | None = None,
        stagger: bool = True,
    ) -> list[dict[str, Any]]:
        """受控并发 + 随机间隔的批量执行器。

        并发度优先级：参数 > 环境变量 TAM_BATCH_CONCURRENCY > 默认 3。
        用 is None 判空，因为 0 是合法入参（夹成 1，即完全串行）。
        """
        if concurrency is None:
            try:
                concurrency = int(os.getenv("TAM_BATCH_CONCURRENCY", "3"))
            except ValueError:
                concurrency = 3
        concurrency = max(1, min(int(concurrency), 32))
        sem = asyncio.Semaphore(concurrency)
        # 按下标预分配再回填。原来用 append 收集，顺序取决于谁先跑完，
        # 网页上按账号列表逐行展示结果时就会错位到别的号头上。
        results: list[dict[str, Any]] = [{} for _ in account_ids]

        async def worker(idx: int, aid: int) -> None:
            async with sem:
                if stagger:
                    await human_delay(self.s.action_min_delay, self.s.action_max_delay)
                try:
                    results[idx] = {"account_id": aid, "ok": True,
                                    "result": await task(aid)}
                except Exception as exc:
                    results[idx] = {"account_id": aid, "ok": False,
                                    "error": repr(exc)}

        await asyncio.gather(
            *(worker(i, a) for i, a in enumerate(account_ids)))
        return results


class _SessionCtx:
    """为单个账号创建一个临时客户端，自动限速、自动断开、自动回写 session。"""

    def __init__(self, mgr: AccountManager, account_id: int) -> None:
        self.mgr = mgr
        self.account_id = account_id
        self.client: Any = None
        self._acc: Account | None = None
        self._lock_held = False

    async def __aenter__(self) -> Any:
        await self.mgr._lock(self.account_id).acquire()
        self._lock_held = True
        try:
            await self.mgr.limiter.wait(self.account_id)
            acc = self.mgr.db.get(self.account_id)
            if acc is None:
                raise RuntimeError("账号不存在")
            if not acc.session_enc:
                raise RuntimeError("该账号尚未登录或会话已清除，请先登录/导入")
            self._acc = acc
            self.client = self.mgr._build_client(acc, self.mgr._load_session(acc))
            # 连接限时，避免无代理时 connection_retries 连挂数分钟
            try:
                await asyncio.wait_for(self.client.connect(), timeout=25.0)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"连接 Telegram 失败：{type(exc).__name__}: {exc}"
                ) from exc
            # 未授权立刻失败，避免 dialogs/spam 等在无效 session 上空转超时
            try:
                ok = await asyncio.wait_for(
                    self.client.is_user_authorized(), timeout=10.0)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"检查授权状态失败：{type(exc).__name__}: {exc}"
                ) from exc
            if not ok:
                try:
                    self.mgr.db.update(
                        self.account_id, status="unauthorized",
                        status_note="session unauthorized",
                        last_check_at=time.time(),
                    )
                except Exception:  # noqa: BLE001
                    pass
                raise RuntimeError("会话未授权或已失效，请重新登录/导入")
            return self.client
        except Exception:
            await self._safe_disconnect(save=False)
            if self._lock_held:
                self.mgr._lock(self.account_id).release()
                self._lock_held = False
            raise

    async def _safe_disconnect(self, *, save: bool) -> None:
        if self.client is None:
            return
        try:
            if save and self._acc is not None:
                try:
                    if self.client.session.save():
                        self.mgr._save_session(
                            self._acc, self.client.session.save())
                except Exception:  # noqa: BLE001
                    pass
            if self.client.is_connected():
                await self.client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.client = None

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            # 仅在仍授权时回写 session；未授权/异常路径不把坏状态盖掉
            await self._safe_disconnect(save=exc_type is None)
        finally:
            if self._lock_held:
                self.mgr._lock(self.account_id).release()
                self._lock_held = False
