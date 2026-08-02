import os
import zipfile
import shutil
import tempfile
import time
import json
import asyncio
import random
import struct
import re
import traceback
import sqlite3
from datetime import datetime
from telethon import TelegramClient as TelethonClient
from telethon.tl.functions import TLRequest
from telethon.tl.functions.contacts import GetContactsRequest, DeleteContactsRequest
from telethon.errors import FloodWaitError
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from dotenv import load_dotenv
from opentele.tl import TelegramClient
from opentele.api import API
from opentele.td import TDesktop
from telethon.tl.functions.contacts import BlockRequest
load_dotenv()
logger = logging.getLogger(__name__)

CLEAN_ACCOUNT_BACK = os.getenv("CLEAN_ACCOUNT_BACK", "").replace('\\n', '\n')
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

_proxy_list = None
_proxy_list_last_load = 0
PROXY_LIST_CACHE_TIME = 60

user_clean_states = {}

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

class GetPasskeysManual(TLRequest):
    CONSTRUCTOR_ID = 0xea1f0c52
    def __init__(self):
        super().__init__()
    def _bytes(self):
        return struct.pack('<I', self.CONSTRUCTOR_ID)
    async def resolve(self, client, utils):
        pass
    def __bytes__(self):
        return self._bytes()

class DeletePasskeyManual(TLRequest):
    CONSTRUCTOR_ID = 0xf5b5563f
    def __init__(self, pk_id):
        super().__init__()
        self.pk_id = pk_id
    def _bytes(self):
        res = struct.pack('<I', self.CONSTRUCTOR_ID)
        b_id = self.pk_id.encode('utf-8')
        L = len(b_id)
        if L <= 253:
            res += struct.pack('<B', L)
        else:
            res += b'\xfe' + struct.pack('<I', L)[:3]
        res += b_id
        while len(res) % 4 != 0:
            res += b'\x00'
        return res
    async def resolve(self, client, utils):
        pass
    def __bytes__(self):
        return self._bytes()

def parse_raw_passkeys(raw_bytes):
    matches = re.findall(b'[\x20-\x7e]{4,}', raw_bytes)
    results = []
    for m in matches:
        text = m.decode('utf-8', errors='ignore')
        if len(text) > 5 and not text.startswith('telethon'):
            results.append(text)
    passkeys = []
    for i in range(0, len(results)-1, 2):
        passkeys.append({'id': results[i], 'name': results[i+1]})
    return passkeys

async def delete_passkeys_for_client(client):
    deleted_count = 0
    errors = []
    try:
        client.session.layer = 188
        try:
            result = await client(GetPasskeysManual())
            if hasattr(result, 'passkeys'):
                passkeys = result.passkeys
                logger.info(f"成功获取到 {len(passkeys)} 个Passkey")
                for pk in passkeys:
                    pk_id = pk.id
                    pk_name = pk.name
                    logger.info(f"准备删除Passkey: ID={pk_id}, Name={pk_name}")
                    try:
                        await client(DeletePasskeyManual(pk_id))
                        deleted_count += 1
                        logger.info(f"成功删除Passkey: ID={pk_id}, Name={pk_name}")
                    except Exception as del_e:
                        error_msg = f"删除Passkey {pk_id} 失败: {str(del_e)}"
                        errors.append(error_msg)
                        logger.error(error_msg)
            else:
                logger.warning("返回结果中没有passkeys属性")
        except Exception as e:
            err_msg = str(e)
            if "Remaining bytes:" in err_msg:
                raw_data = eval(err_msg.split("Remaining bytes: ")[1])
                passkeys = parse_raw_passkeys(raw_data)
                logger.info(f"通过原始解析获取到 {len(passkeys)} 个Passkey")
                for pk in passkeys:
                    pk_id = pk['id']
                    pk_name = pk['name']
                    logger.info(f"准备删除Passkey: ID={pk_id}, Name={pk_name}")
                    try:
                        await client(DeletePasskeyManual(pk_id))
                        deleted_count += 1
                        logger.info(f"成功删除Passkey: ID={pk_id}, Name={pk_name}")
                    except Exception as del_e:
                        error_msg = f"删除Passkey {pk_id} 失败: {str(del_e)}"
                        errors.append(error_msg)
                        logger.error(error_msg)
            else:
                logger.error(f"获取Passkey列表失败: {err_msg}")
                errors.append(f"获取Passkey列表失败: {err_msg}")
    except Exception as e:
        logger.error(f"处理Passkey时出错: {e}")
        errors.append(f"处理Passkey时出错: {str(e)}")
    return deleted_count, errors

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
    if not proxy:
        return None
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

async def show_clean_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    keyboard = [
        [
            InlineKeyboardButton("删除所有对话", callback_data="clean_chats").to_dict() | {"icon_custom_emoji_id": "5877307202888273539"},
            InlineKeyboardButton("删除所有联系人", callback_data="clean_contacts").to_dict() | {"icon_custom_emoji_id": "5877318502947229960"}
        ],
        [
            InlineKeyboardButton("删除所有Passkey", callback_data="clean_passkeys").to_dict() | {"icon_custom_emoji_id": "5886505193180239900"}
        ],
        [
            InlineKeyboardButton("全部删除", callback_data="clean_all").to_dict() | {"icon_custom_emoji_id": "5922712343011135025"}
        ],
        [create_back_button()]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=CLEAN_ACCOUNT_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    user_clean_states[user_id] = {"waiting_selection": True}

async def handle_clean_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    await query.answer()
    clean_type = {
        "clean_chats": "chats",
        "clean_contacts": "contacts",
        "clean_passkeys": "passkeys",
        "clean_all": "all"
    }.get(data)
    user_clean_states[user_id] = {
        "type": clean_type,
        "waiting_zip": True
    }
    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    type_names = {
        "chats": "删除所有对话",
        "contacts": "删除所有联系人",
        "passkeys": "删除所有Passkey",
        "all": "删除所有对话、联系人和Passkey"
    }
    await query.edit_message_text(
        text=f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 已选择: {type_names[clean_type]}

<tg-emoji emoji-id="5877540355187937244">📤</tg-emoji> 请上传包含session或tdata的ZIP文件""",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def handle_clean_document(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    document = update.message.document
    clean_info = user_clean_states.get(user_id, {})
    clean_type = clean_info.get("type")
    if not document.file_name.endswith('.zip'):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        user_clean_states.pop(user_id, None)
        return
    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/clean_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)
        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔍</tg-emoji> 开始处理清理任务...",
            parse_mode='HTML'
        )
        await process_clean(update, context, zip_path, user_id, clean_type)
        try:
            os.remove(zip_path)
        except:
            pass
    except Exception as e:
        logger.error(f"处理文件失败: {e}")
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 处理失败: {str(e)}",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    finally:
        user_clean_states.pop(user_id, None)
        try:
            await status_msg.delete()
        except:
            pass

def get_total_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total

async def clean_account_operations(client, clean_type):
    results = {
        "chats_deleted": 0,
        "contacts_deleted": 0,
        "passkeys_deleted": 0,
        "errors": []
    }
    
    if clean_type in ["chats", "all"]:
        try:
            dialogs = await client.get_dialogs()
            logger.info(f"获取到 {len(dialogs)} 个对话")
            for dialog in dialogs:
                try:
                    entity = dialog.entity
                    is_bot = False
                    bot_info = ""
                    if entity and hasattr(entity, 'bot') and entity.bot:
                        is_bot = True
                        bot_info = f" (机器人: {entity.username or entity.first_name or entity.id})"
                    
                    logger.info(f"正在删除对话: {dialog.name} (ID: {dialog.id}){bot_info}")
                    await client.delete_dialog(dialog.entity or dialog.id)
                    results["chats_deleted"] += 1
                    
                    if is_bot:
                        try:
                            await client(BlockRequest(entity.id))
                            logger.info(f"已屏蔽机器人: {dialog.name}")
                        except Exception as block_e:
                            logger.error(f"屏蔽机器人 {dialog.name} 失败: {block_e}")
                            results["errors"].append(f"屏蔽机器人失败: {str(block_e)[:100]}")
                    
                    await asyncio.sleep(0.1)
                except Exception as e:
                    err_detail = traceback.format_exc()
                    logger.error(f"删除对话失败: {e}\n{err_detail}")
                    results["errors"].append(f"删除对话失败: {str(e)[:100]}")
        except Exception as e:
            err_detail = traceback.format_exc()
            logger.error(f"获取对话列表失败: {e}\n{err_detail}")
            results["errors"].append(f"获取对话列表失败: {str(e)[:100]}")

    if clean_type in ["contacts", "all"]:
        try:
            contacts_result = await client(GetContactsRequest(hash=0))
            contact_entries = contacts_result.contacts
            logger.info(f"获取到 {len(contact_entries)} 个联系人（从 contacts 字段）")
            user_dict = {user.id: user for user in contacts_result.users}
            for contact_entry in contact_entries:
                user_id = contact_entry.user_id
                user_obj = user_dict.get(user_id)
                try:
                    if user_obj:
                        logger.info(f"正在删除联系人: id={user_id}, name={user_obj.first_name} {user_obj.last_name}")
                        await client(DeleteContactsRequest(id=[user_obj]))
                    else:
                        logger.info(f"正在删除联系人: id={user_id}（无完整用户信息）")
                        await client(DeleteContactsRequest(id=[user_id]))
                    results["contacts_deleted"] += 1
                    await asyncio.sleep(0.1)
                except FloodWaitError as flood:
                    wait_time = flood.seconds
                    logger.warning(f"触发 FloodWait，需等待 {wait_time} 秒")
                    await asyncio.sleep(wait_time)
                    try:
                        if user_obj:
                            await client(DeleteContactsRequest(id=[user_obj]))
                        else:
                            await client(DeleteContactsRequest(id=[user_id]))
                        results["contacts_deleted"] += 1
                        logger.info(f"重试后成功删除联系人 {user_id}")
                    except Exception as retry_e:
                        logger.error(f"重试后删除联系人 {user_id} 仍失败: {retry_e}")
                        results["errors"].append(f"删除联系人 {user_id} 失败: {str(retry_e)[:100]}")
                except Exception as e:
                    logger.error(f"删除联系人 {user_id} 失败: {e}\n{traceback.format_exc()}")
                    results["errors"].append(f"删除联系人 {user_id} 失败: {str(e)[:100]}")
        except Exception as e:
            logger.error(f"获取或删除联系人整体失败: {e}\n{traceback.format_exc()}")
            results["errors"].append(f"获取联系人列表失败: {str(e)[:100]}")

    if clean_type in ["passkeys", "all"]:
        deleted, errs = await delete_passkeys_for_client(client)
        results["passkeys_deleted"] = deleted
        results["errors"].extend(errs)

    return results

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

async def process_clean(update, context, zip_path, user_id, clean_type):
    api_id_str = os.getenv("TELEGRAM_APP_ID")
    api_hash = os.getenv("TELEGRAM_APP_HASH")
    admins = os.getenv("ADMIN_ID", "").split(",")
    if not api_id_str or not api_hash:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 系统未配置，请联系管理员",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return
    try:
        api_id = int(api_id_str)
    except (ValueError, TypeError):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> API配置错误，请联系管理员",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return
    try:
        await asyncio.wait_for(
            _process_clean_internal(update, context, zip_path, user_id, api_id, api_hash, admins, clean_type),
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

async def _process_clean_internal(update, context, zip_path, user_id, api_id, api_hash, admins, clean_type):
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                safe_extract(zip_ref, extract_dir)
                extracted_size = get_total_size(extract_dir)
                if extracted_size > MAX_EXTRACT_SIZE:
                    raise Exception(f"解压后文件过大 ({extracted_size//1024//1024}MB > {MAX_EXTRACT_SIZE//1024//1024}MB)")
        except Exception as e:
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 解压失败: {str(e)}",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            return

        session_files = []
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.endswith('.session'):
                    session_path = os.path.join(root, file)
                    session_files.append(session_path)

        accounts = []
        if session_files:
            for sess in session_files:
                session_name = os.path.splitext(os.path.basename(sess))[0]
                json_file = os.path.join(os.path.dirname(sess), f"{session_name}.json")
                if not os.path.exists(json_file):
                    json_file = None
                accounts.append((session_name, sess, json_file, None))
        else:
            tdata_dirs = find_tdata_folders(extract_dir)
            if not tdata_dirs:
                keyboard = [[create_back_button()]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 未找到session或tdata文件夹",
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                return
            
            status_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>检测到tdata，正在转换为session...</b>

找到 <b>{len(tdata_dirs)}</b> 个tdata文件夹
请稍候...""",
                parse_mode='HTML'
            )
            
            convert_temp_dir = os.path.join(temp_dir, "converted_sessions")
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

        type_names = {
            "chats": "删除所有对话",
            "contacts": "删除所有联系人",
            "passkeys": "删除所有Passkey",
            "all": "删除所有对话、联系人和Passkey"
        }

        status_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>清理账号进行中</b>

清理类型: {type_names[clean_type]}
找到 <b>{len(accounts)}</b> 个账号
<tg-emoji emoji-id="5775887550262546277">🔄</tg-emoji>正在处理，请稍候...""",
            parse_mode='HTML'
        )

        success_dir = os.path.join(temp_dir, "success")
        failed_dir = os.path.join(temp_dir, "failed")
        os.makedirs(success_dir, exist_ok=True)
        os.makedirs(failed_dir, exist_ok=True)

        success_count = 0
        failed_count = 0
        results = []

        for i, (phone, session_file, json_file, tdata_dir) in enumerate(accounts, 1):
            if i % 3 == 0 or i == len(accounts):
                try:
                    await status_msg.edit_text(
                        text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>清理账号进行中</b>

进度: {i}/{len(accounts)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>成功: {success_count} | <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji>失败: {failed_count}""",
                        parse_mode='HTML'
                    )
                except:
                    pass

            account_start = time.time()
            log_time(f"开始清理账号 {os.path.basename(session_file)}")

            temp_session_dir = tempfile.mkdtemp(prefix="qingli_temp_")
            temp_session = os.path.join(temp_session_dir, os.path.basename(session_file))
            try:
                shutil.copy2(session_file, temp_session)
                log_time(f"已创建临时 session 副本: {temp_session}")
                use_session = temp_session
            except Exception as e:
                log_time(f"复制 session 到临时目录失败: {e}，将使用原文件")
                use_session = session_file
                temp_session_dir = None

            client = None
            try:
                json_config = {}
                if json_file:
                    try:
                        with open(json_file, 'r', encoding='utf-8') as f:
                            json_config = json.load(f)
                    except Exception as e:
                        logger.warning(f"读取 JSON 配置失败 {json_file}: {e}")

                final_api_id = api_id
                final_api_hash = api_hash
                if json_config:
                    if 'app_id' in json_config and json_config['app_id']:
                        try:
                            final_api_id = int(json_config['app_id'])
                        except (ValueError, TypeError):
                            logger.warning(f"无效的 app_id: {json_config['app_id']}, 使用默认值")
                    if 'app_hash' in json_config and json_config['app_hash']:
                        final_api_hash = str(json_config['app_hash'])

                device_model = json_config.get('device_model') or None
                app_version = json_config.get('app_version') or None
                system_lang_code = json_config.get('system_lang_code') or None
                system_version = json_config.get('system_version') or json_config.get('sdk') or None
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

                        official_api.api_id = final_api_id
                        official_api.api_hash = final_api_hash
                        if app_version:
                            official_api.app_version = app_version
                        if system_lang_code:
                            official_api.system_lang_code = system_lang_code
                        if system_version:
                            official_api.system_version = system_version
                        if lang_pack:
                            official_api.lang_pack = lang_pack
                            official_api.lang_code = lang_pack

                        proxy = get_random_proxy()
                        proxy_dict = create_proxy_dict(proxy) if proxy else None

                        client = TelegramClient(
                            use_session,
                            api=official_api,
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
                            logger.warning(f"检测到 session 文件格式问题: {use_session}，尝试自动修复")
                            if repair_session(use_session):
                                logger.info(f"修复完成，重试创建客户端")
                                retry_count += 1
                                continue
                            else:
                                logger.error(f"自动修复失败，无法使用该 session: {use_session}")
                                raise Exception("Session文件损坏且修复失败")
                        else:
                            raise

                connect_start = time.time()
                await asyncio.wait_for(client.connect(), timeout=15)
                log_time(f"连接耗时: {time.time() - connect_start:.2f}秒")

                auth_start = time.time()
                if not await asyncio.wait_for(client.is_user_authorized(), timeout=10):
                    result = {"session": os.path.basename(session_file), "status": "failed", "message": "session无效"}
                    target_dir = failed_dir
                    failed_count += 1
                    logger.warning(f"账号 {os.path.basename(session_file)} 未授权")
                else:
                    me_start = time.time()
                    me = await asyncio.wait_for(client.get_me(), timeout=10)
                    if not me:
                        raise Exception("无法获取用户信息")
                    log_time(f"获取用户信息耗时: {time.time() - me_start:.2f}秒")
                    account_phone = me.phone if me else phone
                    logger.info(f"开始处理账号 {account_phone} ({os.path.basename(session_file)})")

                    if not json_file:
                        generated_json = await generate_json_for_session(
                            session_file, client, me, final_api_id, final_api_hash, official_api
                        )
                        if generated_json:
                            json_file = generated_json

                    clean_results = await clean_account_operations(client, clean_type)
                    result = {
                        "session": os.path.basename(session_file),
                        "phone": account_phone,
                        "status": "success",
                        "chats_deleted": clean_results["chats_deleted"],
                        "contacts_deleted": clean_results["contacts_deleted"],
                        "passkeys_deleted": clean_results["passkeys_deleted"],
                        "errors": clean_results["errors"]
                    }
                    target_dir = success_dir
                    success_count += 1
                    logger.info(f"账号 {account_phone} 清理完成: 对话={clean_results['chats_deleted']}, 联系人={clean_results['contacts_deleted']}, Passkey={clean_results['passkeys_deleted']}, 错误数={len(clean_results['errors'])}")

                results.append(result)
                
                account_folder_name = account_phone if result.get("phone") else phone
                account_folder = os.path.join(target_dir, account_folder_name)
                os.makedirs(account_folder, exist_ok=True)
                
                if tdata_dir and os.path.exists(tdata_dir):
                    tdata_target = os.path.join(account_folder, "tdata")
                    shutil.copytree(tdata_dir, tdata_target, dirs_exist_ok=True)
                
                if session_file and os.path.exists(session_file):
                    shutil.copy2(session_file, os.path.join(account_folder, os.path.basename(session_file)))
                if json_file and os.path.exists(json_file):
                    shutil.copy2(json_file, os.path.join(account_folder, os.path.basename(json_file)))
                
                account_elapsed = time.time() - account_start
                log_time(f"账号 {os.path.basename(session_file)} 清理完成，状态={result['status']}，耗时={account_elapsed:.2f}秒")
                
                await asyncio.sleep(0.1)

            except FloodWaitError as e:
                logger.warning(f"账号 {os.path.basename(session_file)} 触发 FloodWait，需等待 {e.seconds} 秒")
                result = {"session": os.path.basename(session_file), "status": "failed", "message": f"等待{e.seconds}秒"}
                results.append(result)
                failed_count += 1
            except asyncio.TimeoutError:
                logger.warning(f"账号 {os.path.basename(session_file)} 网络操作超时")
                result = {"session": os.path.basename(session_file), "status": "failed", "message": "网络操作超时"}
                results.append(result)
                failed_count += 1
            except Exception as e:
                err_detail = traceback.format_exc()
                logger.error(f"处理账号 {os.path.basename(session_file)} 时出错: {e}\n{err_detail}")
                result = {"session": os.path.basename(session_file), "status": "failed", "message": str(e)[:100]}
                results.append(result)
                failed_count += 1
            finally:
                if client:
                    disconnect_start = time.time()
                    await client.disconnect()
                    log_time(f"断开连接耗时: {time.time() - disconnect_start:.2f}秒")
                if temp_session_dir and os.path.exists(temp_session_dir):
                    shutil.rmtree(temp_session_dir, ignore_errors=True)
                    log_time(f"已清理临时目录: {temp_session_dir}")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        success_zip = os.path.join(temp_dir, "success.zip")
        if success_count > 0:
            with zipfile.ZipFile(success_zip, 'w') as zipf:
                for root, dirs, files in os.walk(success_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, success_dir)
                        zipf.write(file_path, arcname)

        failed_zip = os.path.join(temp_dir, "failed.zip")
        if failed_count > 0:
            with zipfile.ZipFile(failed_zip, 'w') as zipf:
                for root, dirs, files in os.walk(failed_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, failed_dir)
                        zipf.write(file_path, arcname)

        total_chats = sum(r.get("chats_deleted", 0) for r in results if r["status"] == "success")
        total_contacts = sum(r.get("contacts_deleted", 0) for r in results if r["status"] == "success")
        total_passkeys = sum(r.get("passkeys_deleted", 0) for r in results if r["status"] == "success")

        result_text = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>清理账号完成</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 统计结果:
• <tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 总账号: <b>{len(accounts)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: <b>{success_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: <b>{failed_count}</b>
• <tg-emoji emoji-id="5877307202888273539">💬</tg-emoji> 删除对话: <b>{total_chats}</b>
• <tg-emoji emoji-id="5877318502947229960">👥</tg-emoji> 删除联系人: <b>{total_contacts}</b>
• <tg-emoji emoji-id="5886505193180239900">🔑</tg-emoji> 删除Passkey: <b>{total_passkeys}</b>"""

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=result_text,
            parse_mode='HTML'
        )

        if success_count > 0:
            with open(success_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"clean_success_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 清理成功 ({success_count}个)</b>",
                    parse_mode='HTML'
                )

        if failed_count > 0:
            with open(failed_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"clean_failed_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> 清理失败 ({failed_count}个)</b>",
                    parse_mode='HTML'
                )

        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id:
                continue
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"""<tg-emoji emoji-id="5909201569898827582">📢</tg-emoji> <b>清理账号任务完成</b>

<tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 用户: <code>{user_id}</code>
清理类型: {type_names[clean_type]}
<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 总账号: <b>{len(accounts)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: <b>{success_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: <b>{failed_count}</b>
• <tg-emoji emoji-id="5877307202888273539">💬</tg-emoji> 删除对话: <b>{total_chats}</b>
• <tg-emoji emoji-id="5877318502947229960">👥</tg-emoji> 删除联系人: <b>{total_contacts}</b>
• <tg-emoji emoji-id="5886505193180239900">🔑</tg-emoji> 删除Passkey: <b>{total_passkeys}</b>""",
                    parse_mode='HTML'
                )
                admin_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                if success_count > 0:
                    with open(success_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"clean_success_{user_id}_{admin_timestamp}.zip"
                        )
                if failed_count > 0:
                    with open(failed_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"clean_failed_{user_id}_{admin_timestamp}.zip"
                        )
            except Exception as e:
                logger.error(f"发送给管理员 {admin_id} 失败: {e}")

        try:
            await status_msg.delete()
        except:
            pass
