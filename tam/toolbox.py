"""工具箱：把 GAF 机器人那 12 项功能，改成直接作用于库里已有的号。

原版每个功能都是「让用户上传 session 压缩包 -> 临时连一次 -> 断开」，
每个模块里都抄了一遍 connect / is_user_authorized / get_me / disconnect。
这里全部换成 manager.session(account_id)：加锁、限速、连接、断开、
session 回写都是它自动做的，不用再抄一遍，也不会出现两处并发连同一个号
（Telegram 对同一 authkey 并发很敏感，会掉登录）。

约定：
- 每个 op 是 async def op(client, params) -> dict，只管业务，不管连接。
- telethon 一律在函数内部懒导入，这样没装 telethon 也能 import 本模块
  （便于纯逻辑单测和 py_compile 校验）。
- 返回值必须是能 JSON 序列化的普通字典，网页要直接展示。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

__all__ = ["OP_SPECS", "OPS", "get_spec", "validate_params", "run_op",
           "run_op_batch", "ToolboxError"]


class ToolboxError(Exception):
    """工具箱参数或执行错误。"""


# --------------------------------------------------------------------------
# 各功能实现
# --------------------------------------------------------------------------

async def op_alive(client: Any, p: dict) -> dict:
    """筛活：号还能不能用。

    原版用 GetAppConfigRequest 探测。这个请求很轻，不产生任何可见行为，
    比发消息安全得多。加超时是因为死号常常是连上了但一直不回包。
    """
    from telethon.tl.functions.help import GetAppConfigRequest

    timeout = float(p.get("timeout") or 10)
    if not await client.is_user_authorized():
        return {"alive": False, "reason": "未授权（session 已失效）"}
    try:
        await asyncio.wait_for(client(GetAppConfigRequest(hash=0)),
                               timeout=timeout)
    except asyncio.TimeoutError:
        return {"alive": False, "reason": f"{timeout:g}s 内无响应"}
    me = await client.get_me()
    return {"alive": True, "user_id": getattr(me, "id", None),
            "username": getattr(me, "username", None),
            "phone": getattr(me, "phone", None)}


async def op_twofa(client: Any, p: dict) -> dict:
    """改二步验证密码。

    Telethon 的 edit_2fa 有两个坑，都得绕：
    1. 号上没有二验时，绝对不能传 current_password —— 传了就变成
       「用这个密码去关闭二验」，语义完全不同。
    2. 无二验的号传个错密码去关闭，它会返回 True（Telethon #4526），
       所以不能拿返回值当成功判据，得回头再查一次真实状态。
    """
    from telethon.tl.functions.account import GetPasswordRequest

    old = (p.get("old") or "").strip()
    new = (p.get("new") or "").strip()
    hint = (p.get("hint") or "").strip()
    if not new and not old:
        raise ToolboxError("新密码和当前密码不能都为空")

    pwd = await client(GetPasswordRequest())
    had = bool(getattr(pwd, "has_password", False))

    kwargs: dict[str, Any] = {}
    if had:
        if not old:
            raise ToolboxError("这个号已经设过二验，必须填当前密码")
        kwargs["current_password"] = old
    elif old:
        # 号上没有二验却填了当前密码，直接拦下来，不然会被当成「关闭二验」
        raise ToolboxError("这个号还没有二验，当前密码要留空")
    kwargs["new_password"] = new or None
    if new and hint:
        kwargs["hint"] = hint

    await client.edit_2fa(**kwargs)

    # 不信返回值，回头查真实状态
    after = await client(GetPasswordRequest())
    now_has = bool(getattr(after, "has_password", False))
    want = bool(new)
    if now_has != want:
        raise ToolboxError(
            "改完之后状态不对（期望 "
            f"{'有密码' if want else '无密码'}，实际 "
            f"{'有密码' if now_has else '无密码'}），当前密码可能填错了")
    return {"had_password": had, "has_password": now_has,
            "action": "设置" if want else "移除"}



def _dt_ts(v) -> float | None:
    """datetime / 时间戳 → unix 秒；无法解析则 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v.timestamp())
    except Exception:
        return None


def _password_snapshot(pwd: Any) -> dict[str, Any]:
    """从 account.Password 抽出可 JSON 序列化的二验状态。

    has_recovery：是否绑定了二验恢复邮箱（辅助邮箱）。只读探测，
    不触发发信；真实邮箱地址 Telegram 不会下发，最多给 pattern。
    """
    pending = getattr(pwd, "pending_reset_date", None)
    pending_ts = _dt_ts(pending)
    return {
        "has_password": bool(getattr(pwd, "has_password", False)),
        "has_recovery": bool(getattr(pwd, "has_recovery", False)),
        "has_secure_values": bool(getattr(pwd, "has_secure_values", False)),
        "hint": getattr(pwd, "hint", None) or "",
        "email_unconfirmed_pattern": getattr(pwd, "email_unconfirmed_pattern", None) or "",
        "login_email_pattern": getattr(pwd, "login_email_pattern", None) or "",
        "pending_reset": pending_ts is not None,
        "pending_reset_date": pending_ts,
        "pending_reset_iso": (
            pending.isoformat() if hasattr(pending, "isoformat") and pending else None
        ),
    }


async def op_twofa_status(client: Any, p: dict) -> dict:
    """查二验状态：有无云密码、有无辅助邮箱、是否已在重置等待期。

    只调用 GetPasswordRequest，不改任何设置、不发邮件。
    有辅助邮箱时「发起重置」仍可用官方等待期流程；邮箱恢复是另一条路径，
    本工具不代收恢复码。
    """
    from telethon.tl.functions.account import GetPasswordRequest

    pwd = await client(GetPasswordRequest())
    snap = _password_snapshot(pwd)
    note_parts = []
    if not snap["has_password"]:
        note_parts.append("未开启二步验证")
    else:
        note_parts.append("已开启二步验证")
        if snap["has_recovery"]:
            note_parts.append("已绑定辅助邮箱（可走邮箱恢复；原持有人可能仍能找回）")
        else:
            note_parts.append("未绑定辅助邮箱")
        if snap["pending_reset"]:
            note_parts.append(
                f"已申请重置，等待至 {snap.get('pending_reset_iso') or snap['pending_reset_date']}"
            )
    snap["note"] = "；".join(note_parts)
    return snap


async def op_twofa_reset(client: Any, p: dict) -> dict:
    """发起二验重置（官方等待期，通常约 7 天）。

    对应 account.resetPassword：
    - ResetPasswordOk：立即清掉（极少见）
    - ResetPasswordRequestedWait：已进入等待，until_date 到期后密码失效
    - ResetPasswordFailedWait：距上次申请太近，需等到 retry_date 再试

    有无辅助邮箱都不阻塞本接口；辅助邮箱只影响「对方能否邮箱恢复」。
    返回体里会附带当前 has_recovery 供前端展示。
    """
    from telethon.tl.functions.account import GetPasswordRequest, ResetPasswordRequest
    from telethon.tl.types.account import (
        ResetPasswordFailedWait,
        ResetPasswordOk,
        ResetPasswordRequestedWait,
    )

    if not p.get("confirm"):
        raise ToolboxError("发起二验重置不可随意点，必须勾选确认")

    before = _password_snapshot(await client(GetPasswordRequest()))
    if not before["has_password"]:
        raise ToolboxError("这个号没有开启二步验证，无需重置")
    if before["pending_reset"]:
        # 已在等待期再点一次，Telegram 常返回 FailedWait 或原 until
        pass

    result = await client(ResetPasswordRequest())
    after = _password_snapshot(await client(GetPasswordRequest()))

    out: dict[str, Any] = {
        "action": "reset",
        "before": before,
        "after": after,
        "has_recovery": after.get("has_recovery", before.get("has_recovery")),
        "has_password": after.get("has_password", True),
    }

    if isinstance(result, ResetPasswordOk):
        out["result"] = "ok"
        out["note"] = "二验已立即清除（ResetPasswordOk）"
        out["has_password"] = False
    elif isinstance(result, ResetPasswordRequestedWait):
        until = getattr(result, "until_date", None)
        until_ts = _dt_ts(until)
        out["result"] = "waiting"
        out["until_date"] = until_ts
        out["until_iso"] = until.isoformat() if hasattr(until, "isoformat") and until else None
        out["note"] = (
            f"已申请重置，需等待至 {out['until_iso'] or until_ts} 后二验才会失效"
        )
    elif isinstance(result, ResetPasswordFailedWait):
        retry = getattr(result, "retry_date", None)
        retry_ts = _dt_ts(retry)
        out["result"] = "failed_wait"
        out["retry_date"] = retry_ts
        out["retry_iso"] = retry.isoformat() if hasattr(retry, "isoformat") and retry else None
        out["note"] = (
            f"暂不可再次申请，请等到 {out['retry_iso'] or retry_ts} 后再试"
        )
    else:
        out["result"] = type(result).__name__
        out["note"] = f"未知返回：{type(result).__name__}"

    if after.get("has_recovery"):
        out["note"] = (out.get("note") or "") + "；该号绑定了辅助邮箱，原持有人仍可能通过邮箱恢复二验"
    return out


async def op_twofa_reset_cancel(client: Any, p: dict) -> dict:
    """取消进行中的二验重置等待（account.declinePasswordReset）。"""
    from telethon.tl.functions.account import (
        DeclinePasswordResetRequest,
        GetPasswordRequest,
    )

    if not p.get("confirm"):
        raise ToolboxError("取消重置必须勾选确认")

    before = _password_snapshot(await client(GetPasswordRequest()))
    if not before["pending_reset"]:
        raise ToolboxError("当前没有进行中的二验重置申请")

    await client(DeclinePasswordResetRequest())
    after = _password_snapshot(await client(GetPasswordRequest()))
    return {
        "action": "cancel_reset",
        "before": before,
        "after": after,
        "pending_reset": after["pending_reset"],
        "note": (
            "已取消重置等待"
            if not after["pending_reset"]
            else "已请求取消，但 pending_reset_date 仍在，请稍后复查"
        ),
    }


# 隐私项 -> Telethon 的 key 类名
_PRIVACY_KEYS = {
    "last_seen": "InputPrivacyKeyStatusTimestamp",
    "phone": "InputPrivacyKeyPhoneNumber",
    "invite": "InputPrivacyKeyChatInvite",
    "avatar": "InputPrivacyKeyProfilePhoto",
    "call": "InputPrivacyKeyPhoneCall",
    "forward": "InputPrivacyKeyForwards",
}
# 取值 -> Telethon 的 value 类名
_PRIVACY_VALUES = {
    "everybody": "InputPrivacyValueAllowAll",
    "contacts": "InputPrivacyValueAllowContacts",
    "nobody": "InputPrivacyValueDisallowAll",
}


async def op_privacy(client: Any, p: dict) -> dict:
    """批量设隐私。防骚扰、防被搜到，养号必做。

    不传 items 就用一套保守默认：谁都看不到手机号和最后上线，
    只有联系人能拉进群、能看头像。
    """
    from telethon.tl import types
    from telethon.tl.functions.account import SetPrivacyRequest

    items = p.get("items")
    if not isinstance(items, dict) or not items:
        items = {"phone": "nobody", "last_seen": "nobody",
                 "invite": "contacts", "avatar": "contacts"}

    done: dict[str, str] = {}
    failed: dict[str, str] = {}
    for name, val in items.items():
        kcls = _PRIVACY_KEYS.get(str(name))
        vcls = _PRIVACY_VALUES.get(str(val))
        if not kcls:
            failed[str(name)] = f"不认识的隐私项：{name}"
            continue
        if not vcls:
            failed[str(name)] = f"不认识的取值：{val}（只能是 everybody/contacts/nobody）"
            continue
        try:
            await client(SetPrivacyRequest(getattr(types, kcls)(),
                                           [getattr(types, vcls)()]))
            done[str(name)] = str(val)
        except Exception as exc:  # noqa: BLE001 - 单项失败不影响其他项
            failed[str(name)] = repr(exc)
    return {"applied": done, "failed": failed}


async def op_contacts_clear(client: Any, p: dict) -> dict:
    """清空通讯录。号是二手来的时候，前手的联系人必须清掉。"""
    from telethon.tl.functions.contacts import (DeleteContactsRequest,
                                                GetContactsRequest)

    res = await client(GetContactsRequest(hash=0))
    users = list(getattr(res, "users", []) or [])
    if not users:
        return {"total": 0, "deleted": 0}
    if p.get("dry_run"):
        return {"total": len(users), "deleted": 0, "dry_run": True}
    # 一次删太多容易触发限制，切片分批
    step = 100
    deleted = 0
    for i in range(0, len(users), step):
        chunk = users[i:i + step]
        await client(DeleteContactsRequest(id=chunk))
        deleted += len(chunk)
    return {"total": len(users), "deleted": deleted}


async def op_dialogs_clear(client: Any, p: dict) -> dict:
    """清会话列表。默认只退群/频道，不碰私聊——私聊里可能有验证码和找回信息。"""
    keep_private = not p.get("include_private")
    limit = int(p.get("limit") or 0)

    left = 0
    kept = 0
    errors: list[str] = []
    async for d in client.iter_dialogs():
        if keep_private and getattr(d, "is_user", False):
            kept += 1
            continue
        if limit and left >= limit:
            break
        try:
            await client.delete_dialog(d.id)
            left += 1
        except Exception as exc:  # noqa: BLE001 - 单个会话失败不中断
            errors.append(f"{getattr(d, 'name', d.id)}: {exc!r}")
    return {"removed": left, "kept_private": kept, "errors": errors[:20]}


async def op_spam_check(client: Any, p: dict) -> dict:
    """问 @SpamBot 有没有被限制。就是跟机器人发一句 /start 看它怎么回。"""
    bot = await client.get_entity("SpamBot")
    await client.send_message(bot, "/start")
    await asyncio.sleep(float(p.get("wait") or 3))
    text = ""
    async for m in client.iter_messages(bot, limit=1):
        text = m.message or ""
    low = text.lower()
    # SpamBot 回的是自然语言，只能靠关键词判断
    if not text:
        state = "unknown"
    elif "no limits" in low or "good news" in low or "没有限制" in text:
        state = "free"
    else:
        state = "limited"
    return {"state": state, "reply": text[:1000]}



async def op_okpay_balance(client: Any, p: dict) -> dict:
    """私聊 OKPay 钱包机器人查余额：自动点选 USDT/TRX/CNY 等菜单。"""
    from .manager import probe_okpay_balance

    bot = (p.get("bot") or "Okpay").lstrip("@").strip() or "Okpay"
    wait = float(p.get("wait") or 4)
    return await probe_okpay_balance(client, bot=bot, wait=wait)

async def op_check_phones(client: Any, p: dict) -> dict:
    """筛料：一批手机号里哪些注册过 Telegram。

    做法是导入成联系人看谁能匹配上，查完立刻删掉——不删的话通讯录会
    被这批号污染，而且等于把料留在号上。

    注意 users 为空是正常情况：对方开了「谁能通过手机号找到我」的限制时
    就匹配不上，不代表号不存在。
    """
    from telethon.tl.functions.contacts import (DeleteContactsRequest,
                                                ImportContactsRequest)
    from telethon.tl.types import InputPhoneContact

    raw = p.get("phones") or []
    if isinstance(raw, str):
        raw = [x for x in raw.replace(",", "\n").split("\n")]
    phones = [str(x).strip() for x in raw if str(x).strip()]
    if not phones:
        raise ToolboxError("没有要查的手机号")
    if len(phones) > 500:
        raise ToolboxError(f"一次最多查 500 个，收到 {len(phones)} 个")

    contacts = [InputPhoneContact(client_id=i, phone=ph,
                                  first_name=f"q{i}", last_name="")
                for i, ph in enumerate(phones)]
    res = await client(ImportContactsRequest(contacts))
    users = list(getattr(res, "users", []) or [])

    # 立刻清掉刚导进去的，别把料留在号的通讯录里
    if users:
        try:
            await client(DeleteContactsRequest(id=users))
        except Exception:  # noqa: BLE001 - 清理失败不影响查询结果
            pass

    hit = {}
    for u in users:
        ph = getattr(u, "phone", None)
        if ph:
            hit[str(ph).lstrip("+")] = {
                "user_id": getattr(u, "id", None),
                "username": getattr(u, "username", None),
            }
    found = []
    missing = []
    for ph in phones:
        key = ph.lstrip("+")
        if key in hit:
            found.append({"phone": ph, **hit[key]})
        else:
            missing.append(ph)
    return {"total": len(phones), "found": found, "missing": missing,
            "note": "没匹配上不一定是没注册，也可能是对方限制了手机号搜索"}


async def op_profile_clear(client: Any, p: dict) -> dict:
    """防找回：清掉能被前手认出来的痕迹（头像、简介、用户名）。"""
    from telethon.tl.functions.account import (UpdateProfileRequest,
                                               UpdateUsernameRequest)
    from telethon.tl.functions.photos import (DeletePhotosRequest,
                                              GetUserPhotosRequest)

    done = []
    errors = []
    if p.get("photos", True):
        try:
            got = await client(GetUserPhotosRequest(
                user_id="me", offset=0, max_id=0, limit=100))
            photos = list(getattr(got, "photos", []) or [])
            if photos:
                await client(DeletePhotosRequest(id=photos))
            done.append(f"删了 {len(photos)} 张头像")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"头像: {exc!r}")
    if p.get("bio", True):
        try:
            await client(UpdateProfileRequest(about=""))
            done.append("清空简介")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"简介: {exc!r}")
    if p.get("username"):
        try:
            await client(UpdateUsernameRequest(username=""))
            done.append("清空用户名")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"用户名: {exc!r}")
    return {"done": done, "errors": errors}


async def op_terminate_others(client: Any, p: dict) -> dict:
    """踢掉其它设备，只留本机。

    跟 /devices/terminate 是同一件事，这里只是让它能进批量流程。
    照例踢完回查一遍，不看返回值——Telegram 说成功但设备还在是常事。
    """
    from telethon.tl.functions.account import (GetAuthorizationsRequest,
                                               ResetAuthorizationsRequest)

    before = await client(GetAuthorizationsRequest())
    n_before = len(getattr(before, "authorizations", []) or [])
    await client(ResetAuthorizationsRequest())
    after = await client(GetAuthorizationsRequest())
    rest = [a for a in (getattr(after, "authorizations", []) or [])
            if not getattr(a, "current", False)]
    return {"before": n_before, "remaining_others": len(rest),
            "ok": not rest}


async def op_logout(client: Any, p: dict) -> dict:
    """退出登录：作废当前 session，不解绑手机号、不删除 Telegram 账号。"""
    if not p.get("confirm"):
        raise ToolboxError("这是不可逆操作，必须勾选确认")
    ok = await client.log_out()
    return {"logged_out": bool(ok), "note": "仅作废 session，Telegram 账号仍在"}


async def op_delete_tg_account(client: Any, p: dict) -> dict:
    """向 Telegram 申请注销账号（真正删号）。必须确认。"""
    from telethon.tl.functions.account import DeleteAccountRequest

    if not p.get("confirm"):
        raise ToolboxError("注销 Telegram 账号不可逆，必须勾选确认")
    reason = (p.get("reason") or "User requested deletion").strip()[:128]
    ok = bool(await client(DeleteAccountRequest(reason=reason)))
    return {
        "remote_deleted": ok,
        "reason": reason,
        "note": "已提交注销；请在网页端同步清理本地记录",
    }


# --------------------------------------------------------------------------
# 注册表：网页端靠这张表动态渲染表单，加功能不用改前端
# --------------------------------------------------------------------------

async def op_block_users(client: Any, p: dict) -> dict:
    """屏蔽用户。target 支持 @用户名、用户ID、手机号，多行或逗号分隔。"""
    from telethon.tl.functions.contacts import BlockRequest

    targets = _parse_peer_list(p.get("targets") or p.get("target") or "")
    if not targets:
        raise ToolboxError("请填写要屏蔽的用户（@用户名 / ID / 手机号）")
    if len(targets) > 200:
        raise ToolboxError(f"一次最多 200 个，收到 {len(targets)} 个")

    ok_list: list[dict] = []
    fail_list: list[dict] = []
    for raw in targets:
        try:
            entity = await client.get_entity(raw)
            await client(BlockRequest(id=entity))
            ok_list.append({
                "target": str(raw),
                "user_id": getattr(entity, "id", None),
                "username": getattr(entity, "username", None),
            })
        except Exception as exc:  # noqa: BLE001
            fail_list.append({"target": str(raw), "error": f"{type(exc).__name__}: {exc}"})
    return {
        "total": len(targets),
        "blocked": len(ok_list),
        "failed": len(fail_list),
        "ok": ok_list[:50],
        "errors": fail_list[:30],
    }


async def op_unblock_users(client: Any, p: dict) -> dict:
    """取消屏蔽用户。"""
    from telethon.tl.functions.contacts import UnblockRequest

    targets = _parse_peer_list(p.get("targets") or p.get("target") or "")
    if not targets:
        raise ToolboxError("请填写要取消屏蔽的用户（@用户名 / ID / 手机号）")
    if len(targets) > 200:
        raise ToolboxError(f"一次最多 200 个，收到 {len(targets)} 个")

    ok_list: list[dict] = []
    fail_list: list[dict] = []
    for raw in targets:
        try:
            entity = await client.get_entity(raw)
            await client(UnblockRequest(id=entity))
            ok_list.append({
                "target": str(raw),
                "user_id": getattr(entity, "id", None),
                "username": getattr(entity, "username", None),
            })
        except Exception as exc:  # noqa: BLE001
            fail_list.append({"target": str(raw), "error": f"{type(exc).__name__}: {exc}"})
    return {
        "total": len(targets),
        "unblocked": len(ok_list),
        "failed": len(fail_list),
        "ok": ok_list[:50],
        "errors": fail_list[:30],
    }


def _parse_peer_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw]
    else:
        text = str(raw).replace(",", "\n").replace(";", "\n")
        items = [x.strip() for x in text.splitlines()]
    out: list[Any] = []
    for x in items:
        if not x:
            continue
        if x.isdigit() or (x.startswith("-") and x[1:].isdigit()):
            out.append(int(x))
        else:
            out.append(x[1:] if x.startswith("@") else x)
    return out


async def op_search_public(client: Any, p: dict) -> dict:
    """搜索公开用户/群/频道（contacts.Search）。"""
    from telethon.tl.functions.contacts import SearchRequest

    q = str(p.get("query") or "").strip()
    if not q:
        raise ToolboxError("请填写搜索关键词")
    limit = int(p.get("limit") or 20)
    limit = max(1, min(limit, 50))
    res = await client(SearchRequest(q=q, limit=limit))
    users = []
    for u in list(getattr(res, "users", []) or [])[:limit]:
        users.append({
            "id": getattr(u, "id", None),
            "username": getattr(u, "username", None),
            "first_name": getattr(u, "first_name", None),
            "last_name": getattr(u, "last_name", None),
            "bot": bool(getattr(u, "bot", False)),
        })
    chats = []
    for c in list(getattr(res, "chats", []) or [])[:limit]:
        chats.append({
            "id": getattr(c, "id", None),
            "title": getattr(c, "title", None),
            "username": getattr(c, "username", None),
            "megagroup": bool(getattr(c, "megagroup", False)),
            "broadcast": bool(getattr(c, "broadcast", False)),
            "participants_count": getattr(c, "participants_count", None),
        })
    return {"query": q, "users": users, "chats": chats,
            "user_count": len(users), "chat_count": len(chats)}


def _parse_invite_hash(link: str) -> str | None:
    s = (link or "").strip()
    if not s:
        return None
    for prefix in (
        "https://t.me/+", "http://t.me/+", "t.me/+",
        "https://telegram.me/+", "https://t.me/joinchat/",
        "http://t.me/joinchat/", "t.me/joinchat/",
    ):
        if s.startswith(prefix):
            return s[len(prefix):].split("/")[0].split("?")[0]
    if s.startswith("+"):
        return s[1:]
    return None


async def op_join_chat(client: Any, p: dict) -> dict:
    """加入公开群/频道或邀请链接。"""
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import ImportChatInviteRequest

    target = str(p.get("target") or p.get("link") or "").strip()
    if not target:
        raise ToolboxError("请填写 @用户名 / 链接 / 邀请 hash")
    inv = _parse_invite_hash(target)
    if inv:
        try:
            res = await client(ImportChatInviteRequest(inv))
        except Exception as exc:  # noqa: BLE001
            raise ToolboxError(f"邀请链接加入失败：{type(exc).__name__}: {exc}") from exc
        chats = list(getattr(res, "chats", []) or [])
        c = chats[0] if chats else None
        return {
            "ok": True, "via": "invite",
            "id": getattr(c, "id", None),
            "title": getattr(c, "title", None),
            "username": getattr(c, "username", None),
        }
    # 公开 @username 或 t.me/name
    peer = target
    if "t.me/" in peer:
        peer = peer.rstrip("/").split("t.me/")[-1].split("?")[0]
        if peer.startswith("+") or peer.startswith("joinchat/"):
            raise ToolboxError("无法解析的邀请链接")
    peer = peer.lstrip("@")
    try:
        entity = await client.get_entity(peer)
        await client(JoinChannelRequest(entity))
    except Exception as exc:  # noqa: BLE001
        # 有些群用 messages.ImportChatInvite；普通群可能已在对话中
        try:
            entity = await client.get_entity(peer)
            from telethon.tl.functions.messages import ImportChatInviteRequest as _I  # noqa: F401
            # 尝试 CheckChatInvite 不可加入公开
            raise ToolboxError(f"加入失败：{type(exc).__name__}: {exc}") from exc
        except ToolboxError:
            raise
        except Exception as exc2:  # noqa: BLE001
            raise ToolboxError(f"加入失败：{type(exc2).__name__}: {exc2}") from exc2
    return {
        "ok": True, "via": "public",
        "id": getattr(entity, "id", None),
        "title": getattr(entity, "title", None) or getattr(entity, "first_name", None),
        "username": getattr(entity, "username", None),
    }


async def op_list_members(client: Any, p: dict) -> dict:
    """浏览群/频道成员（需有权限；大群可能受限制）。"""
    target = str(p.get("target") or "").strip()
    if not target:
        raise ToolboxError("请填写群 @用户名 / ID")
    limit = int(p.get("limit") or 100)
    limit = max(1, min(limit, 500))
    entity = await client.get_entity(target.lstrip("@") if not target.lstrip("-").isdigit() else int(target))
    members = []
    async for u in client.iter_participants(entity, limit=limit):
        members.append({
            "id": getattr(u, "id", None),
            "username": getattr(u, "username", None),
            "first_name": getattr(u, "first_name", None),
            "last_name": getattr(u, "last_name", None),
            "bot": bool(getattr(u, "bot", False)),
        })
    return {
        "target": target,
        "chat_id": getattr(entity, "id", None),
        "title": getattr(entity, "title", None),
        "count": len(members),
        "members": members,
    }


async def op_read_messages(client: Any, p: dict) -> dict:
    """读取群/频道/私聊最近消息文本。"""
    target = str(p.get("target") or "").strip()
    if not target:
        raise ToolboxError("请填写对话 @用户名 / ID")
    limit = int(p.get("limit") or 30)
    limit = max(1, min(limit, 200))
    search = str(p.get("search") or "").strip() or None
    raw = target.strip()
    if raw.lstrip("-").isdigit():
        entity = await client.get_entity(int(raw))
    else:
        entity = await client.get_entity(raw.lstrip("@"))
    messages = []
    kwargs = {"limit": limit}
    if search:
        kwargs["search"] = search
    async for m in client.iter_messages(entity, **kwargs):
        text = getattr(m, "message", None) or getattr(m, "text", None) or ""
        messages.append({
            "id": m.id,
            "date": m.date.isoformat() if getattr(m, "date", None) else None,
            "sender_id": getattr(m, "sender_id", None),
            "text": (text or "")[:2000],
            "out": bool(getattr(m, "out", False)),
        })
    return {
        "target": target,
        "chat_id": getattr(entity, "id", None),
        "title": getattr(entity, "title", None) or getattr(entity, "first_name", None),
        "count": len(messages),
        "messages": messages,
    }


def _resolve_entity(client, target: str):
    raw = str(target or "").strip()
    if not raw:
        raise ToolboxError("目标不能为空")
    if raw.lstrip("-").isdigit():
        return int(raw)
    return raw.lstrip("@")


async def op_delete_messages(client: Any, p: dict) -> dict:
    """删除/撤回消息。revoke=True 尽量对双方撤回（视会话类型）。"""
    target = str(p.get("target") or "").strip()
    ids_raw = p.get("message_ids") or p.get("ids") or ""
    if isinstance(ids_raw, list):
        ids = [int(x) for x in ids_raw]
    else:
        ids = [int(x) for x in str(ids_raw).replace(",", " ").split() if x.strip().lstrip("-").isdigit()]
    if not target or not ids:
        raise ToolboxError("需要 target 与 message_ids")
    if len(ids) > 100:
        raise ToolboxError("一次最多 100 条")
    revoke = bool(p.get("revoke", True))
    entity = await client.get_entity(_resolve_entity(client, target))
    result = await client.delete_messages(entity, ids, revoke=revoke)
    return {
        "ok": True, "target": target, "message_ids": ids, "revoke": revoke,
        "affected": len(ids),
        "result_type": type(result).__name__ if result is not None else None,
    }


async def op_edit_message(client: Any, p: dict) -> dict:
    """编辑自己发送的消息。"""
    target = str(p.get("target") or "").strip()
    mid = int(p.get("message_id") or p.get("id") or 0)
    text = str(p.get("text") or "")
    if not target or not mid:
        raise ToolboxError("需要 target 与 message_id")
    if not text.strip():
        raise ToolboxError("新文本不能为空")
    entity = await client.get_entity(_resolve_entity(client, target))
    msg = await client.edit_message(entity, mid, text)
    return {
        "ok": True, "target": target, "message_id": mid,
        "text": (getattr(msg, "message", None) or text)[:500],
    }


async def op_forward_messages(client: Any, p: dict) -> dict:
    """转发消息到另一会话。"""
    src = str(p.get("from_peer") or p.get("source") or "").strip()
    dst = str(p.get("to_peer") or p.get("target") or "").strip()
    ids_raw = p.get("message_ids") or p.get("ids") or ""
    if isinstance(ids_raw, list):
        ids = [int(x) for x in ids_raw]
    else:
        ids = [int(x) for x in str(ids_raw).replace(",", " ").split() if x.strip().lstrip("-").isdigit()]
    if not src or not dst or not ids:
        raise ToolboxError("需要 from_peer、to_peer、message_ids")
    if len(ids) > 50:
        raise ToolboxError("一次最多转发 50 条")
    from_e = await client.get_entity(_resolve_entity(client, src))
    to_e = await client.get_entity(_resolve_entity(client, dst))
    msgs = await client.forward_messages(to_e, ids, from_e)
    if not isinstance(msgs, list):
        msgs = [msgs] if msgs else []
    return {
        "ok": True, "from_peer": src, "to_peer": dst, "message_ids": ids,
        "forwarded": [getattr(m, "id", None) for m in msgs],
    }


async def op_reply_message(client: Any, p: dict) -> dict:
    """回复/引用某条消息（reply_to）。"""
    target = str(p.get("target") or "").strip()
    mid = int(p.get("message_id") or p.get("reply_to") or 0)
    text = str(p.get("text") or "")
    if not target or not mid or not text.strip():
        raise ToolboxError("需要 target、message_id、text")
    entity = await client.get_entity(_resolve_entity(client, target))
    msg = await client.send_message(entity, text, reply_to=mid)
    return {
        "ok": True, "target": target, "reply_to": mid,
        "message_id": getattr(msg, "id", None),
        "text": text[:500],
    }


async def op_read_message(client: Any, p: dict) -> dict:
    """标记对话已读到指定 message_id（含该条）。"""
    target = str(p.get("target") or "").strip()
    mid = int(p.get("message_id") or p.get("max_id") or 0)
    if not target or not mid:
        raise ToolboxError("需要 target 与 message_id")
    entity = await client.get_entity(_resolve_entity(client, target))
    await client.send_read_acknowledge(entity, max_id=mid)
    return {"ok": True, "target": target, "max_id": mid}


async def op_get_message(client: Any, p: dict) -> dict:
    """读取单条消息完整内容。"""
    target = str(p.get("target") or "").strip()
    mid = int(p.get("message_id") or 0)
    if not target or not mid:
        raise ToolboxError("需要 target 与 message_id")
    entity = await client.get_entity(_resolve_entity(client, target))
    msg = await client.get_messages(entity, ids=mid)
    if not msg:
        raise ToolboxError(f"消息 {mid} 不存在或不可见")
    text = getattr(msg, "message", None) or getattr(msg, "text", None) or ""
    return {
        "ok": True,
        "id": msg.id,
        "date": msg.date.isoformat() if getattr(msg, "date", None) else None,
        "sender_id": getattr(msg, "sender_id", None),
        "text": text[:8000],
        "out": bool(getattr(msg, "out", False)),
        "reply_to_msg_id": getattr(getattr(msg, "reply_to", None), "reply_to_msg_id", None),
        "grouped_id": getattr(msg, "grouped_id", None),
        "media": type(msg.media).__name__ if getattr(msg, "media", None) else None,
    }


async def op_create_group(client: Any, p: dict) -> dict:
    """创建群组（超级群 megagroup）或基础群。"""
    from telethon.tl.functions.channels import CreateChannelRequest
    from telethon.tl.functions.messages import CreateChatRequest

    title = str(p.get("title") or "").strip()
    if not title:
        raise ToolboxError("需要群标题 title")
    about = str(p.get("about") or "")[:255]
    megagroup = bool(p.get("megagroup", True))
    users_raw = p.get("users") or p.get("members") or ""
    if isinstance(users_raw, list):
        user_list = [str(x).strip() for x in users_raw if str(x).strip()]
    else:
        user_list = [x.strip() for x in str(users_raw).replace(",", "\n").splitlines() if x.strip()]

    if megagroup:
        res = await client(CreateChannelRequest(
            title=title, about=about, megagroup=True, broadcast=False,
        ))
        chats = list(getattr(res, "chats", []) or [])
        chat = chats[0] if chats else None
        invited = []
        if chat and user_list:
            from telethon.tl.functions.channels import InviteToChannelRequest
            entities = []
            for u in user_list[:50]:
                try:
                    entities.append(await client.get_entity(u.lstrip("@")))
                except Exception:  # noqa: BLE001
                    pass
            if entities:
                try:
                    await client(InviteToChannelRequest(chat, entities))
                    invited = [getattr(e, "id", None) for e in entities]
                except Exception as exc:  # noqa: BLE001
                    return {
                        "ok": True, "created": True, "megagroup": True,
                        "id": getattr(chat, "id", None),
                        "title": getattr(chat, "title", None),
                        "invite_error": f"{type(exc).__name__}: {exc}",
                    }
        return {
            "ok": True, "megagroup": True,
            "id": getattr(chat, "id", None),
            "title": getattr(chat, "title", None) or title,
            "invited": invited,
        }

    # 基础群至少需要一些用户；无用户则创建超级群更稳妥
    if not user_list:
        raise ToolboxError("基础群需要至少一个成员 users，或改用 megagroup=true")
    entities = []
    for u in user_list[:50]:
        entities.append(await client.get_entity(u.lstrip("@")))
    res = await client(CreateChatRequest(users=entities, title=title))
    chats = list(getattr(res, "chats", []) or [])
    chat = chats[0] if chats else None
    return {
        "ok": True, "megagroup": False,
        "id": getattr(chat, "id", None),
        "title": getattr(chat, "title", None) or title,
    }


async def op_leave_chat(client: Any, p: dict) -> dict:
    """离开群/频道。"""
    from telethon.tl.functions.channels import LeaveChannelRequest

    target = str(p.get("target") or "").strip()
    if not target:
        raise ToolboxError("需要 target")
    entity = await client.get_entity(_resolve_entity(client, target))
    try:
        await client(LeaveChannelRequest(entity))
        return {"ok": True, "left": True, "via": "LeaveChannel", "id": getattr(entity, "id", None)}
    except Exception:
        # 普通群：删对话
        await client.delete_dialog(entity)
        return {"ok": True, "left": True, "via": "delete_dialog", "id": getattr(entity, "id", None)}



async def op_list_contacts(client: Any, p: dict) -> dict:
    """列出通讯录联系人。"""
    from telethon.tl.functions.contacts import GetContactsRequest

    limit = int(p.get("limit") or 200)
    limit = max(1, min(limit, 1000))
    res = await client(GetContactsRequest(hash=0))
    users = list(getattr(res, "users", []) or [])
    items = []
    for u in users[:limit]:
        items.append({
            "user_id": getattr(u, "id", None),
            "username": getattr(u, "username", None),
            "first_name": getattr(u, "first_name", None) or "",
            "last_name": getattr(u, "last_name", None) or "",
            "phone": getattr(u, "phone", None) or "",
            "bot": bool(getattr(u, "bot", False)),
            "mutual": bool(getattr(u, "mutual_contact", False)),
        })
    return {"total": len(users), "returned": len(items), "items": items}


async def op_add_contact(client: Any, p: dict) -> dict:
    """添加联系人（@username / 用户ID / 手机号）。"""
    from telethon.tl.functions.contacts import AddContactRequest
    from telethon.tl.types import InputPhoneContact
    from telethon.tl.functions.contacts import ImportContactsRequest

    target = str(p.get("target") or "").strip()
    if not target:
        raise ToolboxError("需要 target（@username / 用户ID / 手机号）")
    first = (p.get("first_name") or "").strip() or "Contact"
    last = (p.get("last_name") or "").strip()
    phone = (p.get("phone") or "").strip()

    # 纯手机号导入
    if target.replace("+", "").isdigit() or (target.startswith("+") and target[1:].isdigit()):
        phone = target if target.startswith("+") else f"+{target}"
        res = await client(ImportContactsRequest(contacts=[
            InputPhoneContact(client_id=0, phone=phone, first_name=first, last_name=last)
        ]))
        users = list(getattr(res, "users", []) or [])
        u = users[0] if users else None
        return {
            "ok": bool(u),
            "imported": getattr(res, "imported", None) is not None,
            "user_id": getattr(u, "id", None) if u else None,
            "username": getattr(u, "username", None) if u else None,
            "phone": phone,
        }

    entity = await client.get_entity(_resolve_entity(client, target))
    await client(AddContactRequest(
        id=entity,
        first_name=first,
        last_name=last,
        phone=phone or getattr(entity, "phone", None) or "",
        add_phone_privacy_exception=False,
    ))
    return {
        "ok": True,
        "user_id": getattr(entity, "id", None),
        "username": getattr(entity, "username", None),
        "first_name": first,
        "last_name": last,
    }


async def op_delete_contact(client: Any, p: dict) -> dict:
    """从通讯录删除联系人。"""
    from telethon.tl.functions.contacts import DeleteContactsRequest

    target = str(p.get("target") or "").strip()
    if not target:
        raise ToolboxError("需要 target")
    entity = await client.get_entity(_resolve_entity(client, target))
    await client(DeleteContactsRequest(id=[entity]))
    return {
        "ok": True,
        "user_id": getattr(entity, "id", None),
        "username": getattr(entity, "username", None),
    }


async def op_send_media(client: Any, p: dict) -> dict:
    """发送本地文件/图片/视频（路径须在服务端可访问）。"""
    from pathlib import Path as _P

    target = str(p.get("target") or "").strip()
    path = str(p.get("path") or "").strip()
    caption = str(p.get("caption") or "")
    if not target:
        raise ToolboxError("需要 target")
    if not path:
        raise ToolboxError("需要 path（服务端本地文件路径）")
    fp = _P(path).expanduser()
    if not fp.is_file():
        raise ToolboxError(f"文件不存在：{fp}")
    # 简单大小保护 50MB
    size = fp.stat().st_size
    if size > 50 * 1024 * 1024:
        raise ToolboxError(f"文件过大（{size} bytes），上限 50MB")
    entity = await client.get_entity(_resolve_entity(client, target))
    msg = await client.send_file(entity, str(fp), caption=caption or None)
    if isinstance(msg, list):
        msg = msg[-1]
    return {
        "ok": True,
        "message_id": getattr(msg, "id", None),
        "path": str(fp),
        "size": size,
        "caption": caption,
        "peer_id": getattr(entity, "id", None),
    }


async def op_download_media(client: Any, p: dict) -> dict:
    """下载某条消息中的媒体到 data 目录。"""
    from pathlib import Path as _P
    import os

    target = str(p.get("target") or "").strip()
    mid = p.get("message_id")
    if not target or mid is None:
        raise ToolboxError("需要 target 与 message_id")
    mid = int(mid)
    out_dir = str(p.get("out_dir") or "").strip()
    data_dir = os.environ.get("TAM_DATA_DIR") or "./data"
    root = _P(out_dir).expanduser() if out_dir else (_P(data_dir) / "downloads")
    root.mkdir(parents=True, exist_ok=True)
    entity = await client.get_entity(_resolve_entity(client, target))
    msg = await client.get_messages(entity, ids=mid)
    if not msg:
        raise ToolboxError(f"消息不存在：{mid}")
    if not getattr(msg, "media", None):
        raise ToolboxError("该消息没有媒体")
    path = await client.download_media(msg, file=str(root / f"{mid}"))
    return {
        "ok": True,
        "message_id": mid,
        "path": path,
        "media_type": type(msg.media).__name__,
    }


async def op_create_channel(client: Any, p: dict) -> dict:
    """创建公开/私有频道。"""
    from telethon.tl.functions.channels import CreateChannelRequest

    title = str(p.get("title") or "").strip()
    if not title:
        raise ToolboxError("需要 title")
    about = str(p.get("about") or "")
    megagroup = False  # 频道
    result = await client(CreateChannelRequest(
        title=title, about=about, megagroup=False, broadcast=True,
    ))
    chats = list(getattr(result, "chats", []) or [])
    ch = chats[0] if chats else None
    username = str(p.get("username") or "").strip().lstrip("@")
    out = {
        "ok": True,
        "id": getattr(ch, "id", None),
        "title": getattr(ch, "title", title),
        "username": getattr(ch, "username", None),
    }
    if username and ch is not None:
        from telethon.tl.functions.channels import UpdateUsernameRequest
        try:
            await client(UpdateUsernameRequest(channel=ch, username=username))
            out["username"] = username
        except Exception as exc:  # noqa: BLE001
            out["username_error"] = f"{type(exc).__name__}: {exc}"
    return out


async def op_set_username(client: Any, p: dict) -> dict:
    """设置本账号 @用户名（空字符串=清除）。"""
    from telethon.tl.functions.account import UpdateUsernameRequest

    username = str(p.get("username") or "").strip().lstrip("@")
    ok = await client(UpdateUsernameRequest(username=username))
    me = await client.get_me()
    return {
        "ok": bool(ok),
        "username": getattr(me, "username", None),
        "user_id": getattr(me, "id", None),
    }


async def op_interact_bot(client: Any, p: dict) -> dict:
    """与机器人交互：发送文本并等待若干条回复。"""
    import asyncio as _aio
    from telethon import events

    bot = str(p.get("bot") or p.get("target") or "").strip()
    text = str(p.get("text") or "/start").strip()
    wait = float(p.get("wait") or 5)
    limit = int(p.get("limit") or 3)
    if not bot:
        raise ToolboxError("需要 bot（@Bot 用户名）")
    entity = await client.get_entity(_resolve_entity(client, bot))
    replies: list[dict] = []
    done = _aio.Event()

    async def handler(event):
        if event.sender_id != getattr(entity, "id", None):
            return
        replies.append({
            "message_id": event.id,
            "text": (event.raw_text or "")[:2000],
            "date": event.date.isoformat() if event.date else None,
        })
        if len(replies) >= limit:
            done.set()

    client.add_event_handler(handler, events.NewMessage(from_users=entity))
    try:
        sent = await client.send_message(entity, text)
        try:
            await _aio.wait_for(done.wait(), timeout=max(1.0, wait))
        except _aio.TimeoutError:
            pass
        return {
            "ok": True,
            "sent_id": getattr(sent, "id", None),
            "bot_id": getattr(entity, "id", None),
            "replies": replies,
            "reply_count": len(replies),
        }
    finally:
        client.remove_event_handler(handler)



OP_SPECS: list[dict[str, Any]] = [
    {"op": "alive", "label": "筛活", "danger": False,
     "desc": "探测号是否还能用，不产生任何可见行为",
     "params": [{"name": "timeout", "type": "float", "label": "超时（秒）",
                 "default": 10}]},
    {"op": "spam_check", "label": "双向/限制检测", "danger": False,
     "desc": "问 @SpamBot 当前有没有被限制",
     "params": [{"name": "wait", "type": "float", "label": "等回复（秒）",
                 "default": 3}]},
    {"op": "okpay_balance", "label": "OKPay 余额", "danger": False,
     "desc": "私聊 @Okpay 查询钱包余额（自动解析 USDT/TRX 等）",
     "params": [{"name": "bot", "type": "str", "label": "机器人用户名",
                 "default": "Okpay"},
                {"name": "wait", "type": "float", "label": "等待秒数",
                 "default": 4}]},

    {"op": "privacy", "label": "隐私设置", "danger": False,
     "desc": "批量设手机号/最后上线/入群/头像可见性，留空用保守默认",
     "params": [{"name": "items", "type": "privacy", "label": "隐私项"}]},
    {"op": "check_phones", "label": "筛料", "danger": False,
     "desc": "查一批手机号哪些注册过 Telegram，查完自动清理通讯录",
     "params": [{"name": "phones", "type": "textarea",
                 "label": "手机号（每行一个，最多 500）", "required": True}]},
    {"op": "twofa", "label": "改二步验证", "danger": True,
     "desc": "设置或移除二验密码。新密码留空 = 移除",
     "params": [{"name": "old", "type": "password",
                 "label": "当前密码（没设过就留空）"},
                {"name": "new", "type": "password",
                 "label": "新密码（留空 = 移除二验）"},
                {"name": "hint", "type": "str", "label": "提示语"}]},
    {"op": "twofa_status", "label": "二验状态（含辅助邮箱）", "danger": False,
     "desc": "只读：有无云密码、是否绑定辅助邮箱、是否已在重置等待期",
     "params": []},
    {"op": "twofa_reset", "label": "发起2FA重置", "danger": True,
     "desc": "申请官方重置等待期（通常约7天）。有辅助邮箱也会照常申请，但原持有人仍可能邮箱恢复",
     "params": [{"name": "confirm", "type": "bool",
                 "label": "我确认发起二验重置（进入等待期）", "required": True}]},
    {"op": "twofa_reset_cancel", "label": "取消2FA重置", "danger": True,
     "desc": "取消进行中的二验重置等待（declinePasswordReset）",
     "params": [{"name": "confirm", "type": "bool",
                 "label": "我确认取消重置等待", "required": True}]},
    {"op": "delete_messages", "label": "删除/撤回消息", "danger": True,
     "desc": "删除消息；revoke 默认开启（尽量双向撤回）",
     "params": [{"name": "target", "type": "str", "label": "会话 @/ID", "required": True},
                {"name": "message_ids", "type": "str", "label": "消息ID（空格/逗号分隔）", "required": True},
                {"name": "revoke", "type": "bool", "label": "双向撤回", "default": True}]},
    {"op": "edit_message", "label": "编辑消息", "danger": True,
     "desc": "编辑本账号发送的消息",
     "params": [{"name": "target", "type": "str", "label": "会话", "required": True},
                {"name": "message_id", "type": "int", "label": "消息ID", "required": True},
                {"name": "text", "type": "textarea", "label": "新文本", "required": True}]},
    {"op": "forward_messages", "label": "转发消息", "danger": True,
     "desc": "从源会话转发到目标会话",
     "params": [{"name": "from_peer", "type": "str", "label": "来源会话", "required": True},
                {"name": "to_peer", "type": "str", "label": "目标会话", "required": True},
                {"name": "message_ids", "type": "str", "label": "消息ID", "required": True}]},
    {"op": "reply_message", "label": "回复/引用消息", "danger": True,
     "desc": "回复指定 message_id",
     "params": [{"name": "target", "type": "str", "label": "会话", "required": True},
                {"name": "message_id", "type": "int", "label": "被回复消息ID", "required": True},
                {"name": "text", "type": "textarea", "label": "回复内容", "required": True}]},
    {"op": "read_message", "label": "标记已读", "danger": False,
     "desc": "将对话标记已读到指定消息ID",
     "params": [{"name": "target", "type": "str", "label": "会话", "required": True},
                {"name": "message_id", "type": "int", "label": "消息ID(max_id)", "required": True}]},
    {"op": "get_message", "label": "读取单条消息", "danger": False,
     "desc": "按 ID 读取一条消息完整文本",
     "params": [{"name": "target", "type": "str", "label": "会话", "required": True},
                {"name": "message_id", "type": "int", "label": "消息ID", "required": True}]},
    {"op": "create_group", "label": "创建群聊", "danger": True,
     "desc": "创建超级群（默认）或基础群",
     "params": [{"name": "title", "type": "str", "label": "群标题", "required": True},
                {"name": "about", "type": "str", "label": "简介"},
                {"name": "megagroup", "type": "bool", "label": "超级群", "default": True},
                {"name": "users", "type": "textarea", "label": "初始成员@（可选）"}]},
    {"op": "leave_chat", "label": "离开群聊", "danger": True,
     "desc": "退出群/频道",
     "params": [{"name": "target", "type": "str", "label": "群 @/ID", "required": True}]},
    {"op": "search_public", "label": "搜索公开群/用户", "danger": False,
     "desc": "用当前账号搜索公开用户名/群/频道",
     "params": [{"name": "query", "type": "str", "label": "关键词", "required": True},
                {"name": "limit", "type": "int", "label": "最多条数", "default": 20}]},
    {"op": "join_chat", "label": "加入群/频道", "danger": True,
     "desc": "加入公开 @群 或 t.me 邀请链接",
     "params": [{"name": "target", "type": "str", "label": "@用户名或邀请链接", "required": True}]},
    {"op": "list_members", "label": "群成员列表", "danger": False,
     "desc": "浏览群/频道成员（受账号权限与群设置限制）",
     "params": [{"name": "target", "type": "str", "label": "群 @用户名或ID", "required": True},
                {"name": "limit", "type": "int", "label": "最多人数", "default": 100}]},
    {"op": "read_messages", "label": "读取群消息", "danger": False,
     "desc": "读取对话最近消息文本（可关键词搜索）",
     "params": [{"name": "target", "type": "str", "label": "群/频道/用户", "required": True},
                {"name": "limit", "type": "int", "label": "条数", "default": 30},
                {"name": "search", "type": "str", "label": "关键词（可选）"}]},
    {"op": "block_users", "label": "屏蔽用户", "danger": True,
     "desc": "用当前账号屏蔽指定用户（@用户名 / 用户ID / 手机号，每行一个）",
     "params": [{"name": "targets", "type": "textarea", "label": "目标用户（每行一个）",
                 "required": True}]},
    {"op": "unblock_users", "label": "取消屏蔽", "danger": False,
     "desc": "取消屏蔽指定用户",
     "params": [{"name": "targets", "type": "textarea", "label": "目标用户（每行一个）",
                 "required": True}]},
    {"op": "contacts_clear", "label": "清空通讯录", "danger": True,
     "desc": "删掉号上全部联系人，分批执行",
     "params": [{"name": "dry_run", "type": "bool",
                 "label": "只统计不删除"}]},
    {"op": "dialogs_clear", "label": "清理会话", "danger": True,
     "desc": "退群退频道。默认不动私聊（里面可能有验证码）",
     "params": [{"name": "include_private", "type": "bool",
                 "label": "连私聊一起删（危险）"},
                {"name": "limit", "type": "int",
                 "label": "最多处理几个（0=不限）", "default": 0}]},
    {"op": "profile_clear", "label": "防找回清理", "danger": True,
     "desc": "清掉头像、简介、用户名等可被前手认出的痕迹",
     "params": [{"name": "photos", "type": "bool", "label": "删头像",
                 "default": True},
                {"name": "bio", "type": "bool", "label": "清简介",
                 "default": True},
                {"name": "username", "type": "bool",
                 "label": "清用户名"}]},
    {"op": "terminate_others", "label": "踢掉其它设备", "danger": True,
     "desc": "只留本机登录，踢完会回查确认真的踢干净了",
     "params": []},
    {"op": "logout", "label": "退出登录（作废 session）", "danger": True,
     "desc": "仅作废当前登录，不解绑手机、不删除 Telegram 账号",
     "params": [{"name": "confirm", "type": "bool",
                 "label": "我确认要退出登录", "required": True}]},
    {"op": "delete_tg_account", "label": "注销 Telegram 账号", "danger": True,
     "desc": "向官方申请删除该号（不可逆）。成功后本地会话也会作废",
     "params": [{"name": "confirm", "type": "bool",
                 "label": "我确认永久注销此 Telegram 账号", "required": True},
                {"name": "reason", "type": "str",
                 "label": "注销原因（可选）", "default": "User requested deletion"}]},
    {"op": "list_contacts", "label": "通讯录列表", "danger": False,
     "desc": "列出本号通讯录联系人",
     "params": [{"name": "limit", "type": "int", "label": "最多返回", "default": 200}]},
    {"op": "add_contact", "label": "添加联系人", "danger": True,
     "desc": "添加 @用户名 / 用户ID / 手机号到通讯录",
     "params": [{"name": "target", "type": "str", "label": "目标", "required": True},
                {"name": "first_name", "type": "str", "label": "名", "default": "Contact"},
                {"name": "last_name", "type": "str", "label": "姓"},
                {"name": "phone", "type": "str", "label": "手机号（可选）"}]},
    {"op": "delete_contact", "label": "删除联系人", "danger": True,
     "desc": "从通讯录删除指定联系人",
     "params": [{"name": "target", "type": "str", "label": "目标", "required": True}]},
    {"op": "send_media", "label": "发送文件/图片/视频", "danger": True,
     "desc": "向会话发送服务端本地文件（≤50MB）",
     "params": [{"name": "target", "type": "str", "label": "目标", "required": True},
                {"name": "path", "type": "str", "label": "文件路径", "required": True},
                {"name": "caption", "type": "str", "label": "说明文字"}]},
    {"op": "download_media", "label": "下载消息媒体", "danger": False,
     "desc": "下载指定消息中的图片/文件到 data/downloads",
     "params": [{"name": "target", "type": "str", "label": "会话", "required": True},
                {"name": "message_id", "type": "int", "label": "消息 ID", "required": True},
                {"name": "out_dir", "type": "str", "label": "输出目录（可选）"}]},
    {"op": "create_channel", "label": "创建频道", "danger": True,
     "desc": "创建广播频道，可选设置公开用户名",
     "params": [{"name": "title", "type": "str", "label": "标题", "required": True},
                {"name": "about", "type": "str", "label": "简介"},
                {"name": "username", "type": "str", "label": "公开用户名（可选）"}]},
    {"op": "set_username", "label": "设置用户名", "danger": True,
     "desc": "修改本账号 @username，空=清除",
     "params": [{"name": "username", "type": "str", "label": "用户名"}]},
    {"op": "interact_bot", "label": "与机器人交互", "danger": True,
     "desc": "向机器人发消息并等待回复（通用，不限 SpamBot）",
     "params": [{"name": "bot", "type": "str", "label": "机器人 @用户名", "required": True},
                {"name": "text", "type": "str", "label": "发送内容", "default": "/start"},
                {"name": "wait", "type": "float", "label": "等待秒数", "default": 5},
                {"name": "limit", "type": "int", "label": "最多收几条回复", "default": 3}]},
]

OPS: dict[str, Callable[[Any, dict], Awaitable[dict]]] = {
    "alive": op_alive,
    "spam_check": op_spam_check,
    "okpay_balance": op_okpay_balance,
    "privacy": op_privacy,
    "check_phones": op_check_phones,
    "twofa": op_twofa,
    "twofa_status": op_twofa_status,
    "twofa_reset": op_twofa_reset,
    "twofa_reset_cancel": op_twofa_reset_cancel,
    "delete_messages": op_delete_messages,
    "edit_message": op_edit_message,
    "forward_messages": op_forward_messages,
    "reply_message": op_reply_message,
    "read_message": op_read_message,
    "get_message": op_get_message,
    "create_group": op_create_group,
    "leave_chat": op_leave_chat,
    "search_public": op_search_public,
    "join_chat": op_join_chat,
    "list_members": op_list_members,
    "read_messages": op_read_messages,
    "block_users": op_block_users,
    "unblock_users": op_unblock_users,
    "contacts_clear": op_contacts_clear,
    "dialogs_clear": op_dialogs_clear,
    "profile_clear": op_profile_clear,
    "terminate_others": op_terminate_others,
    "logout": op_logout,
    "delete_tg_account": op_delete_tg_account,
    "list_contacts": op_list_contacts,
    "add_contact": op_add_contact,
    "delete_contact": op_delete_contact,
    "send_media": op_send_media,
    "download_media": op_download_media,
    "create_channel": op_create_channel,
    "set_username": op_set_username,
    "interact_bot": op_interact_bot,
}

_SPEC_BY_OP = {s["op"]: s for s in OP_SPECS}

# 注册表和实现必须一一对应，写漏一个就在导入时炸，别等到用户点了才发现
assert set(_SPEC_BY_OP) == set(OPS), (
    f"OP_SPECS 与 OPS 对不上：{set(_SPEC_BY_OP) ^ set(OPS)}")


def get_spec(op: str) -> dict[str, Any]:
    spec = _SPEC_BY_OP.get(op)
    if spec is None:
        raise ToolboxError(f"不认识的操作：{op}")
    return spec


def validate_params(op: str, params: dict | None) -> dict:
    """按 spec 校验并归一入参。未知键直接拒掉，不让网页乱塞东西。"""
    spec = get_spec(op)
    params = dict(params or {})
    known = {p["name"]: p for p in spec["params"]}
    for k in params:
        if k not in known:
            raise ToolboxError(f"{op} 不接受参数 {k}")
    out: dict[str, Any] = {}
    for name, meta in known.items():
        if name not in params or params[name] is None or params[name] == "":
            if meta.get("required"):
                raise ToolboxError(f"{meta.get('label') or name} 必填")
            if "default" in meta:
                out[name] = meta["default"]
            continue
        v = params[name]
        kind = meta["type"]
        try:
            if kind == "int":
                out[name] = int(v)
            elif kind == "float":
                out[name] = float(v)
            elif kind == "bool":
                out[name] = str(v).strip().lower() in ("1", "true", "yes",
                                                       "on") if not isinstance(v, bool) else v
            else:
                out[name] = v
        except (TypeError, ValueError):
            raise ToolboxError(
                f"{meta.get('label') or name} 填的不是{kind}：{v!r}") from None
    # required 的 bool（比如销号确认）没勾也要拦下来
    for name, meta in known.items():
        if meta.get("required") and not out.get(name):
            raise ToolboxError(f"{meta.get('label') or name} 必填")
    return out


async def run_op(manager: Any, account_id: int, op: str,
                 params: dict | None = None) -> dict[str, Any]:
    """对单个号执行一项操作。连接/加锁/限速/回写全交给 manager.session。"""
    clean = validate_params(op, params)
    fn = OPS[op]
    async with manager.session(account_id) as client:
        if not await client.is_user_authorized():
            raise ToolboxError("这个号没有登录态（session 已失效）")
        result = await fn(client, clean)
    # 退出登录 / 注销账号后清掉本地 session，避免列表仍显示「已授权」
    if op in ("logout", "delete_tg_account"):
        try:
            status = "deleted" if op == "delete_tg_account" else "unauthorized"
            note = (
                "telegram account deletion requested"
                if op == "delete_tg_account"
                else "logged out"
            )
            manager.db.update(
                account_id,
                session_enc=None,
                status=status,
                status_note=note,
                login_at=None,
                adopted_at=None,
                last_kick_at=None,
                kick_retry_at=None,
            )
            manager.db.log(
                account_id, op, True,
                (result or {}).get("note") if isinstance(result, dict) else "",
            )
        except Exception:
            pass
    # 二验状态/重置：把 has_2fa 写回列表，便于筛选
    if op in ("twofa_status", "twofa_reset", "twofa_reset_cancel") and isinstance(result, dict):
        try:
            hp = result.get("has_password")
            if hp is None and isinstance(result.get("after"), dict):
                hp = result["after"].get("has_password")
            if hp is not None:
                updates = {"has_2fa": int(bool(hp))}
                # 立即清除成功：本地存的密码也作废
                if op == "twofa_reset" and result.get("result") == "ok":
                    updates["twofa_enc"] = None
                manager.db.update(account_id, **updates)
        except Exception:
            pass
    # 改二验成功后回写有无二验；新密码加密保存供列表显示与导出 2fa.txt
    if op == "twofa" and isinstance(result, dict) and "has_password" in result:
        try:
            updates = {"has_2fa": int(bool(result["has_password"]))}
            new_pwd = (clean.get("new") or "").strip()
            if result.get("has_password") and new_pwd:
                from .crypto import encrypt
                updates["twofa_enc"] = encrypt(manager.s.master_key, new_pwd)
            elif not result.get("has_password"):
                updates["twofa_enc"] = None
            manager.db.update(account_id, **updates)
        except Exception:
            try:
                manager.db.update(account_id,
                                  has_2fa=int(bool(result["has_password"])))
            except Exception:
                pass
    return result


async def run_op_batch(manager: Any, account_ids: list[int], op: str,
                       params: dict | None = None,
                       concurrency: int | None = None) -> dict[str, Any]:
    """批量执行。并发度不传就走 TAM_BATCH_CONCURRENCY（面板里可调）。

    先校验一次参数，别等跑到第 50 个号才发现参数是错的。
    """
    clean = validate_params(op, params)
    if not account_ids:
        raise ToolboxError("没有选中任何账号")

    async def task(aid: int) -> dict:
        return await run_op(manager, aid, op, clean)

    results = await manager.run_batch(account_ids, task,
                                      concurrency=concurrency)
    ok = sum(1 for r in results if r.get("ok"))
    return {"op": op, "total": len(results), "ok": ok,
            "failed": len(results) - ok, "results": results}
