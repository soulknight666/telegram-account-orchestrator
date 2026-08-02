"""Telegram 机器人前端（菜单 + 命令两种用法都支持）。

这是 TAM 的第二个前端：网页控制台管“托管的账号”，机器人管“用户上传的号包”。
两者共用同一份 .env、同一个数据目录，由 tam.run 决定启哪个或都启。

功能引擎在 tam/gaf/ 里（从 GAFBot 移植）。踢设备 / 登录 / 取码这三项 TAM 自己已经有，
没有重复搬，机器人里用 /kick、/login 引导到 TAM 自己的实现。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from asyncio import Queue
from collections import defaultdict
from datetime import datetime

# 先把 .env 加载进环境，gaf 下的模块在 import 时就要读各种文案变量，顺序不能反。
from pathlib import Path

from .config import Settings, _load_dotenv

_load_dotenv(Path(os.getenv("TAM_ENV_FILE", ".env")))

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update  # noqa: E402
from telegram.constants import ChatMemberStatus, ParseMode  # noqa: E402
from telegram.ext import (  # noqa: E402
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .gaf.chaibao import (  # noqa: E402
    handle_unpack_document,
    handle_unpack_format,
    show_unpack_menu,
    user_unpack_states,
)
from .gaf.fangzhaohui import (  # noqa: E402
    handle_recovery_2fa_input,
    handle_recovery_document,
    handle_recovery_skip,
    show_prevent_recovery,
    user_recovery_states,
)
from .gaf.huzhuan import (  # noqa: E402
    handle_convert_document,
    handle_convert_selection,
    show_convert_menu,
    user_convert_states,
)
from .gaf.passkey import (  # noqa: E402
    handle_passkey_document,
    handle_passkey_selection,
    show_passkey_menu,
    user_passkey_states,
)
from .gaf.pay import (  # noqa: E402
    ORDER_TIMEOUT,
    OkayPay,
    add_order,
    cleanup_expired_orders,
    load_all_users,
    remove_order,
    save_all_users,
)
from .gaf.qingli import (  # noqa: E402
    handle_clean_document,
    handle_clean_selection,
    show_clean_menu,
    user_clean_states,
)
from .gaf.shaiban import handle_ban_document, show_check_ban, user_ban_states  # noqa: E402
from .gaf.shaihuo import SHAIHUO_BACK, handle_shaihuo_document  # noqa: E402
from .gaf.shailiao import (  # noqa: E402
    handle_material_document,
    show_material_menu,
    user_material_states,
)
from .gaf.shaireg import handle_regtime_document  # noqa: E402
from .gaf.shuangxiang import (  # noqa: E402
    handle_bidirectional_document,
    show_bidirectional,
    user_bidirectional_states,
)
from .gaf.xiaohui import DESTROY_BACK, handle_destroy_document  # noqa: E402
from .gaf.xiugai2fa import (  # noqa: E402
    handle_2fa_document,
    handle_2fa_mode_selection,
    handle_2fa_text_input,
    show_2fa_menu,
)
from .gaf.yinsi import (  # noqa: E402
    handle_privacy_confirm_upload,
    handle_privacy_document,
    handle_privacy_option,
    handle_privacy_reset_all,
    handle_privacy_selection,
    show_privacy_config,
    user_privacy_states,
)
from .gaf.zhenghe import (  # noqa: E402
    confirm_merge,
    handle_merge_document,
    show_merge_packs,
    user_merge_sessions,
)
from .gaf.zhuanapi import (  # noqa: E402
    handle_api_document,
    handle_api_mode,
    handle_api_text,
    show_convert_api,
    user_api_states,
)

logger = logging.getLogger("tam.bot")

TOKEN = os.getenv("TAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or ""
ADMIN_ID = str(os.getenv("TAM_BOT_ADMIN_ID") or os.getenv("ADMIN_ID") or "").strip()
JOIN_ID = os.getenv("START_JOIN_USERNAME")
OKPAY_ID = os.getenv("OKPAY_ID")
OKPAY_TOKEN = os.getenv("OKPAY_TOKEN")
OKPAY_PAYED = os.getenv("OKPAY_PAYED")
OKPAY_COST = os.getenv("OKPAY_COST")


def _msg(key: str, fallback: str = "") -> str:
    raw = os.getenv(key)
    return raw.replace("\\n", "\n") if raw else fallback


START_MESSAGE_TEMPLATE = _msg("START_MESSAGE", "你好 {USER}，请从下方选一个功能。")
UN_ACTIVE_MSG = _msg("START_MESSAGE_UN", "账号未激活，请先完成支付或联系管理员。")
MERGE_PACKS_BACK = _msg("MERGE_PACKS_BACK", "请上传要整合的号包 ZIP。")
REGTIME_BACK = _msg("REGTIME_BACK", "请上传号包 ZIP，返回注册时间。")
BACK_EMOJI = "5877629862306385808"

user_states: dict[str, str] = {}
user_queues: defaultdict[str, Queue] = defaultdict(Queue)
user_tasks: dict[str, asyncio.Task] = {}
queue_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
broadcast_message = None
broadcast_users: list[str] = []

# 菜单按钮 <-> 命令 <-> 中文名，三者一张表管到底，加功能只改这里
FEATURES: list[tuple[str, str, str, str]] = [
    # (callback_data, 命令, 中文名, 自定义 emoji id)
    ("check_active", "shaihuo", "账号筛活", "5942826671290715541"),
    ("change_2fa", "twofa", "修改 2FA", "6005570495603282482"),
    ("merge_packs", "merge", "整合号包", "5877307202888273539"),
    ("test_bidirectional", "bidir", "双向测试", "5922612721244704425"),
    ("privacy_config", "privacy", "隐私配置", "5931409969613116639"),
    ("format_convert", "convert", "格式互转", "6005843436479975944"),
    ("convert_api", "toapi", "转 API", "5877597667231534929"),
    ("prevent_recovery", "norecover", "防止找回", "5870734657384877785"),
    ("check_ban", "ban", "号码筛BAN", "5922712343011135025"),
    ("check_material", "material", "筛料能力", "5944940516754853337"),
    ("clean_account", "clean", "清理账号", "6007942490076745785"),
    ("unpack_tool", "unpack", "拆包工具", "5877540355187937244"),
    ("destroy_session", "destroy", "销毁会话", "5879937509579820068"),
    ("passkey_menu", "passkey", "Passkey 功能", "6008118472066732010"),
    ("check_regtime", "regtime", "注册时间", "5900104897885376843"),
]
CMD_TO_CALLBACK = {cmd: cb for cb, cmd, _n, _e in FEATURES}


def create_back_button() -> dict:
    return InlineKeyboardButton("返回主菜单", callback_data="back_to_main").to_dict() | {
        "icon_custom_emoji_id": BACK_EMOJI
    }


def _back_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[create_back_button()]])


def get_or_create_user(user) -> dict:
    data = load_all_users()
    uid = str(user.id)
    if uid not in data:
        # 没配价格 = 自托管自用，进来就是 VIP；配了价格才走付费门槛
        data[uid] = {
            "id": user.id,
            "full_name": user.full_name,
            "username": user.username,
            "status": "free" if OKPAY_COST else "vip",
            "created_at": time.time(),
        }
        save_all_users(data)
    return data.get(uid, {})


def is_vip(user_id: str) -> bool:
    if ADMIN_ID and str(user_id) == ADMIN_ID:
        return True
    return load_all_users().get(str(user_id), {}).get("status") == "vip"


# ---------- 每人一条串行队列，避免同一个人同时跑两个重活 ----------
async def message_queue_processor(user_id: str) -> None:
    try:
        while True:
            try:
                msg_type, update, context = await asyncio.wait_for(
                    user_queues[user_id].get(), timeout=60
                )
                try:
                    if msg_type == "callback":
                        await process_button_callback(update, context)
                    elif msg_type == "message":
                        await process_handle_message(update, context)
                    elif msg_type == "document":
                        await process_handle_document(update, context)
                except Exception as exc:  # 单条消息出错不能拖死整个队列
                    logger.error("处理用户 %s 消息失败: %s", user_id, exc, exc_info=True)
                    target = getattr(update, "callback_query", None) or update.message
                    try:
                        msg = target.message if hasattr(target, "message") else target
                        await msg.reply_text("❌ 处理失败，请重试")
                    except Exception:
                        pass
                user_queues[user_id].task_done()
            except asyncio.TimeoutError:
                async with queue_locks[user_id]:
                    if user_queues[user_id].empty():
                        user_tasks.pop(user_id, None)
                        user_queues.pop(user_id, None)
                        break
                    continue
            except asyncio.CancelledError:
                break
    finally:
        queue_locks.pop(user_id, None)


async def ensure_queue_processor(user_id: str) -> None:
    async with queue_locks[user_id]:
        task = user_tasks.get(user_id)
        if task is None or task.done():
            user_tasks[user_id] = asyncio.create_task(
                message_queue_processor(user_id), name=f"queue_{user_id}"
            )


async def _enqueue(kind: str, update: Update, context) -> None:
    user_id = str(update.effective_user.id)
    await user_queues[user_id].put((kind, update, context))
    await ensure_queue_processor(user_id)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _enqueue("callback", update, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _enqueue("message", update, context)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _enqueue("document", update, context)


# ---------- 功能分发 ----------
async def open_feature(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    """菜单点击和 /命令 最终都跑到这里，保证两种入口行为一致。"""
    user_id = str(update.effective_user.id)

    if data == "check_active":
        await _show_text(update, SHAIHUO_BACK or "请上传号包 ZIP 进行筛活。")
        user_states[user_id] = "waiting_shaihuo"
    elif data == "check_regtime":
        await _show_text(update, REGTIME_BACK)
        user_states[user_id] = "waiting_regtime_zip"
    elif data == "destroy_session":
        text = DESTROY_BACK.replace("\\n", "\n") if isinstance(DESTROY_BACK, str) else ""
        await _show_text(update, text or "请上传包含 .session 和 .json 的 ZIP。")
        user_states[user_id] = "waiting_destroy_zip"
    elif data == "change_2fa":
        await show_2fa_menu(update, context)
    elif data == "merge_packs":
        await show_merge_packs(update, context, MERGE_PACKS_BACK, user_states)
    elif data == "test_bidirectional":
        await show_bidirectional(update, context)
    elif data == "privacy_config":
        await show_privacy_config(update, context)
    elif data == "format_convert":
        await show_convert_menu(update, context)
    elif data == "convert_api":
        await show_convert_api(update, context)
    elif data == "clean_account":
        await show_clean_menu(update, context)
    elif data == "check_material":
        await show_material_menu(update, context)
    elif data == "check_ban":
        await show_check_ban(update, context)
    elif data == "prevent_recovery":
        await show_prevent_recovery(update, context)
    elif data == "unpack_tool":
        await show_unpack_menu(update, context)
    elif data == "passkey_menu":
        await show_passkey_menu(update, context)


async def _show_text(update: Update, text: str) -> None:
    """菜单进来就改原消息，命令进来就发新消息。"""
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=text, parse_mode=ParseMode.HTML, reply_markup=_back_markup()
        )
    else:
        await update.message.reply_text(
            text=text, parse_mode=ParseMode.HTML, reply_markup=_back_markup()
        )


async def process_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    await query.answer()

    if not is_vip(user_id) and data != "back_to_main":
        await query.edit_message_text(text=UN_ACTIVE_MSG, parse_mode=ParseMode.HTML)
        return

    if data == "back_to_main":
        await start(update, context)
        return

    # 二级菜单（各功能模块自己的选项）
    if data in ("2fa_input_mode", "2fa_auto_mode"):
        await handle_2fa_mode_selection(update, context)
    elif data == "confirm_merge":
        await confirm_merge(update, context, user_states)
    elif data in ("privacy_phone", "privacy_last_seen", "privacy_forward", "privacy_profile_photo"):
        await handle_privacy_selection(update, context)
    elif data in ("privacy_set_everyone", "privacy_set_contacts", "privacy_set_nobody"):
        await handle_privacy_option(update, context)
    elif data == "privacy_confirm_upload":
        await handle_privacy_confirm_upload(update, context)
    elif data == "privacy_reset_all":
        await handle_privacy_reset_all(update, context)
    elif data in ("convert_session_to_tdata", "convert_tdata_to_session"):
        await handle_convert_selection(update, context)
    elif data in ("api_no_2fa", "api_manual_2fa", "api_from_json"):
        await handle_api_mode(update, context)
    elif data in ("clean_chats", "clean_contacts", "clean_all", "clean_passkeys"):
        await handle_clean_selection(update, context)
    elif data == "recovery_skip_2fa":
        await handle_recovery_skip(update, context)
    elif data in ("passkey_create", "passkey_login"):
        await handle_passkey_selection(update, context)
    else:
        await open_feature(update, context, data)


async def process_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    text = update.message.text or ""

    # 先让正在等输入的功能拿走，否则会被当成普通消息吃掉
    if user_recovery_states.get(user_id, {}).get("state") == "waiting_2fa":
        await handle_recovery_2fa_input(update, context)
        return
    if "2fa_state" in context.user_data:
        await handle_2fa_text_input(update, context)
        return
    if user_api_states.get(user_id, {}).get("waiting_2fa"):
        await handle_api_text(update, context)
        return
    if user_unpack_states.get(user_id, {}).get("waiting_format"):
        await handle_unpack_format(update, context)
        return

    if not is_vip(user_id):
        await update.message.reply_text(UN_ACTIVE_MSG, parse_mode=ParseMode.HTML)
        return

    if user_states.get(user_id) == "waiting_regtime_zip":
        await handle_regtime_document(update, context, user_id, _back_markup())
        user_states.pop(user_id, None)


async def process_handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    state = user_states.get(user_id)

    # 按“谁在等文件”分发；模块自己的 state 字典优先于全局 user_states
    if user_recovery_states.get(user_id, {}).get("state") == "waiting_zip":
        await handle_recovery_document(update, context, user_id)
        return
    if user_id in user_ban_states:
        await handle_ban_document(update, context, user_id)
        return
    if context.user_data.get("2fa_state") == "waiting_2fa_zip":
        await handle_2fa_document(update, context)
        return
    if user_convert_states.get(user_id, {}).get("waiting_zip"):
        await handle_convert_document(update, context, user_id)
        return
    if user_api_states.get(user_id, {}).get("waiting_zip"):
        await handle_api_document(update, context, user_id)
        return
    if user_clean_states.get(user_id, {}).get("waiting_zip"):
        await handle_clean_document(update, context, user_id)
        return
    if user_unpack_states.get(user_id, {}).get("waiting_zip"):
        await handle_unpack_document(update, context, user_id)
        return
    if user_passkey_states.get(user_id, {}).get("waiting_zip"):
        await handle_passkey_document(update, context, user_id)
        return
    if state == "waiting_destroy_zip":
        await handle_destroy_document(update, context, user_id)
        user_states.pop(user_id, None)
        return

    if not is_vip(user_id):
        await update.message.reply_text(UN_ACTIVE_MSG, parse_mode=ParseMode.HTML)
        return

    if state == "waiting_regtime_zip":
        await handle_regtime_document(update, context, user_id, _back_markup())
        user_states.pop(user_id, None)
    elif state == "waiting_shaihuo":
        await handle_shaihuo_document(update, context, user_id, user_states)
    elif state == "waiting_merge_packs":
        await handle_merge_document(update, context, user_id)
    elif state == "waiting_bidirectional_zip" or user_id in user_bidirectional_states:
        await handle_bidirectional_document(update, context, user_id)
    elif user_privacy_states.get(user_id, {}).get("waiting_zip"):
        await handle_privacy_document(update, context, user_id)
    elif state == "waiting_material_zip" or user_id in user_material_states:
        await handle_material_document(update, context, user_id)
    else:
        await update.message.reply_text(
            "❌ 请先选功能再上传文件（/start 看菜单，或直接发 /help）",
            reply_markup=_back_markup(),
        )


# ---------- 付费 / VIP / 广播 ----------
async def check_pay_status(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    order_id = job.data["order_id"]
    user_id = job.data["user_id"]
    chat_id = job.data["chat_id"]

    if time.time() - job.data["time"] > ORDER_TIMEOUT:
        remove_order(order_id)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏰ <b>订单已过期</b>\n\n订单号：<code>{order_id}</code>\n重新下单请发 /start。",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            logger.error("发过期提醒失败: %s", exc)
        job.schedule_removal()
        return

    if OkayPay(OKPAY_ID, OKPAY_TOKEN).check_order(order_id):
        data = load_all_users()
        if str(user_id) in data:
            data[str(user_id)]["status"] = "vip"
            data[str(user_id)]["paid_at"] = time.time()
            save_all_users(data)
        remove_order(order_id)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✔️ <b>支付成功！</b>\n\n已升级为 VIP，发 /start 开始使用。\n订单号：<code>{order_id}</code>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            logger.error("发成功消息失败: %s", exc)
        job.schedule_removal()


async def periodic_order_cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    expired = cleanup_expired_orders()
    if expired:
        logger.info("清理了 %d 个过期订单", len(expired))


async def is_user_joined(context, user_id: int) -> bool:
    if not JOIN_ID:
        return True
    try:
        target = JOIN_ID if str(JOIN_ID).startswith(("@", "-100")) else f"@{JOIN_ID}"
        member = await context.bot.get_chat_member(chat_id=target, user_id=user_id)
        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception as exc:
        logger.error("检查入群状态失败: %s", exc)
        return False


async def send_payment_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    reply = (
        update.callback_query.message.reply_text
        if update.callback_query
        else update.message.reply_text
    )

    if not (OKPAY_ID and OKPAY_TOKEN):
        await reply(
            "💰 未配置支付，请联系管理员手动开通（管理员用 /vip 你的ID）。",
            parse_mode=ParseMode.HTML,
        )
        return

    pay_url, order_id = OkayPay(OKPAY_ID, OKPAY_TOKEN).get_pay_link(
        unique_id=f"VIP_{user.id}_{int(time.time())}",
        amount=OKPAY_COST,
        coin=OKPAY_PAYED,
        name=f"VIP Membership - {user.id}",
    )
    if not (pay_url and order_id):
        await reply("❌ 无法生成支付链接，请稍后再试或联系管理员。")
        return

    chat_id = update.effective_chat.id
    add_order(order_id, user.id, chat_id, time.time())
    expire = datetime.fromtimestamp(time.time() + ORDER_TIMEOUT).strftime("%H:%M:%S")
    await reply(
        f"{UN_ACTIVE_MSG}\n\n🩧 <b>订单详情</b>\n"
        f"💳 订单号：<code>{order_id}</code>\n"
        f"🪙 金额：{OKPAY_COST} {OKPAY_PAYED}\n"
        f"⏰ 过期：{expire}（{ORDER_TIMEOUT // 60} 分钟内有效）\n\n支付后稍等，系统会自动开通。",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"立即支付 {OKPAY_COST} {OKPAY_PAYED}", url=pay_url)]]
        ),
    )
    if context.job_queue:
        context.job_queue.run_repeating(
            check_pay_status,
            interval=5,
            first=3,
            data={"order_id": order_id, "user_id": user.id, "chat_id": chat_id, "time": time.time()},
            name=f"pay_check_{order_id}",
        )


def _admin_only(update: Update) -> bool:
    return bool(ADMIN_ID) and str(update.effective_user.id) == ADMIN_ID


async def set_vip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_only(update):
        return
    if not context.args:
        await update.message.reply_text("用法：/vip 用户ID")
        return
    target = context.args[0]
    data = load_all_users()
    if target not in data:
        data[target] = {"id": int(target) if target.isdigit() else target, "status": "vip"}
    data[target]["status"] = "vip"
    save_all_users(data)
    await update.message.reply_text(f"✅ 用户 {target} 已开通 VIP。")


async def remove_vip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_only(update):
        return
    if not context.args:
        await update.message.reply_text("用法：/unvip 用户ID")
        return
    target = context.args[0]
    data = load_all_users()
    if target in data:
        data[target]["status"] = "free"
        save_all_users(data)
        await update.message.reply_text(f"👤 用户 {target} 已降为普通用户。")
    else:
        await update.message.reply_text("🚫 找不到该用户。")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_only(update):
        return
    global broadcast_message, broadcast_users
    broadcast_message = None
    broadcast_users = list(load_all_users().keys())
    context.user_data["awaiting_broadcast"] = True
    await update.message.reply_text(f"请发要广播的内容，将发给 {len(broadcast_users)} 个用户。")


async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global broadcast_message
    if not _admin_only(update) or not context.user_data.get("awaiting_broadcast"):
        return
    broadcast_message = update.message
    context.user_data["awaiting_broadcast"] = False
    await update.message.reply_text(f"开始广播，共 {len(broadcast_users)} 个用户，每秒 20 条。")
    asyncio.create_task(send_broadcast(context))


async def send_broadcast(context: ContextTypes.DEFAULT_TYPE) -> None:
    global broadcast_message, broadcast_users
    if not broadcast_message or not broadcast_users:
        return
    ok = fail = 0
    for i, uid in enumerate(broadcast_users):
        try:
            await broadcast_message.copy(chat_id=uid)
            ok += 1
        except Exception as exc:
            logger.error("广播失败 %s: %s", uid, exc)
            fail += 1
        if (i + 1) % 20 == 0:
            await asyncio.sleep(1)
    if ADMIN_ID:
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"广播完成：成功 {ok}，失败 {fail}")
    broadcast_message = None
    broadcast_users = []


# ---------- 命令式入口 ----------
async def cmd_feature(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/shaihuo 、/twofa 这类命令，直接进对应功能，跳过主菜单。"""
    user_id = str(update.effective_user.id)
    get_or_create_user(update.effective_user)
    if not is_vip(user_id):
        await send_payment_prompt(update, context)
        return
    cmd = (update.message.text or "").split()[0].lstrip("/").split("@")[0]
    data = CMD_TO_CALLBACK.get(cmd)
    if data:
        await open_feature(update, context, data)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["<b>可用命令</b>", "/start 主菜单　/help 本帮助　/id 看自己ID　/status 会员状态", ""]
    lines += [f"/{cmd} — {name}" for _cb, cmd, name, _e in FEATURES]
    lines += [
        "",
        "<b>TAM 自带（不在机器人重复做）</b>",
        "/kick — 踢其他设备　/login — 账号登录（说明）",
    ]
    if _admin_only(update):
        lines += ["", "<b>管理员</b>", "/vip ID　/unvip ID　/gb 广播"]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(
        f"你的 ID：<code>{user.id}</code>", parse_mode=ParseMode.HTML
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    get_or_create_user(update.effective_user)
    vip = is_vip(user_id)
    await update.message.reply_text(
        f"会员状态：{'VIP（已激活）' if vip else '普通（未激活）'}\nID：<code>{user_id}</code>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_kick_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """踢设备 TAM 自己已经有一套（带校验+重试），不在机器人里再做一份。"""
    port = os.getenv("TAM_PORT", "8848")
    await update.message.reply_text(
        "🔧 踢其他设备由 TAM 托管侧完成（带踢后校验与失败重试）：\n"
        f"• 网页：http://127.0.0.1:{port} 账号列表 → 清设备\n"
        "• 命令行：python -m tam.cli devices <id>\n"
        "• 自动：本机接管满 TAM_AUTO_KICK_HOURS 小时自动执行",
    )


async def cmd_login_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    port = os.getenv("TAM_PORT", "8848")
    await update.message.reply_text(
        "🔑 账号登录/导入由 TAM 托管侧完成（会话加密入库）：\n"
        f"• 网页：http://127.0.0.1:{port}\n"
        "• 命令行：python -m tam.cli login <id> / import-tdata <path>",
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.callback_query:
        user = update.callback_query.from_user
    else:
        user = update.effective_user
    user_id = str(user.id)

    # 回主菜单 = 清干净所有半成品状态，避免上一个功能的残留把文件吃错地方
    for store in (
        user_states, user_merge_sessions, user_bidirectional_states, user_privacy_states,
        user_convert_states, user_api_states, user_clean_states, user_material_states,
        user_ban_states, user_recovery_states, user_unpack_states, user_passkey_states,
    ):
        store.pop(user_id, None)
    context.user_data.clear()

    get_or_create_user(user)

    if JOIN_ID and not await is_user_joined(context, user.id):
        link = "https://t.me/" + str(JOIN_ID).lstrip("@")
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("点击加入频道/群组", url=link)]])
        text = _msg("START_JOIN_MESSAGE", "请先加入频道后再使用。")
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, reply_markup=markup, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        return

    if not is_vip(user_id):
        await send_payment_prompt(update, context)
        return

    text = START_MESSAGE_TEMPLATE.replace("{USER}", user.full_name or "")
    text = re.sub(r"\* (.*)", r"* <code>\1</code>", text)

    def btn(name: str, data: str, emoji_id: str) -> dict:
        return InlineKeyboardButton(name, callback_data=data).to_dict() | {
            "icon_custom_emoji_id": emoji_id
        }

    buttons = [btn(name, cb, emoji) for cb, _cmd, name, emoji in FEATURES]
    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]

    for i in range(1, 4):  # 底部自定义广告位，格式：文字-链接
        ads = os.getenv(f"ADS_{i}")
        if ads and "-" in ads:
            label, url = ads.split("-", 1)
            keyboard.append([InlineKeyboardButton(text=label.strip(), url=url.strip())])

    markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=text.strip(), parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await update.message.reply_text(
            text=text.strip(), parse_mode=ParseMode.HTML, reply_markup=markup)


async def post_init(application) -> None:
    cmds = [BotCommand("start", "主菜单"), BotCommand("help", "命令列表")]
    cmds += [BotCommand(cmd, name) for _cb, cmd, name, _e in FEATURES]
    cmds += [BotCommand("status", "会员状态"), BotCommand("id", "看自己ID")]
    await application.bot.set_my_commands(cmds[:100])


def build_application():
    if not TOKEN:
        raise SystemExit(
            "未配置机器人 Token。请在 .env 里设 TAM_BOT_TOKEN=xxx，或把 TAM_FRONTEND 改成 web。"
        )
    settings = Settings.load()
    os.environ.setdefault("TAM_DATA_DIR", str(settings.data_dir))
    os.makedirs(settings.data_dir / "downloads", exist_ok=True)
    os.makedirs(settings.data_dir / "acd", exist_ok=True)
    cleanup_expired_orders()

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("kick", cmd_kick_hint))
    app.add_handler(CommandHandler("login", cmd_login_hint))
    app.add_handler(CommandHandler("vip", set_vip))
    app.add_handler(CommandHandler("unvip", remove_vip))
    app.add_handler(CommandHandler("gb", broadcast))
    for _cb, cmd, _name, _e in FEATURES:
        app.add_handler(CommandHandler(cmd, cmd_feature))
    app.add_handler(CallbackQueryHandler(button_callback))

    async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if _admin_only(update) and context.user_data.get("awaiting_broadcast"):
            await handle_broadcast_message(update, context)
        else:
            await handle_message(update, context)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    if app.job_queue:
        app.job_queue.run_repeating(periodic_order_cleanup, interval=60, first=10)
    return app


def run() -> None:
    """阻塞运行机器人（自己管事件循环，适合单独跑）。"""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
    )
    app = build_application()
    print("TAM 机器人前端已启动（菜单 + 命令）")
    app.run_polling()


if __name__ == "__main__":
    run()
