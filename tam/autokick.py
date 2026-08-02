"""登录后定时自动踢出其它设备。

为什么需要：发卡平台/发销商卖出的号常常自己也留着一份登录，而且 Telegram 对
"刚登录就踢掉其它设备"有 24 小时保护期（新会话在前 24 小时内不能注销其它会话）。
所以正确做法是：登录满 24 小时后再自动执行一次 ResetAuthorizations，之后每隔一个
周期复查一次，把新冒出来的外部登录持续清掉。

周期由 TAM_AUTO_KICK_HOURS 控制（0 = 关闭），单个账号可用 auto_kick 字段单独关闭。

注意：到期时间只有 plan() 一个真源。以前前端自己用 base + hours*3600 复算一遍，
规则和这里不一致（比如不认识 SKIP_STATUS），于是被跳过的号会一直显示一个越拖越远
的过去时间（"32.8 天前"）。现在后端把状态和到期时间一起下发，前端只负责显示。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

# 后台循环的复查间隔（秒）。踢设备本身很轻，10 分钟扫一次就够。
SCAN_INTERVAL = 600.0

# 可重试的失败（服务端 24h 保护期未过、校验没通过、连不上）隔多久重试。
# 默认 1 小时，可用 TAM_KICK_RETRY 自定义：45s / 10m / 2h / 1h30m 都行。
RETRY_AFTER = 3600.0
RETRY_MIN = 10.0


RETRY_KEY = "kick_retry"      # settings 表里的键名


def retry_source(manager: Any) -> tuple[float, str]:
    """当前生效的重试间隔（秒）及其来源。

    优先级：Web 上改的（settings 表）> .env 的 TAM_KICK_RETRY > 默认 1 小时。
    """
    from .config import parse_duration

    cfg = parse_duration(
        getattr(getattr(manager, "s", None), "kick_retry_s", RETRY_AFTER), RETRY_AFTER)
    raw = None
    db = getattr(manager, "db", None)
    if db is not None and hasattr(db, "get_setting"):
        try:
            raw = db.get_setting(RETRY_KEY)
        except Exception:
            raw = None
    if raw:
        val = parse_duration(raw, cfg)
        return max(RETRY_MIN, val), "web"
    return max(RETRY_MIN, cfg), "env"


def retry_after(manager: Any, override: float | str | None = None) -> float:
    """本次重试间隔（秒）：调用方临时传的优先，否则用已保存的设置。"""
    from .config import parse_duration

    base, _ = retry_source(manager)
    if override is not None:
        base = parse_duration(override, base)
    return max(RETRY_MIN, base)


def set_retry(manager: Any, value: float | str | None) -> dict[str, Any]:
    """保存重试间隔（支持 45s / 10m / 2h 写法）。传 None 或空字符串 = 恢复默认。"""
    from .config import parse_duration

    if value is None or (isinstance(value, str) and not value.strip()):
        manager.db.set_setting(RETRY_KEY, None)
        manager.db.log(None, "kick_retry", True, "重试间隔恢复为配置/默认值")
    else:
        secs = parse_duration(value, 0.0)
        if secs <= 0:
            raise ValueError("时长格式看不懂，请写成 45s / 10m / 2h 这样")
        if secs < RETRY_MIN:
            raise ValueError("重试间隔不能小于 %g 秒" % RETRY_MIN)
        manager.db.set_setting(RETRY_KEY, str(value).strip())
        manager.db.log(None, "kick_retry", True, "重试间隔改为 %s" % human_gap(secs))
    secs, src = retry_source(manager)
    return {"ok": True, "retry_after_s": secs, "retry_after_text": human_gap(secs),
            "retry_source": src}


def human_gap(sec: float) -> str:
    """把秒数说成人话，写日志和 UI 用。"""
    if sec < 60:
        return "%g 秒" % round(sec, 1)
    if sec < 3600:
        return "%g 分钟" % round(sec / 60, 1)
    return "%g 小时" % round(sec / 3600, 2)

# 这些状态的号不值得再去碰，碰了只会制造无效请求
SKIP_STATUS = {"banned", "unauthorized", "frozen", "spam_block_perm"}

# 状态机：
#   disabled   全局关闭
#   off        该账号单独关闭
#   no_session 没有会话（未授权）
#   skipped    状态在 SKIP_STATUS 里，永不参与
#   no_base    连 created_at 都没有，理论上不该出现
#   waiting    未到期，due_at 是未来时间
#   due        已到期，等下一轮扫描执行（due_at 是过去时间，属于正常现象）
STATES_WITH_DUE = {"waiting", "due"}


def hours_for(acc: Any, global_hours: float) -> float:
    """该号实际使用的清设备周期（小时）。账号自定义优先，否则用全局。"""
    try:
        h = getattr(acc, "auto_kick_hours", None)
        if h is not None and float(h) > 0:
            return float(h)
    except (TypeError, ValueError):
        pass
    return float(global_hours or 0)


def plan(acc: Any, hours: float, now: float | None = None) -> dict[str, Any]:
    """算出某个账号的自动清设备计划：状态 + 到期时间 + 原因。

    前端展示、后台调度、状态面板都走这一个函数，避免两边规则漂移。
    hours：全局周期；若账号设置了 auto_kick_hours 则覆盖。
    """
    now = time.time() if now is None else now
    hrs = hours_for(acc, hours)
    loop = bool(getattr(acc, "auto_kick_loop", 1))
    if hours <= 0 and getattr(acc, "auto_kick_hours", None) in (None, 0, 0.0):
        return {"state": "disabled", "due_at": None, "hours": hrs, "loop": loop,
                "reason": "全局未开启自动清设备"}
    if hrs <= 0:
        return {"state": "disabled", "due_at": None, "hours": hrs, "loop": loop,
                "reason": "清设备周期无效"}
    if not acc.session_enc:
        return {"state": "no_session", "due_at": None, "hours": hrs, "loop": loop,
                "reason": "未授权，暂不参与"}
    if not getattr(acc, "auto_kick", 1):
        return {"state": "off", "due_at": None, "hours": hrs, "loop": loop,
                "reason": "该账号已单独关闭"}
    if acc.status in SKIP_STATUS:
        return {"state": "skipped", "due_at": None, "hours": hrs, "loop": loop,
                "reason": f"状态 {acc.status}，不再尝试"}

    # 单次模式：已成功踢过一次且无重试排队 → 结束
    if (not loop) and acc.last_kick_at and not acc.kick_retry_at:
        return {"state": "done", "due_at": None, "hours": hrs, "loop": False,
                "base_from": "last_kick_at", "retrying": False,
                "reason": "单次清设备已完成（未开启循环）"}

    # 上一次没踢成功时留下的重试时间优先，避免直接往后推一整个周期
    if acc.kick_retry_at:
        due, base_from, retrying = float(acc.kick_retry_at), "kick_retry_at", True
    else:
        retrying = False
        if acc.last_kick_at:
            base, base_from = float(acc.last_kick_at), "last_kick_at"
        elif acc.adopted_at:
            base, base_from = float(acc.adopted_at), "adopted_at"
        elif acc.login_at:
            base, base_from = float(acc.login_at), "login_at"
        elif acc.created_at:
            base, base_from = float(acc.created_at), "created_at"
        else:
            return {"state": "no_base", "due_at": None, "hours": hrs, "loop": loop,
                    "reason": "尚无接管时间"}
        due = base + hrs * 3600

    common = {"due_at": due, "base_from": base_from, "retrying": retrying,
              "hours": hrs, "loop": loop}
    if due <= now:
        return {"state": "due", "overdue_s": now - due,
                "reason": ("上次未踢成功，等待重试" if retrying
                           else "已到期，等待本轮扫描执行"),
                **common}
    return {"state": "waiting", "overdue_s": 0.0,
            "reason": ("上次未踢成功，等待重试" if retrying
                       else ("距下次循环还剩" if (loop and acc.last_kick_at)
                             else "接管未满") + " %g 小时" % hrs),
            **common}


def next_due_at(acc: Any, hours: float, now: float | None = None) -> float | None:
    """返回该账号下一次应该自动清设备的时间戳；None = 不参与。"""
    p = plan(acc, hours, now)
    return p["due_at"] if p["state"] in STATES_WITH_DUE else None


def due_ids(accounts: list[Any], hours: float, now: float | None = None) -> list[int]:
    """筛出已经到期的账号 ID。"""
    now = time.time() if now is None else now
    return [a.id for a in accounts
            if a.id and plan(a, hours, now)["state"] == "due"]


async def run_once(manager: Any, hours: float | None = None,
                   concurrency: int = 2,
                   retry_gap: float | str | None = None) -> dict[str, Any]:
    """扫一轮：对到期账号执行一次清设备，并记录 last_kick_at。

    retry_gap：本轮失败后隔多久重试，可传 "45s" / "10m" / "2h" 或秒数；
    不传则用 TAM_KICK_RETRY 配置值。
    """
    hrs = manager.s.auto_kick_hours if hours is None else hours
    gap = retry_after(manager, retry_gap)
    ids = due_ids(manager.db.list(), hrs)
    if not ids:
        return {"enabled": hrs > 0, "hours": hrs, "due": 0,
                "retry_after_s": gap, "results": []}

    async def task(aid: int) -> Any:
        # 顺手校一下服务端的真实会话创建时间（只当参考，计时起点是 adopted_at）
        try:
            await manager.sync_login_at(aid)
        except Exception:
            pass
        acc = manager.db.get(aid)
        if acc is not None:
            p = plan(acc, hrs)
            if p["state"] != "due":
                manager.db.log(aid, "auto_kick", True, "本轮跳过：%s" % p["reason"])
                return {"skipped": True, "reason": p["state"]}

        r = await manager.terminate_other_sessions(aid)   # 内部已回拉会话列表校验
        now = time.time()
        if r.get("ok"):
            # 只有服务端重查确认“只剩本机”才算成功，才能重新起算一个周期
            manager.db.update(aid, last_kick_at=now, kick_retry_at=None)
            acc2 = manager.db.get(aid)
            use_h = hours_for(acc2, hrs) if acc2 else hrs
            manager.db.log(aid, "auto_kick", True,
                           "满 %g 小时自动清设备，已校验：踢掉 %d 个，现只剩本机"
                           % (use_h, len(r.get("removed") or [])))
        else:
            manager.db.update(aid, kick_retry_at=now + gap)
            manager.db.log(aid, "auto_kick", False,
                           "清设备未成功（%s），%s后重试"
                           % (r.get("error") or "校验未通过，还剩 %d 个外部会话"
                              % r.get("after_others", 0), human_gap(gap)))
        return r

    results = await manager.run_batch(ids, task, concurrency=concurrency, stagger=False)
    verified = 0
    for r in results:
        if not r.get("ok"):
            # 抛异常的（连不上、代理挂了）也走重试，不能当成功把周期重置
            manager.db.update(r["account_id"], kick_retry_at=time.time() + gap)
            manager.db.log(r["account_id"], "auto_kick", False, str(r.get("error"))[:500])
        elif isinstance(r.get("result"), dict) and r["result"].get("verified"):
            verified += 1
    return {"enabled": hrs > 0, "hours": hrs, "due": len(ids),
            "verified": verified, "retry_after_s": gap, "results": results}


def status(manager: Any) -> dict[str, Any]:
    """给 UI 看的概览：开关、周期、待处理数量、最近一个到期时间。

    server_now 用来让前端校正客户端时钟偏差——机器时间不准时，同一个时间戳在前端
    会被渲染成一个莫名其妙的相对时间。
    """
    hrs = manager.s.auto_kick_hours
    now = time.time()
    plans = [plan(a, hrs, now) for a in manager.db.list()]
    watched = [p for p in plans if p["state"] in STATES_WITH_DUE]
    overdue = [p for p in watched if p["state"] == "due"]
    retrying = [p for p in watched if p.get("retrying")]
    retry_gap_now, retry_src = retry_source(manager)
    return {
        "enabled": hrs > 0,
        "hours": hrs,
        "scan_interval_s": SCAN_INTERVAL,
        "server_now": now,
        "watched": len(watched),
        "due_now": len(overdue),
        "retrying": len(retrying),
        "retry_after_s": retry_gap_now,
        "retry_after_text": human_gap(retry_gap_now),
        "retry_source": retry_src,
        "retry_min_s": RETRY_MIN,
        "max_overdue_s": max((p["overdue_s"] for p in overdue), default=0.0),
        "next_at": min((p["due_at"] for p in watched if p["state"] == "waiting"),
                       default=None),
    }


async def loop(manager: Any, interval: float = SCAN_INTERVAL) -> None:
    """后台常驻循环。任何异常都不能把循环带死。"""
    while True:
        try:
            if manager.s.auto_kick_hours > 0:
                await run_once(manager)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 单轮失败不影响下一轮
            try:
                manager.db.log(None, "auto_kick", False, repr(exc)[:500])
            except Exception:
                pass
        await asyncio.sleep(interval)