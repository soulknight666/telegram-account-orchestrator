# 源自 GAFBot zhuanapi.py (MIT)
# https://github.com/kugua332334554/GAFBot
# Copyright (c) 2026 kugua311 — 见仓库根目录 NOTICE.GAFBot

import os
import json
import zipfile
import shutil
import tempfile
import time
import random
import string
import asyncio
import re
from datetime import datetime
from telethon import TelegramClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import logging
from opentele.api import API
from opentele.td import TDesktop

logger = logging.getLogger(__name__)
from dotenv import load_dotenv
load_dotenv()

CONVERT_API_BACK = os.getenv("CONVERT_API_BACK", "").replace('\\n', '\n')
SERVER_IP = os.getenv("SERVER_IP")
API_PORT = os.getenv("API_PORT", "5099")
DM = os.getenv("DM", "")
BACK_BUTTON_EMOJI_ID = "5877629862306385808"
_proxy_list = None
_proxy_list_last_load = 0
PROXY_LIST_CACHE_TIME = 60
MAX_ZIP_SIZE = 50 * 1024 * 1024

user_api_states = {}

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
    return InlineKeyboardButton(
        "返回主菜单",
        callback_data="back_to_main"
    ).to_dict() | {"icon_custom_emoji_id": BACK_BUTTON_EMOJI_ID}

def safe_extract(zip_ref, target_dir):
    for member in zip_ref.infolist():
        member_path = os.path.normpath(member.filename)
        if member_path.startswith(('..', '/', '\\')):
            raise Exception(f"非法路径: {member.filename}")
        zip_ref.extract(member, target_dir)

def generate_non_linux_api():
    max_attempts = 100
    attempt = 0
    while attempt < max_attempts:
        api = API.TelegramDesktop.Generate()
        if 'linux' not in api.device_model.lower():
            return api
        attempt += 1
    api = API.TelegramDesktop.Generate()
    api.device_model = "Desktop"
    return api

def find_tdata_folders(root_dir):
    tdata_dirs = set()
    for root, dirs, files in os.walk(root_dir):
        if os.path.basename(root) == 'tdata':
            if any(f in files for f in ['key_datas', 'map']):
                tdata_dirs.add(root)
        elif 'tdata' in dirs:
            potential = os.path.join(root, 'tdata')
            if os.path.exists(potential):
                sub_files = os.listdir(potential)
                if any(f in sub_files for f in ['key_datas', 'map']):
                    tdata_dirs.add(potential)
    return list(tdata_dirs)

def read_2fa_from_folder(folder_path: str):
    for file in os.listdir(folder_path):
        if file.lower() in ['2fa.txt', '2fa', 'password.txt']:
            try:
                with open(os.path.join(folder_path, file), 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except:
                pass
    return None

def sanitize_2fa(text: str) -> str:
    text = re.sub(r'[\x00-\x1f\x7f]', '', text)
    if len(text) > 64:
        raise ValueError("2FA密码过长（≤64）")
    return text

def clean_phone(phone: str) -> str:
    cleaned = re.sub(r'[^\d\+\(\)\s\-]', '', str(phone))
    cleaned = cleaned.strip()
    return cleaned if cleaned else "unknown"

async def convert_tdata_to_session_with_proxy(tdata_dir, output_dir, twofa, proxy_dict):
    API_ID = int(os.getenv("TELEGRAM_APP_ID", "2040"))
    API_HASH = os.getenv("TELEGRAM_APP_HASH", "b18441a1ff607e10a989891a5462e627")
    try:
        tdesk = TDesktop(tdata_dir)
        if not tdesk.isLoaded():
            return False, None, None, None, "tdata 文件无法加载"
        from opentele.api import UseCurrentSession
        client = await tdesk.ToTelethon(
            session=os.path.join(output_dir, "temp.session"),
            flag=UseCurrentSession,
            proxy=proxy_dict
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return False, None, None, None, "会话未授权"
        me = await client.get_me()
        if not me:
            await client.disconnect()
            return False, None, None, None, "无法获取用户信息"
        phone = me.phone
        if not phone:
            await client.disconnect()
            return False, None, None, None, "无法获取手机号"
        temp_session = os.path.join(output_dir, "temp.session")
        final_session = os.path.join(output_dir, f"{phone}.session")
        if os.path.exists(temp_session):
            shutil.move(temp_session, final_session)
        random_api = generate_non_linux_api()
        try:
            if hasattr(me, 'date') and me.date:
                reg_time = datetime.fromtimestamp(me.date.timestamp()).strftime("%Y-%m-%d")
            else:
                reg_time = datetime.now().strftime("%Y-%m-%d")
        except Exception:
            reg_time = datetime.now().strftime("%Y-%m-%d")
        json_data = {
            "api_id": API_ID,
            "api_hash": API_HASH,
            "device_model": random_api.device_model,
            "system_version": random_api.system_version,
            "app_version": random_api.app_version,
            "system_lang_code": random_api.system_lang_code,
            "lang_pack": random_api.lang_pack,
            "lang_code": random_api.lang_code,
            "pid": random_api.pid,
            "user_id": me.id,
            "phone": phone,
            "twofa": twofa if twofa else "",
            "password": twofa if twofa else "",
            "app_id": API_ID,
            "app_hash": API_HASH,
            "session_file": phone,
            "device": random_api.device_model,
            "username": me.username or "",
            "sex": None,
            "avatar": "img/default.png",
            "package_id": "",
            "installer": "",
            "ipv6": False,
            "SDK": random_api.system_version,
            "sdk": random_api.system_version,
            "system_lang_pack": random_api.system_lang_code,
            "premium": getattr(me, 'premium', False),
            "reg_time": reg_time
        }
        json_path = os.path.join(output_dir, f"{phone}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        await client.disconnect()
        return True, phone, final_session, json_path, None
    except Exception as e:
        logger.error(f"转换 tdata 失败 {tdata_dir}: {e}")
        return False, None, None, None, str(e)

async def show_convert_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("无2FA", callback_data="api_no_2fa")],
        [InlineKeyboardButton("手动输入2FA", callback_data="api_manual_2fa")],
        [InlineKeyboardButton("从JSON提取", callback_data="api_from_json")],
        [create_back_button()]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text="请选择2FA处理方式：",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    user_api_states[str(query.from_user.id)] = {"waiting_mode": True}

async def handle_api_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    await query.answer()
    if data == "api_no_2fa":
        user_api_states[user_id] = {"mode": "no_2fa", "waiting_zip": True}
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text="请上传session或tdata的ZIP包（无2FA）",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    elif data == "api_manual_2fa":
        user_api_states[user_id] = {"mode": "manual", "waiting_2fa": True}
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text="请输入2FA密码：",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    elif data == "api_from_json":
        user_api_states[user_id] = {"mode": "from_json", "waiting_zip": True}
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text="请上传session或tdata的ZIP包（将自动从JSON提取2FA和手机号）",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

async def handle_api_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text
    if user_id not in user_api_states or not user_api_states[user_id].get("waiting_2fa"):
        return
    try:
        safe_2fa = sanitize_2fa(text.strip())
    except ValueError as e:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> {str(e)}",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        user_api_states.pop(user_id, None)
        return
    user_api_states[user_id]["two_fa"] = safe_2fa
    user_api_states[user_id]["waiting_2fa"] = False
    user_api_states[user_id]["waiting_zip"] = True
    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "2FA已记录，请上传session或tdata的ZIP包",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

def generate_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))

async def handle_api_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document
    if not document.file_name.endswith('.zip'):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传ZIP格式",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        user_api_states.pop(user_id, None)
        return
    if document.file_size > MAX_ZIP_SIZE:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 文件过大（最大{MAX_ZIP_SIZE//(1024*1024)}MB）",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        user_api_states.pop(user_id, None)
        return
    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/api_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)
        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔍</tg-emoji> 开始处理转换...",
            parse_mode='HTML'
        )
        mode = user_api_states[user_id].get("mode", "no_2fa")
        two_fa = user_api_states[user_id].get("two_fa") if mode == "manual" else None
        await process_conversion(update, context, zip_path, user_id, mode, two_fa)
        try:
            os.remove(zip_path)
        except:
            pass
    except Exception as e:
        logger.error(f"处理失败: {e}")
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 处理失败: {str(e)[:50]}",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    finally:
        user_api_states.pop(user_id, None)
        try:
            await status_msg.delete()
        except:
            pass

async def process_conversion(update, context, zip_path, user_id, mode, manual_2fa=None):
    api_id = int(os.getenv("TELEGRAM_APP_ID"))
    api_hash = os.getenv("TELEGRAM_APP_HASH")
    with tempfile.TemporaryDirectory() as tmp:
        extract_dir = os.path.join(tmp, "extracted")
        os.makedirs(extract_dir)
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                safe_extract(zf, extract_dir)
        except Exception as e:
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 解压失败: {str(e)[:50]}",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            return
        session_files = []
        json_files = {}
        tdata_dirs = find_tdata_folders(extract_dir)
        for root, _, files in os.walk(extract_dir):
            for f in files:
                if f.endswith('.session'):
                    session_files.append(os.path.join(root, f))
                elif f.endswith('.json'):
                    base = os.path.splitext(f)[0]
                    json_files[base] = os.path.join(root, f)
        accounts = []
        if session_files:
            for sess in session_files:
                session_name = os.path.splitext(os.path.basename(sess))[0]
                json_file = json_files.get(session_name)
                accounts.append((session_name, sess, json_file, None))
        elif tdata_dirs:
            status_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>检测到tdata，正在转换为session...</b>

找到 <b>{len(tdata_dirs)}</b> 个tdata文件夹
请稍候...""",
                parse_mode='HTML'
            )
            convert_temp_dir = os.path.join(tmp, "converted_sessions")
            os.makedirs(convert_temp_dir, exist_ok=True)
            for i, tdata_dir in enumerate(tdata_dirs, 1):
                parent_dir = os.path.dirname(tdata_dir)
                twofa = read_2fa_from_folder(parent_dir)
                proxy = get_random_proxy()
                proxy_dict = create_proxy_dict(proxy) if proxy else None
                account_out = os.path.join(convert_temp_dir, f"acc_{i}")
                os.makedirs(account_out, exist_ok=True)
                success, phone, sess_path, json_path, err = await convert_tdata_to_session_with_proxy(
                    tdata_dir, account_out, twofa, proxy_dict
                )
                if success and sess_path and json_path:
                    accounts.append((phone, sess_path, json_path, tdata_dir))
                else:
                    logger.error(f"转换失败 {tdata_dir}: {err}")
                if i % 3 == 0 or i == len(tdata_dirs):
                    try:
                        await status_msg.edit_text(
                            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>tdata转换进度</b>

进度: {i}/{len(tdata_dirs)}
成功: {len(accounts)}""",
                            parse_mode='HTML'
                        )
                    except:
                        pass
                await asyncio.sleep(0.2)
            try:
                await status_msg.delete()
            except:
                pass
            if not accounts:
                keyboard = [[create_back_button()]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 所有tdata转换失败，无法继续",
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                return
        else:
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 未找到session或tdata文件",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            return
        progress_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id='5839200986022812209'>🔄</tg-emoji> 处理中: 0/{len(accounts)}",
            parse_mode='HTML'
        )
        os.makedirs("acd", exist_ok=True)
        api_data = {}
        used_ids = set()
        lines = []
        api_prefix = f"http://{SERVER_IP}:{API_PORT}"
        if DM:
            api_prefix = f"{DM}"
        for i, (phone, session_path, json_path, tdata_dir) in enumerate(accounts, 1):
            new_id = generate_id()
            while new_id in used_ids:
                new_id = generate_id()
            used_ids.add(new_id)
            new_session = os.path.join("acd", f"{new_id}.session")
            shutil.copy2(session_path, new_session)
            json_config = {}
            if json_path and os.path.exists(json_path):
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        json_config = json.load(f)
                except Exception as e:
                    logger.debug(f"读取JSON失败 {json_path}: {e}")
            _app_id = json_config.get('app_id')
            if _app_id is None:
                _app_id = api_id
            else:
                try:
                    _app_id = int(_app_id)
                except (ValueError, TypeError):
                    _app_id = api_id
            _app_hash = json_config.get('app_hash')
            if not _app_hash:
                _app_hash = api_hash
            device_model = json_config.get('device_model') or None
            app_version = json_config.get('app_version') or None
            system_lang_code = json_config.get('system_lang_code') or None
            system_vision = json_config.get('sdk') or json_config.get('system_version') or None
            lang_pack = json_config.get('lang_pack') or None
            phone_number = "unknown"
            phone_fields = ['phone', 'number', 'phone_number', 'Phone', '账号', '电话号码', '手机号']
            for field in phone_fields:
                val = json_config.get(field)
                if val:
                    phone_number = str(val)
                    break
            if phone_number == "unknown":
                phone_number = phone if phone else os.path.splitext(os.path.basename(session_path))[0]
            phone_number = clean_phone(phone_number)
            two_fa = None
            if mode == "manual":
                two_fa = manual_2fa
            elif mode == "from_json":
                if json_path and os.path.exists(json_path):
                    try:
                        with open(json_path, 'r', encoding='utf-8') as f:
                            json_data = json.load(f)
                            two_fa = (json_data.get('2fa') or
                                     json_data.get('2FA') or
                                     json_data.get('two_fa') or
                                     json_data.get('password') or
                                     json_data.get('twofa'))
                            if two_fa:
                                two_fa = sanitize_2fa(str(two_fa))
                    except Exception as e:
                        logger.debug(f"读取JSON失败 {json_path}: {e}")
            api_data[new_id] = {
                "phone": phone_number,
                "two_fa": two_fa if two_fa else "",
                "app_id": _app_id,
                "app_hash": _app_hash,
                "device_model": device_model,
                "app_version": app_version,
                "system_lang_code": system_lang_code,
                "system_vision": system_vision,
                "lang_pack": lang_pack
            }
            line = f"{phone_number} --- {api_prefix}/getcode?id={new_id}"
            if two_fa:
                line += f" (2FA: {two_fa})"
            lines.append(line)
            if i % 5 == 0 or i == len(accounts):
                try:
                    await progress_msg.edit_text(
                        f"<tg-emoji emoji-id='5839200986022812209'>🔄</tg-emoji> 处理中: {i}/{len(accounts)}",
                        parse_mode='HTML'
                    )
                except:
                    pass
            await asyncio.sleep(0.3)
        json_path = os.path.join("acd", "api.json")
        existing_data = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
            except Exception as e:
                logger.error(f"读取现有 api.json 失败: {e}")
        existing_data.update(api_data)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f, indent=2, ensure_ascii=False)
        txt_path = os.path.join(tmp, "api_links.txt")
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))
        await progress_msg.delete()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>转换完成</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 总计: <b>{len(accounts)}</b>""",
            parse_mode='HTML'
        )
        with open(txt_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename=f"api_links_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                caption=f'<b><tg-emoji emoji-id="5877540355187937244">📁</tg-emoji> API链接</b>',
                parse_mode='HTML'
            )
