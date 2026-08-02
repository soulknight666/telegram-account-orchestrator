"""养号（warm-up）：让新导入的账号看起来像真人在用。

参考业内养号工具的标准动作（保持在线、随机已读、账号之间互相加联系人并聊天），
所有动作都走 manager.session()，因此自动继承限速、单账号串行锁与代理设置。

注意：养号只降低风控概率，不能消除风险。第三方 API 自动化本身就可能被封号。
"""
from __future__ import annotations

import random
import time
from typing import Any

from .ratelimit import human_delay

# 互聊用的中性短语，避免营销词、链接、emoji 轰炸
PHRASES: tuple[str, ...] = (
    "在吗", "忙啥呢", "刚看到消息", "晚点聊", "好的", "收到", "哈哈",
    "今天天气不错", "吃了吗", "最近怎么样", "我这边刚忙完", "嗯嗯",
    "下次约", "辛苦了", "稍等一下", "看到了", "没问题", "回头说",
)

# 单个账号最多互加多少个同池账号为联系人（业内经验值 ≤10）
MAX_PEERS = 10


async def keep_online(manager: Any, account_id: int) -> dict[str, Any]:
    """上报在线状态（account.updateStatus offline=False）。"""
    from telethon import functions

    async with manager.session(account_id) as client:
        await client(functions.account.UpdateStatusRequest(offline=False))
    return {"action": "online", "ok": True}


async def read_random(manager: Any, account_id: int, max_dialogs: int = 5,
                      scan: int = 30) -> dict[str, Any]:
    """随机挑几个会话标记已读，模拟真人翻消息。"""
    read: list[str] = []
    async with manager.session(account_id) as client:
        dialogs = []
        async for d in client.iter_dialogs(limit=scan):
            if d.unread_count:
                dialogs.append(d)
        random.shuffle(dialogs)
        for d in dialogs[:max_dialogs]:
            try:
                await client.send_read_acknowledge(d.entity)
                read.append(str(d.name or d.id))
            except Exception:  # 单个会话失败不影响整体
                continue
            await human_delay(1.0, 3.0)
    return {"action": "read", "ok": True, "dialogs": read}


async def _resolve_peer(client: Any, acc: Any) -> Any:
    """把目标账号解析成可发送的实体。

    Telethon 无法凭空用 user_id 找人（会报 "Could not find the input entity"），
    所以优先用 username；只有手机号时先 ImportContacts 把对方加为联系人。
    """
    if acc.username:
        return await client.get_input_entity(acc.username)
    if acc.phone:
        from telethon.tl import functions, types

        res = await client(functions.contacts.ImportContactsRequest(
            [types.InputPhoneContact(client_id=0, phone=acc.phone,
                                     first_name=(acc.label or "tam")[:32], last_name="")]
        ))
        if res.users:
            return res.users[0]
    if acc.user_id:
        # 仅当对方已在本地实体缓存里时才能成功
        return await client.get_input_entity(int(acc.user_id))
    return None


async def chat_pair(manager: Any, from_id: int, to_id: int,
                    text: str | None = None) -> dict[str, Any]:
    """账号 A 给账号 B 发一条随机短语（先确保能解析到对方实体）。"""
    target = manager.db.get(to_id)
    if target is None:
        raise RuntimeError(f"账号 #{to_id} 不存在")
    body = text or random.choice(PHRASES)
    async with manager.session(from_id) as client:
        peer = await _resolve_peer(client, target)
        if peer is None:
            raise RuntimeError(
                f"账号 #{to_id} 既无 username 也无手机号，无法互聊（先跑一次健康检查补全信息）")
        msg = await client.send_message(peer, body)
    manager.db.log(from_id, "warmup_chat", True, f"-> #{to_id}")
    return {"action": "chat", "ok": True, "to": to_id, "message_id": msg.id}


def _pairs(ids: list[int], rounds: int) -> list[tuple[int, int]]:
    """生成互聊配对：每个账号最多跟 MAX_PEERS 个不同的同池账号说话。

    业内经验：同一批号里单号的联系人数不要超过 10，否则互相关联反而明显。
    同一对在一次养号里不重复，避免连发多条相同方向的消息。
    """
    out: list[tuple[int, int]] = []
    if len(ids) < 2:
        return out
    peers: dict[int, set[int]] = {a: set() for a in ids}
    for _ in range(max(1, rounds)):
        for a in ids:
            if len(peers[a]) >= MAX_PEERS:
                continue
            others = [b for b in ids if b != a and b not in peers[a]]
            if not others:
                continue
            b = random.choice(others)
            peers[a].add(b)
            out.append((a, b))
    random.shuffle(out)
    return out


async def warmup(manager: Any, account_ids: list[int], *, online: bool = True,
                 read: bool = True, chat: bool = True, rounds: int = 1,
                 concurrency: int = 3) -> dict[str, Any]:
    """对一批账号执行一轮养号。返回逐账号结果。

    online: 上报在线；read: 随机已读；chat: 账号之间互聊。
    """
    started = time.time()
    results: list[dict[str, Any]] = []

    async def task(aid: int) -> Any:
        steps: list[dict[str, Any]] = []
        if online:
            steps.append(await keep_online(manager, aid))
        if read:
            await human_delay(manager.s.action_min_delay, manager.s.action_max_delay)
            steps.append(await read_random(manager, aid))
        manager.db.update(aid, last_warm_at=time.time())
        manager.db.log(aid, "warmup", True, ",".join(s["action"] for s in steps))
        return steps

    if online or read:
        results = await manager.run_batch(account_ids, task,
                                          concurrency=concurrency, stagger=True)

    chats: list[dict[str, Any]] = []
    if chat:
        for a, b in _pairs(list(account_ids), rounds):
            try:
                chats.append(await chat_pair(manager, a, b))
            except Exception as exc:
                manager.db.log(a, "warmup_chat", False, repr(exc))
                chats.append({"action": "chat", "ok": False, "from": a, "to": b,
                              "error": repr(exc)})
            await human_delay(manager.s.action_min_delay, manager.s.action_max_delay)

    return {
        "accounts": len(account_ids),
        "elapsed_s": round(time.time() - started, 1),
        "results": results,
        "chats": chats,
    }
