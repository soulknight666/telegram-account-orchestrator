import os
import logging
import zipfile
import shutil
import tempfile
import json
import asyncio
import time
import re
import sqlite3
from typing import Optional, Union, Tuple, List
from datetime import datetime
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from opentele.td import TDesktop
from opentele.api import UseCurrentSession, API
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

FORMAT_CONVERT_BACK = os.getenv("FORMAT_CONVERT_BACK", "").replace('\\n', '\n')
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
BACK_BUTTON_EMOJI_ID = "5877629862306385808"
API_ID = int(os.getenv("TELEGRAM_APP_ID", "2040"))
API_HASH = os.getenv("TELEGRAM_APP_HASH", "b18441a1ff607e10a989891a5462e627")

user_convert_states = {}

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

async def show_convert_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [
            InlineKeyboardButton("Session → Tdata", callback_data="convert_session_to_tdata").to_dict() | {"icon_custom_emoji_id": "5877307202888273539"},
            InlineKeyboardButton("Tdata → Session", callback_data="convert_tdata_to_session").to_dict() | {"icon_custom_emoji_id": "6005570495603282482"}
        ],
        [create_back_button()]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text=FORMAT_CONVERT_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def handle_convert_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    await query.answer()

    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if data == "convert_session_to_tdata":
        await query.edit_message_text(
            text="""<tg-emoji emoji-id="5877307202888273539">🔄</tg-emoji> <b>Session → Tdata 转换</b>

请上传包含 <code>.session</code> 文件的ZIP压缩包

<tg-emoji emoji-id="5775887550262546277">📌</tg-emoji> 转换后将返回包含tdata文件夹的ZIP包""",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        user_convert_states[user_id] = {
            "mode": "session_to_tdata",
            "waiting_zip": True
        }

    elif data == "convert_tdata_to_session":
        await query.edit_message_text(
            text="""<tg-emoji emoji-id="6005570495603282482">🔄</tg-emoji> <b>Tdata → Session 转换</b>

请上传包含 <code>tdata</code> 文件夹的ZIP压缩包

<tg-emoji emoji-id="5775887550262546277">📌</tg-emoji> 转换后将返回包含session+json的ZIP包""",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        user_convert_states[user_id] = {
            "mode": "tdata_to_session",
            "waiting_zip": True
        }

def get_total_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total

def read_2fa_from_folder(folder_path: str) -> Optional[str]:
    allowed_names = {'2fa', 'twofa', 'password'}
    
    for file in os.listdir(folder_path):
        name, ext = os.path.splitext(file)
        if ext.lower() == '.txt' and name.lower() in allowed_names:
            try:
                with open(os.path.join(folder_path, file), 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except:
                pass
        elif not ext and file.lower() in allowed_names:
            try:
                with open(os.path.join(folder_path, file), 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except:
                pass
    
    for file in os.listdir(folder_path):
        if file.lower().endswith('.json'):
            try:
                with open(os.path.join(folder_path, file), 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, value in data.items():
                        if key.lower() in allowed_names and value:
                            return str(value).strip()
            except:
                continue
    
    return None

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

async def convert_session_to_tdata(session_path: str, output_dir: str, twofa: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    start_time = time.time()
    log_time(f"开始转换 session 到 tdata: {session_path}")
    
    temp_dir = tempfile.mkdtemp(prefix="huzhuan_temp_")
    temp_session = os.path.join(temp_dir, os.path.basename(session_path))
    try:
        shutil.copy2(session_path, temp_session)
        log_time(f"已创建临时 session 副本: {temp_session}")
        use_session = temp_session
    except Exception as e:
        log_time(f"复制 session 到临时目录失败: {e}，将使用原文件")
        use_session = session_path
        temp_dir = None
    
    client = None
    session_dir = os.path.dirname(session_path)
    session_name = os.path.splitext(os.path.basename(session_path))[0]
    json_path = os.path.join(session_dir, f"{session_name}.json")
    
    json_config = {}
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                json_config = json.load(f)
        except Exception:
            pass
    
    api_id_val = API_ID
    api_hash_val = API_HASH
    if json_config:
        if 'app_id' in json_config and json_config['app_id']:
            try:
                api_id_val = int(json_config['app_id'])
            except (ValueError, TypeError):
                pass
        if 'app_hash' in json_config and json_config['app_hash']:
            api_hash_val = str(json_config['app_hash'])
    
    device_model = json_config.get('device_model') or None
    app_version = json_config.get('app_version') or None
    system_lang_code = json_config.get('system_lang_code') or None
    system_vision = json_config.get('system_version') or json_config.get('sdk') or None
    lang_pack = json_config.get('lang_pack') or None
    
    retry_count = 0
    while retry_count < 2:
        try:
            official_api = API.TelegramDesktop.Generate()
            if device_model is None:
                max_attempts = 100
                attempt = 0
                while 'linux' in official_api.device_model.lower() and attempt < max_attempts:
                    official_api = API.TelegramDesktop.Generate()
                    attempt += 1
                if 'linux' in official_api.device_model.lower():
                    official_api.device_model = "Desktop"
            else:
                official_api.device_model = device_model
            
            official_api.api_id = api_id_val
            official_api.api_hash = api_hash_val
            if app_version:
                official_api.app_version = app_version
            if system_lang_code:
                official_api.system_lang_code = system_lang_code
            if system_vision:
                official_api.system_version = system_vision
            if lang_pack:
                official_api.lang_pack = lang_pack
                official_api.lang_code = lang_pack

            client = TelegramClient(
                use_session,
                api=official_api,
                receive_updates=False,
                timeout=10,
                connection_retries=1
            )
            break
        except ValueError as e:
            err_msg = str(e)
            if ("not enough values to unpack (expected 6, got 5)" in err_msg or
                "too many values to unpack (expected 6)" in err_msg) and retry_count == 0:
                logger.warning(f"检测到 session 文件格式问题: {use_session}，尝试自动修复")
                if repair_session(use_session):
                    logger.info(f"修复完成，重试创建客户端")
                    retry_count += 1
                    continue
                else:
                    logger.error(f"自动修复失败，无法使用该 session: {use_session}")
                    return False, f"Session文件损坏且修复失败", None
            else:
                return False, f"创建客户端失败: {err_msg[:30]}", None
        except Exception as ex:
            return False, f"创建客户端异常: {str(ex)[:30]}", None

    try:
        connect_start = time.time()
        await asyncio.wait_for(client.connect(), timeout=15)
        log_time(f"连接耗时: {time.time() - connect_start:.2f}秒")
        
        auth_start = time.time()
        if not await asyncio.wait_for(client.is_user_authorized(), timeout=10):
            return False, "session未授权", None
        log_time(f"授权检查耗时: {time.time() - auth_start:.2f}秒")
        
        me_start = time.time()
        me = await asyncio.wait_for(client.get_me(), timeout=10)
        if not me:
            return False, "无法获取用户信息", None
        log_time(f"获取用户信息耗时: {time.time() - me_start:.2f}秒")

        tdesk = await client.ToTDesktop(flag=UseCurrentSession)

        account_name = me.phone or str(me.id)
        account_dir = os.path.join(output_dir, account_name)
        tdata_dir = os.path.join(account_dir, "tdata")
        os.makedirs(tdata_dir, exist_ok=True)

        tdesk.SaveTData(tdata_dir)
        with open(os.path.join(account_dir, "2fa.txt"), 'w', encoding='utf-8') as f:
            if twofa:
                f.write(twofa)
            else:
                f.write("无2FA密码。")

        if os.path.exists(session_path):
            shutil.copy2(session_path, os.path.join(account_dir, os.path.basename(session_path)))
        json_orig = os.path.splitext(session_path)[0] + '.json'
        if os.path.exists(json_orig):
            shutil.copy2(json_orig, os.path.join(account_dir, os.path.basename(json_orig)))

        total_time = time.time() - start_time
        log_time(f"Session 转 Tdata 成功: {session_path} -> {account_name}，耗时 {total_time:.2f}秒")
        return True, account_name, account_dir

    except asyncio.TimeoutError:
        return False, "网络操作超时", None
    except Exception as e:
        return False, str(e), None
    finally:
        if client:
            disconnect_start = time.time()
            await client.disconnect()
            log_time(f"断开连接耗时: {time.time() - disconnect_start:.2f}秒")
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            log_time(f"已清理临时目录: {temp_dir}")

async def convert_tdata_to_session(tdata_dir: str, output_dir: str, twofa: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    start_time = time.time()
    log_time(f"开始转换 tdata 到 session: {tdata_dir}")
    client = None
    try:
        tdesk = TDesktop(tdata_dir)
        if not tdesk.isLoaded():
            return False, "tdata文件无法加载", None

        session_name = f"account.session"
        session_path = os.path.join(output_dir, session_name)

        client = await tdesk.ToTelethon(session=session_path, flag=UseCurrentSession)

        connect_start = time.time()
        await asyncio.wait_for(client.connect(), timeout=15)
        log_time(f"连接耗时: {time.time() - connect_start:.2f}秒")
        
        auth_start = time.time()
        if not await asyncio.wait_for(client.is_user_authorized(), timeout=10):
            return False, "会话未授权", None
        log_time(f"授权检查耗时: {time.time() - auth_start:.2f}秒")
        
        me_start = time.time()
        me = await asyncio.wait_for(client.get_me(), timeout=10)
        if not me:
            return False, "无法获取用户信息", None
        log_time(f"获取用户信息耗时: {time.time() - me_start:.2f}秒")

        phone = me.phone
        if not phone:
            return False, "无法获取手机号", None

        new_session_path = os.path.join(output_dir, f"{phone}.session")
        if os.path.exists(session_path):
            shutil.move(session_path, new_session_path)

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

        total_time = time.time() - start_time
        log_time(f"Tdata 转 Session 成功: {tdata_dir} -> {phone}，耗时 {total_time:.2f}秒")
        return True, phone, output_dir

    except asyncio.TimeoutError:
        return False, "网络操作超时", None
    except Exception as e:
        return False, str(e), None
    finally:
        if client:
            disconnect_start = time.time()
            await client.disconnect()
            log_time(f"断开连接耗时: {time.time() - disconnect_start:.2f}秒")

async def handle_convert_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document

    if user_id not in user_convert_states:
        return

    state = user_convert_states[user_id]
    if not state.get("waiting_zip"):
        return

    if not document.file_name.endswith('.zip'):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        user_convert_states.pop(user_id, None)
        return

    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> 正在下载文件...",
        parse_mode=ParseMode.HTML
    )

    zip_path = None
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/convert_{user_id}_{int(datetime.now().timestamp())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)

        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔄</tg-emoji> 开始处理转换任务...",
            parse_mode=ParseMode.HTML
        )

        mode = state["mode"]

        if mode == "session_to_tdata":
            await process_session_to_tdata(update, context, zip_path, user_id)
        else:
            await process_tdata_to_session(update, context, zip_path, user_id)

    except Exception as e:
        logger.error(f"转换失败: {e}")
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 处理失败: {str(e)}",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    finally:
        user_convert_states.pop(user_id, None)
        if zip_path and os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except:
                pass
        try:
            await status_msg.delete()
        except:
            pass

async def process_session_to_tdata(update: Update, context: ContextTypes.DEFAULT_TYPE, zip_path: str, user_id: str):
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                safe_extract(zip_ref, extract_dir)

                extracted_size = get_total_size(extract_dir)
                if extracted_size > MAX_EXTRACT_SIZE:
                    raise Exception(f"文件过大 ({extracted_size//1024//1024}MB > {MAX_EXTRACT_SIZE//1024//1024}MB)")
        except Exception as e:
            error_zip_name = f"session_to_tdata_error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            error_zip_path = os.path.join(temp_dir, error_zip_name)
            with zipfile.ZipFile(error_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.writestr("error.txt", f"解压失败: {str(e)}")
            with open(error_zip_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=error_zip_name,
                    caption="<tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> 处理失败，请检查压缩包",
                    parse_mode=ParseMode.HTML
                )
            return

        session_files = []
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.endswith('.session'):
                    session_files.append(os.path.join(root, file))

        if not session_files:
            error_zip_name = f"session_to_tdata_error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            error_zip_path = os.path.join(temp_dir, error_zip_name)
            with zipfile.ZipFile(error_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.writestr("error.txt", "未找到任何 .session 文件")
            with open(error_zip_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=error_zip_name,
                    caption="<tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> 未找到session文件",
                    parse_mode=ParseMode.HTML
                )
            return

        status_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>Session转Tdata进行中</b>

找到 <b>{len(session_files)}</b> 个session文件
正在转换，请稍候...""",
            parse_mode=ParseMode.HTML
        )

        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        success_items = []
        failed_items = []

        for i, session_file in enumerate(session_files, 1):
            session_dir = os.path.dirname(session_file)
            twofa = read_2fa_from_folder(session_dir)

            success, result, account_dir = await convert_session_to_tdata(
                session_file, output_dir, twofa
            )

            if success:
                success_items.append((account_dir, result))
            else:
                failed_items.append((session_file, result))

            if i % 3 == 0 or i == len(session_files):
                try:
                    await status_msg.edit_text(
                        text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>Session转Tdata进行中</b>

进度: {i}/{len(session_files)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>成功: {len(success_items)}
<tg-emoji emoji-id="5922712343011135025">❌</tg-emoji>失败: {len(failed_items)}""",
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
            await asyncio.sleep(0.1)

        try:
            await status_msg.delete()
        except:
            pass

        if success_items:
            success_zip_name = f"session_to_tdata_success_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            success_zip_path = os.path.join(temp_dir, success_zip_name)
            with zipfile.ZipFile(success_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for account_dir, account_name in success_items:
                    for root, dirs, files in os.walk(account_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, output_dir)
                            zipf.write(file_path, arcname)

            with open(success_zip_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=success_zip_name,
                    caption=f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功转换 ({len(success_items)}个)""",
                    parse_mode=ParseMode.HTML
                )

        if failed_items:
            failed_zip_name = f"session_to_tdata_failed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            failed_zip_path = os.path.join(temp_dir, failed_zip_name)
            with zipfile.ZipFile(failed_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for idx, (orig_file, err_msg) in enumerate(failed_items, 1):
                    folder_name = f"failed_account_{idx}"
                    zipf.writestr(f"{folder_name}/error.txt", err_msg)
                    zipf.write(orig_file, f"{folder_name}/{os.path.basename(orig_file)}")
            with open(failed_zip_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=failed_zip_name,
                    caption=f"""<tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败账号 ({len(failed_items)}个)""",
                    parse_mode=ParseMode.HTML
                )


async def process_tdata_to_session(update: Update, context: ContextTypes.DEFAULT_TYPE, zip_path: str, user_id: str):
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                safe_extract(zip_ref, extract_dir)

                extracted_size = get_total_size(extract_dir)
                if extracted_size > MAX_EXTRACT_SIZE:
                    raise Exception(f"文件过大 ({extracted_size//1024//1024}MB > {MAX_EXTRACT_SIZE//1024//1024}MB)")
        except Exception as e:
            error_zip_name = f"tdata_to_session_error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            error_zip_path = os.path.join(temp_dir, error_zip_name)
            with zipfile.ZipFile(error_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.writestr("error.txt", f"解压失败: {str(e)}")
            with open(error_zip_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=error_zip_name,
                    caption="<tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> 处理失败，请检查压缩包",
                    parse_mode=ParseMode.HTML
                )
            return

        tdata_dirs = set()
        for root, dirs, files in os.walk(extract_dir):
            if os.path.basename(root) == 'tdata':
                if any(f in files for f in ['key_datas', 'map']):
                    tdata_dirs.add(root)
            elif 'tdata' in dirs:
                potential_tdata = os.path.join(root, 'tdata')
                if os.path.exists(potential_tdata):
                    sub_files = os.listdir(potential_tdata)
                    if any(f in sub_files for f in ['key_datas', 'map']):
                        tdata_dirs.add(potential_tdata)

        tdata_dirs = list(tdata_dirs)

        if not tdata_dirs:
            error_zip_name = f"tdata_to_session_error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            error_zip_path = os.path.join(temp_dir, error_zip_name)
            with zipfile.ZipFile(error_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.writestr("error.txt", "未找到有效的 tdata 文件夹")
            with open(error_zip_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=error_zip_name,
                    caption="<tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> 未找到tdata文件夹",
                    parse_mode=ParseMode.HTML
                )
            return

        status_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>Tdata转Session进行中</b>

找到 <b>{len(tdata_dirs)}</b> 个tdata文件夹
正在转换，请稍候...""",
            parse_mode=ParseMode.HTML
        )

        output_dir = os.path.join(temp_dir, "output_success")
        os.makedirs(output_dir, exist_ok=True)

        success_items = []
        failed_items = []

        for i, tdata_dir in enumerate(tdata_dirs, 1):
            parent_dir = os.path.dirname(tdata_dir)
            twofa = read_2fa_from_folder(parent_dir)

            account_output = os.path.join(output_dir, f"temp_{i}")
            os.makedirs(account_output, exist_ok=True)

            success, result, _ = await convert_tdata_to_session(
                tdata_dir, account_output, twofa
            )

            if success:
                session_file = None
                json_file = None
                for f in os.listdir(account_output):
                    if f.endswith('.session'):
                        session_file = os.path.join(account_output, f)
                    elif f.endswith('.json'):
                        json_file = os.path.join(account_output, f)
                if session_file and json_file:
                    phone_name = os.path.splitext(os.path.basename(session_file))[0]
                    success_items.append((session_file, json_file, phone_name))
                else:
                    failed_items.append((tdata_dir, f"转换成功但输出文件不完整"))
            else:
                failed_items.append((tdata_dir, result))

            if i % 3 == 0 or i == len(tdata_dirs):
                try:
                    await status_msg.edit_text(
                        text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>Tdata转Session进行中</b>

进度: {i}/{len(tdata_dirs)}
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>成功: {len(success_items)}
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji>失败: {len(failed_items)}""",
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
            await asyncio.sleep(0.1)

        try:
            await status_msg.delete()
        except:
            pass

        if success_items:
            success_zip_name = f"tdata_to_session_success_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            success_zip_path = os.path.join(temp_dir, success_zip_name)
            with zipfile.ZipFile(success_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for sess_file, json_file, phone_name in success_items:
                    zipf.write(sess_file, f"{phone_name}.session")
                    zipf.write(json_file, f"{phone_name}.json")

            with open(success_zip_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=success_zip_name,
                    caption=f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>  成功转换 ({len(success_items)}个)""",
                    parse_mode=ParseMode.HTML
                )

        if failed_items:
            failed_zip_name = f"tdata_to_session_failed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            failed_zip_path = os.path.join(temp_dir, failed_zip_name)
            with zipfile.ZipFile(failed_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for idx, (tdata_dir, err_msg) in enumerate(failed_items, 1):
                    folder_name = f"failed_account_{idx}"
                    zipf.writestr(f"{folder_name}/error.txt", err_msg)
                    for root, dirs, files in os.walk(tdata_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.join(folder_name, "tdata", os.path.relpath(file_path, tdata_dir))
                            zipf.write(file_path, arcname)
            with open(failed_zip_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=failed_zip_name,
                    caption=f"""<tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败账号 ({len(failed_items)}个)""",
                    parse_mode=ParseMode.HTML
                )
