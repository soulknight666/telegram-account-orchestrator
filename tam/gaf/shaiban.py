import os
import re
import asyncio
import tempfile
import random
import time
from datetime import datetime
import logging
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)
BACK_BUTTON_EMOJI_ID = "5877629862306385808"
CHECK_BAN_BACK = os.getenv("CHECK_BAN_BACK", "").replace('\\n', '\n')
MAX_PHONES = 100

_proxy_list = None
_proxy_list_last_load = 0
PROXY_LIST_CACHE_TIME = 60

user_ban_states = {}

def load_proxies():
    global _proxy_list, _proxy_list_last_load
    
    current_time = time.time()
    if _proxy_list is not None and (current_time - _proxy_list_last_load) < PROXY_LIST_CACHE_TIME:
        return _proxy_list
    
    proxy_file = "proxy.txt"
    valid_proxies = []
    
    if not os.path.exists(proxy_file):
        logger.warning("proxy.txt 文件不存在")
        _proxy_list = []
        _proxy_list_last_load = current_time
        return []
    
    try:
        with open(proxy_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                parts = line.split(':')
                if len(parts) >= 5:
                    ip, port, username, password, expire_ts = parts[:5]
                    try:
                        expire_timestamp = int(expire_ts)
                        if current_time < expire_timestamp:
                            proxy = {
                                'ip': ip,
                                'port': int(port),
                                'username': username,
                                'password': password,
                                'expire': expire_timestamp
                            }
                            valid_proxies.append(proxy)
                        else:
                            logger.debug(f"代理 {ip}:{port} 已过期")
                    except ValueError:
                        logger.warning(f"代理过期时间格式错误: {expire_ts}")
                        continue
    
    except Exception as e:
        logger.error(f"读取 proxy.txt 失败: {e}")
        _proxy_list = []
        _proxy_list_last_load = current_time
        return []
    
    _proxy_list = valid_proxies
    _proxy_list_last_load = current_time
    logger.info(f"加载了 {len(valid_proxies)} 个有效代理")
    return valid_proxies

def get_random_proxy():
    proxies = load_proxies()
    if not proxies:
        return None
    return random.choice(proxies)

def create_proxy_dict(proxy):
    return {
        'proxy_type': 'http',
        'addr': proxy['ip'],
        'port': proxy['port'],
        'username': proxy['username'],
        'password': proxy['password'],
        'rdns': True
    }

def create_back_button():
    return InlineKeyboardButton("返回主菜单", callback_data="back_to_main").to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}

async def show_check_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    
    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=CHECK_BAN_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    user_ban_states[user_id] = {"waiting": True}

async def handle_ban_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传TXT格式文件",
            parse_mode=ParseMode.HTML
        )
        return
    
    file = await context.bot.get_file(document.file_id)
    txt_path = f"downloads/ban_{user_id}_{int(datetime.now().timestamp())}.txt"
    os.makedirs("downloads", exist_ok=True)
    await file.download_to_drive(txt_path)
    
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            phones = [line.strip() for line in f if line.strip()]
        
        phones = [re.sub(r'\D', '', p) for p in phones if re.sub(r'\D', '', p)]
        phones = [f"+{p}" if not p.startswith('+') else p for p in phones]
        phones = list(dict.fromkeys(phones))[:MAX_PHONES]
        
        if not phones:
            await update.message.reply_text(
                "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 文件中没有有效手机号",
                parse_mode=ParseMode.HTML
            )
            return
        
        await process_ban_check(update, context, user_id, phones)
    except Exception as e:
        logger.error(f"处理文件失败: {e}")
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 处理失败: {str(e)}",
            parse_mode=ParseMode.HTML
        )
    finally:
        try: os.remove(txt_path)
        except: pass
        user_ban_states.pop(user_id, None)

async def process_ban_check(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str, phones: list):
    api_id = int(os.getenv("TELEGRAM_APP_ID"))
    api_hash = os.getenv("TELEGRAM_APP_HASH")
    admins = os.getenv("ADMIN_ID", "").split(",")
    
    status_msg = await update.message.reply_text(
        f"<tg-emoji emoji-id='5443127283898405358'>🔍</tg-emoji> 开始检测 {len(phones)} 个号码...",
        parse_mode=ParseMode.HTML
    )
    
    with tempfile.TemporaryDirectory() as temp_dir:
        banned, unbanned = [], []
        
        for i, phone in enumerate(phones, 1):
            proxy = None
            client = None
            try:
                proxy = get_random_proxy()
                proxy_dict = create_proxy_dict(proxy) if proxy else None
                
                client = TelegramClient(
                    tempfile.mktemp(dir=temp_dir), 
                    api_id, 
                    api_hash,
                    proxy=proxy_dict
                )
                await client.connect()
                
                try:
                    await client.send_code_request(phone)
                    unbanned.append(phone)
                except FloodWaitError as e:
                    unbanned.append(phone)
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    error_str = str(e).lower()
                    if "phone_number_invalid" in error_str or "phone_number_banned" in error_str:
                        banned.append(phone)
                    else:
                        unbanned.append(phone)
                
                await client.disconnect()
                
                if i % 10 == 0 or i == len(phones):
                    await status_msg.edit_text(
                        f"<tg-emoji emoji-id='5443127283898405358'>🔍</tg-emoji> 检测进度: {i}/{len(phones)}\n"
                        f"<tg-emoji emoji-id='5922712343011135025'>🚫</tg-emoji> 已封禁: {len(banned)} | "
                        f"<tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 正常: {len(unbanned)}",
                        parse_mode=ParseMode.HTML
                    )
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"检测 {phone} 失败: {e}")
                unbanned.append(phone)
                if client:
                    try: await client.disconnect()
                    except: pass
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        banned_file = os.path.join(temp_dir, "banned.txt")
        unbanned_file = os.path.join(temp_dir, "unbanned.txt")
        
        with open(banned_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(banned))
        with open(unbanned_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(unbanned))
        
        result_text = f"""<tg-emoji emoji-id='5909201569898827582'>✅</tg-emoji> <b>封禁检测完成</b>

<tg-emoji emoji-id='5931472654660800739'>📊</tg-emoji> 统计结果:
• <tg-emoji emoji-id='5886412370347036129'>📱</tg-emoji> 总号码数: <b>{len(phones)}</b>
• <tg-emoji emoji-id='5922712343011135025'>🚫</tg-emoji> 已封禁账号: <b>{len(banned)}</b>
• <tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 正常账号: <b>{len(unbanned)}</b>"""
        
        await update.message.reply_text(result_text, parse_mode=ParseMode.HTML)
        
        if banned:
            with open(banned_file, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"banned_{timestamp}.txt",
                    caption=f"<b><tg-emoji emoji-id='5922712343011135025'>🚫</tg-emoji> 已封禁号码 ({len(banned)}个)</b>\n包含所有检测为封禁状态的手机号",
                    parse_mode=ParseMode.HTML
                )
        
        if unbanned:
            with open(unbanned_file, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"unbanned_{timestamp}.txt",
                    caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 正常号码 ({len(unbanned)}个)</b>\n包含所有可正常接收验证码的手机号",
                    parse_mode=ParseMode.HTML
                )
        
        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id: continue
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"""<tg-emoji emoji-id='5909201569898827582'>📢</tg-emoji> <b>封禁检测任务完成</b>

<tg-emoji emoji-id='5886412370347036129'>👤</tg-emoji> 用户ID: <code>{user_id}</code>
<tg-emoji emoji-id='5931472654660800739'>📊</tg-emoji> 检测结果:
• 总号码: <b>{len(phones)}</b>
• <tg-emoji emoji-id='5922712343011135025'>🚫</tg-emoji> 已封禁: <b>{len(banned)}</b>
• <tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 正常: <b>{len(unbanned)}</b>""",
                    parse_mode=ParseMode.HTML
                )
                
                if banned:
                    with open(banned_file, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"banned_{user_id}_{timestamp}.txt",
                            caption=f"<b><tg-emoji emoji-id='5922712343011135025'>🚫</tg-emoji> 用户 {user_id} 已封禁号码 ({len(banned)}个)</b>",
                            parse_mode=ParseMode.HTML
                        )
                
                if unbanned:
                    with open(unbanned_file, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"unbanned_{user_id}_{timestamp}.txt",
                            caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 用户 {user_id} 正常号码 ({len(unbanned)}个)</b>",
                            parse_mode=ParseMode.HTML
                        )
            except Exception as e:
                logger.error(f"发送给管理员 {admin_id} 失败: {e}")
        
        await status_msg.delete()
