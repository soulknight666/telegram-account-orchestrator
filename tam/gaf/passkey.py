import os
import zipfile
import shutil
import asyncio
import tempfile
import time
import json
import base64
import hashlib
import cbor2
import sqlite3
from pathlib import Path
from telethon.errors import FloodWaitError
from datetime import datetime
import logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from dotenv import load_dotenv
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from telethon.errors import SessionPasswordNeededError
from telethon.errors.rpcbaseerrors import BadRequestError
from telethon import functions
from telethon.tl.types import (
    DataJSON, 
    InputPasskeyCredentialPublicKey, 
    InputPasskeyResponseRegister,
    InputPasskeyResponseLogin
)
from opentele.tl import TelegramClient
from opentele.api import API
from opentele.td import TDesktop
import random
logger = logging.getLogger(__name__)
load_dotenv()

PASSKEY_BACK = os.getenv("PASSKEY_BACK", "🔑 <b>Passkey 功能管理</b>\n\n请选择您要执行的操作：").replace('\\n', '\n')
user_passkey_states = {}

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

def get_random_proxy_dict():
    proxy_file = "proxy.txt"
    if not os.path.exists(proxy_file):
        return None
    try:
        import random
        valid_proxies = []
        current_time = time.time()
        with open(proxy_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split(':')
                if len(parts) >= 5:
                    ip, port, username, password, expire_ts = parts[:5]
                    try:
                        if current_time < int(expire_ts):
                            valid_proxies.append({
                                'proxy_type': 'http', 'addr': ip, 'port': int(port),
                                'username': username, 'password': password, 'rdns': True
                            })
                    except ValueError:
                        continue
        if valid_proxies:
            log_time(f"从 proxy.txt 加载了 {len(valid_proxies)} 个有效代理")
            return random.choice(valid_proxies)
    except Exception:
        pass
    return None

def B64UrlEncode(Data: bytes) -> str: 
    return base64.urlsafe_b64encode(Data).decode("ascii").rstrip("=")

def B64UrlDecodeToLatin1(Text: str) -> str: 
    Pad = "=" * ((4 - len(Text) % 4) % 4)
    return base64.urlsafe_b64decode(Text + Pad).decode("latin1")

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

def safe_extract(zip_ref, target_dir):
    for member in zip_ref.infolist():
        member_path = os.path.normpath(member.filename)
        if member_path.startswith(('..', '/', '\\')):
            raise Exception(f"非法路径: {member.filename}")
        zip_ref.extract(member, target_dir)

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

async def generate_json_for_session(session_path, client, me, api_id, api_hash, official_api, twofa=None):
    session_path = Path(session_path)
    json_path = session_path.with_suffix('.json')
    phone = me.phone if me.phone else session_path.stem
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
        "twofa": twofa if twofa is not None else "",
        "password": twofa if twofa is not None else "",
        "app_id": api_id,
        "app_hash": api_hash,
        "session_file": session_path.stem,
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
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        return str(json_path)
    except Exception as e:
        logger.error(f"生成 JSON 失败 {session_path}: {e}")
        return None

async def create_single_passkey(session_file, json_file, out_dir, api_id, api_hash):
    start_time = time.time()
    log_time(f"开始创建 Passkey: {os.path.basename(session_file)}")
    
    temp_dir = tempfile.mkdtemp(prefix="passkey_temp_")
    temp_session = os.path.join(temp_dir, os.path.basename(session_file))
    try:
        shutil.copy2(session_file, temp_session)
        log_time(f"已创建临时 session 副本: {temp_session}")
        use_session = temp_session
    except Exception as e:
        log_time(f"复制 session 到临时目录失败: {e}，将使用原文件")
        use_session = str(session_file)
        temp_dir = None
    
    client = None
    session_path_str = use_session
    retry_count = 0
    while retry_count < 2:
        try:
            json_config = {}
            if json_file and os.path.exists(json_file):
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        json_config = json.load(f)
                except:
                    pass
            final_api_id = int(json_config.get('app_id', json_config.get('api_id', api_id)))
            final_api_hash = str(json_config.get('app_hash', json_config.get('api_hash', api_hash)))
            keys_to_check = ['twofa', '2fa', 'password', '2FA', 'twoFA', 'Password']
            twofa_pwd = ""
            for key in keys_to_check:
                val = json_config.get(key)
                if val:
                    twofa_pwd = str(val)
                    break
            official_api = API.TelegramDesktop.Generate()
            official_api.api_id = final_api_id
            official_api.api_hash = final_api_hash
            if json_config.get('device_model'):
                official_api.device_model = json_config['device_model']
            proxy = get_random_proxy_dict()
            client = TelegramClient(session_path_str, api=official_api, proxy=proxy, receive_updates=False, timeout=10, connection_retries=1)
            break
        except ValueError as e:
            err_msg = str(e)
            if ("not enough values to unpack (expected 6, got 5)" in err_msg or
                "too many values to unpack (expected 6)" in err_msg) and retry_count == 0:
                logger.warning(f"检测到 session 文件格式问题: {session_path_str}，尝试自动修复")
                if repair_session(session_path_str):
                    logger.info(f"修复完成，重试创建客户端")
                    retry_count += 1
                    continue
                else:
                    logger.error(f"自动修复失败，无法使用该 session: {session_path_str}")
                    return False, "Session文件损坏且修复失败"
            else:
                return False, f"创建客户端失败: {err_msg[:30]}"
        except Exception as ex:
            return False, f"创建客户端异常: {str(ex)[:30]}"
    try:
        connect_start = time.time()
        await asyncio.wait_for(client.connect(), timeout=15)
        log_time(f"连接耗时: {time.time() - connect_start:.2f}秒")
        auth_start = time.time()
        if not await asyncio.wait_for(client.is_user_authorized(), timeout=10):
            return False, "会话未授权/已掉线"
        log_time(f"授权检查耗时: {time.time() - auth_start:.2f}秒")
        me_start = time.time()
        Me = await asyncio.wait_for(client.get_me(), timeout=10)
        log_time(f"获取用户信息耗时: {time.time() - me_start:.2f}秒")
        Result = await client(functions.account.InitPasskeyRegistrationRequest())
        OptionsJson = json.loads(Result.options.data)
        PublicKey = OptionsJson.get("publicKey", {})
        ChallengeB64 = PublicKey["challenge"]
        RpId = PublicKey["rp"]["id"]
        PrivateKey = ec.generate_private_key(ec.SECP256R1())
        PublicKeyObj = PrivateKey.public_key()
        Pn = PublicKeyObj.public_numbers()
        PrivateKeyHex = PrivateKey.private_numbers().private_value.to_bytes(32, "big").hex()
        CoseKey = {1: 2, 3: -7, -1: 1, -2: Pn.x.to_bytes(32, "big"), -3: Pn.y.to_bytes(32, "big")}
        CoseKeyCbor = cbor2.dumps(CoseKey)
        RpIdHash = hashlib.sha256(RpId.encode("utf-8")).digest()
        Flags = b"\x45"
        SignCount = b"\x00\x00\x00\x00"
        Aaguid = b"\x00" * 16
        CredentialId = os.urandom(32)
        CredIdLen = len(CredentialId).to_bytes(2, "big")
        AuthData = RpIdHash + Flags + SignCount + Aaguid + CredIdLen + CredentialId + CoseKeyCbor
        ClientData = {"type": "webauthn.create", "challenge": ChallengeB64, "origin": "https://web.telegram.org", "crossOrigin": False}
        AttObj = {"fmt": "none", "attStmt": {}, "authData": AuthData}
        RegisterResponse = InputPasskeyResponseRegister(
            client_data=DataJSON(data=json.dumps(ClientData, separators=(",", ":"))), 
            attestation_data=cbor2.dumps(AttObj)
        )
        CredIdB64Url = B64UrlEncode(CredentialId)
        Credential = InputPasskeyCredentialPublicKey(id=CredIdB64Url, raw_id=CredIdB64Url, response=RegisterResponse)
        await client(functions.account.RegisterPasskeyRequest(credential=Credential))
        UserHandleB64 = ((PublicKey.get("user") or {}).get("id") if isinstance(PublicKey, dict) else None)
        UserHandlePlain = B64UrlDecodeToLatin1(UserHandleB64) if UserHandleB64 else ""
        PasskeyPayload = {
            "Phone": Me.phone or Path(session_file).stem,
            "TwoFA": twofa_pwd,
            "CredentialId": CredIdB64Url,
            "PrivateKeyHex": PrivateKeyHex,
            "UserHandle": UserHandlePlain,
            "UserHandleEncoding": "plain",
            "SignCount": 0,
            "RpId": RpId,
            "Origin": "https://web.telegram.org",
            "SigFormat": "der"
        }
        out_file = out_dir / f"{Me.phone or Path(session_file).stem}.Passkey"
        out_file.write_text(json.dumps(PasskeyPayload, ensure_ascii=False, indent=2), encoding="utf-8")
        total_time = time.time() - start_time
        log_time(f"账号 {os.path.basename(session_file)} Passkey创建成功，总耗时={total_time:.2f}秒")
        return True, "成功"
    except asyncio.TimeoutError:
        log_time(f"账号 {os.path.basename(session_file)} 网络操作超时")
        return False, "网络操作超时"
    except FloodWaitError as e:
        return False, f"频繁限制: {e.seconds}秒"
    except Exception as e:
        return False, f"错误: {str(e)[:20]}"
    finally:
        if client:
            disconnect_start = time.time()
            await client.disconnect()
            log_time(f"断开连接耗时: {time.time() - disconnect_start:.2f}秒")
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            log_time(f"已清理临时目录: {temp_dir}")

async def login_single_passkey(passkey_file, out_dir, api_id, api_hash):
    start_time = time.time()
    log_time(f"开始 Passkey 登录: {os.path.basename(passkey_file)}")
    client = None
    try:
        raw_data = passkey_file.read_text(encoding="utf-8")
        PasskeyData = json.loads(raw_data)
        phone = PasskeyData.get("Phone", passkey_file.stem)
        SessionPath = out_dir / f"{phone}.session"
        CredentialId = PasskeyData["CredentialId"]
        PrivateKeyHex = PasskeyData["PrivateKeyHex"]
        RpId = PasskeyData["RpId"]
        Origin = PasskeyData["Origin"]
        UserHandle = PasskeyData["UserHandle"]
        SignCount = int(PasskeyData.get("SignCount", 0))
        Flags = bytes.fromhex(str(PasskeyData.get("LoginFlagsHex", "01")))
        RpIdHash = hashlib.sha256(RpId.encode("utf-8")).digest()
        PrivateKey = ec.derive_private_key(int(PrivateKeyHex, 16), ec.SECP256R1())
        official_api = API.TelegramDesktop.Generate()
        official_api.api_id = api_id
        official_api.api_hash = api_hash
        proxy = get_random_proxy_dict()
        client = TelegramClient(str(SessionPath), api=official_api, proxy=proxy, receive_updates=False, timeout=10, connection_retries=1)
        connect_start = time.time()
        await asyncio.wait_for(client.connect(), timeout=15)
        log_time(f"连接耗时: {time.time() - connect_start:.2f}秒")
        MaxAttempts = 3
        CurrentSignCount = SignCount + 1
        LoginOk = False
        for Attempt in range(1, MaxAttempts + 1):
            try:
                Options = await client(functions.auth.InitPasskeyLoginRequest(api_id, api_hash))
                OptionsJson = json.loads(Options.options.data)
                Challenge = OptionsJson.get("publicKey", {}).get("challenge")
                AuthenticatorData = RpIdHash + Flags + CurrentSignCount.to_bytes(4, "big", signed=False)
                ClientDataText = json.dumps({"type": "webauthn.get", "challenge": Challenge, "origin": Origin, "crossOrigin": False}, separators=(",", ":"))
                ClientDataHash = hashlib.sha256(ClientDataText.encode("utf-8")).digest()
                Signature = PrivateKey.sign(AuthenticatorData + ClientDataHash, ec.ECDSA(hashes.SHA256()))
                Response = InputPasskeyResponseLogin(
                    client_data=DataJSON(data=ClientDataText), 
                    authenticator_data=AuthenticatorData, 
                    signature=Signature, 
                    user_handle=UserHandle
                )
                Credential = InputPasskeyCredentialPublicKey(id=CredentialId, raw_id=CredentialId, response=Response)
                await client(functions.auth.FinishPasskeyLoginRequest(credential=Credential))
                LoginOk = True
                break
            except SessionPasswordNeededError:
                TwoFa = PasskeyData.get("TwoFA")
                if TwoFa:
                    await client.sign_in(password=TwoFa)
                    LoginOk = True
                else:
                    return False, "需提供2FA密码"
                break
            except BadRequestError as E:
                if "PASSKEY_CHALLENGE_EXPIRED" in str(E) and Attempt < MaxAttempts:
                    CurrentSignCount += 1
                    await asyncio.sleep(0.15)
                    continue
                raise E
        if not LoginOk:
            return False, "登录失败"
        me_start = time.time()
        Me = await asyncio.wait_for(client.get_me(), timeout=10)
        log_time(f"获取用户信息耗时: {time.time() - me_start:.2f}秒")
        await generate_json_for_session(SessionPath, client, Me, api_id, api_hash, official_api, PasskeyData.get("TwoFA"))
        total_time = time.time() - start_time
        log_time(f"Passkey 登录成功: {phone}，总耗时={total_time:.2f}秒")
        return True, "成功"
    except asyncio.TimeoutError:
        log_time(f"Passkey 登录 {os.path.basename(passkey_file)} 网络操作超时")
        return False, "网络操作超时"
    except Exception as e:
        return False, f"错误: {str(e)[:20]}"
    finally:
        if client:
            disconnect_start = time.time()
            await client.disconnect()
            log_time(f"断开连接耗时: {time.time() - disconnect_start:.2f}秒")

async def show_passkey_menu(update, context):
    from bot import create_back_button
    keyboard = [
        [
            InlineKeyboardButton(
                text="创建Passkey", 
                callback_data="passkey_create",
                icon_custom_emoji_id="6005570495603282482",
                style="primary"
            ),
            InlineKeyboardButton(
                text="Passkey登录", 
                callback_data="passkey_login",
                icon_custom_emoji_id="6019523512908124649",
                style="success"
            )
        ],
        [create_back_button()]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        text=PASSKEY_BACK,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def handle_passkey_selection(update, context):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    from bot import create_back_button
    if data == "passkey_create":
        text = "<tg-emoji emoji-id='6005570495603282482'>🔑</tg-emoji> <b>创建 Passkey</b>\n\n请上传包含 <code>.session</code> 或 <code>tdata</code> 的 ZIP 压缩包，将为您导出 <code>.Passkey</code> 凭据文件。"
        user_passkey_states[user_id] = {"state": "create", "waiting_zip": True}
    else:
        text = "<tg-emoji emoji-id='6019523512908124649'>📱</tg-emoji> <b>Passkey 登录做号</b>\n\n请上传包含 <code>.Passkey</code> 凭据文件的 ZIP 压缩包，将为您自动登录并生成 <code>.session</code> 和 <code>.json</code>。"
        user_passkey_states[user_id] = {"state": "login", "waiting_zip": True}
    keyboard = [[create_back_button()]]
    await query.edit_message_text(
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_passkey_document(update, context, user_id):
    document = update.message.document
    from bot import create_back_button
    if not document.file_name.lower().endswith('.zip'):
        keyboard = [[create_back_button()]]
        await update.message.reply_text(
            "<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 请上传 ZIP 格式的压缩包",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    mode = user_passkey_states[user_id].get("state")
    status_msg = await update.message.reply_text("<tg-emoji emoji-id='5942826671290715541'>📥</tg-emoji> 正在下载文件...", parse_mode='HTML')
    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/passkey_{mode}_{user_id}_{int(time.time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)
        user_passkey_states.pop(user_id, None)
        if mode == "create":
            await process_passkey_create(update, context, zip_path, user_id, status_msg)
        else:
            await process_passkey_login(update, context, zip_path, user_id, status_msg)
        try:
            os.remove(zip_path)
        except:
            pass
    except Exception as e:
        logger.error(f"Passkey 文件处理失败: {e}")
        keyboard = [[create_back_button()]]
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 处理失败: {str(e)}",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    finally:
        try:
            await status_msg.delete()
        except:
            pass

async def process_passkey_create(update, context, zip_path, user_id, status_msg):
    api_id = int(os.getenv("TELEGRAM_APP_ID", "2040"))
    api_hash = os.getenv("TELEGRAM_APP_HASH", "b18441a1ff607e10a989891a5462e627")
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = Path(temp_dir) / "extracted"
        out_dir = Path(temp_dir) / "output"
        os.makedirs(extract_dir, exist_ok=True)
        os.makedirs(out_dir, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                safe_extract(zip_ref, extract_dir)
        except Exception as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 解压失败: {str(e)}", parse_mode='HTML')
            return
        session_files = list(extract_dir.rglob("*.session"))
        accounts = []
        if session_files:
            for sess in session_files:
                json_file = sess.with_suffix(".json")
                if not json_file.exists():
                    json_file = None
                accounts.append((sess, json_file))
        else:
            tdata_dirs = find_tdata_folders(str(extract_dir))
            if not tdata_dirs:
                await context.bot.send_message(chat_id=update.effective_chat.id, text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 未找到session或tdata文件夹", parse_mode='HTML')
                return
            status_msg2 = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"<tg-emoji emoji-id='5942826671290715541'>🔄</tg-emoji> 检测到 {len(tdata_dirs)} 个tdata，正在转换...", parse_mode='HTML')
            convert_temp_dir = Path(temp_dir) / "converted_sessions"
            os.makedirs(convert_temp_dir, exist_ok=True)
            for i, tdata_dir in enumerate(tdata_dirs, 1):
                parent_dir = os.path.dirname(tdata_dir)
                twofa = read_2fa_from_folder(parent_dir)
                proxy = get_random_proxy_dict()
                account_out = convert_temp_dir / f"acc_{i}"
                os.makedirs(account_out, exist_ok=True)
                success, phone, sess_path, json_path, err = await convert_tdata_to_session_with_proxy(
                    tdata_dir, str(account_out), twofa, proxy
                )
                if success and sess_path and json_path:
                    accounts.append((Path(sess_path), Path(json_path)))
                else:
                    logger.error(f"转换失败 {tdata_dir}: {err}")
                if i % 3 == 0 or i == len(tdata_dirs):
                    try:
                        await status_msg2.edit_text(f"<tg-emoji emoji-id='5942826671290715541'>🔄</tg-emoji> 转换进度: {i}/{len(tdata_dirs)} 成功: {len(accounts)}", parse_mode='HTML')
                    except:
                        pass
                await asyncio.sleep(0.2)
            try:
                await status_msg2.delete()
            except:
                pass
            if not accounts:
                await context.bot.send_message(chat_id=update.effective_chat.id, text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 所有tdata转换失败", parse_mode='HTML')
                return
        success_count = 0
        fail_count = 0
        for idx, (session_file, json_file) in enumerate(accounts, 1):
            if idx % 3 == 0 or idx == len(accounts):
                try:
                    await status_msg.edit_text(
                        f"""<tg-emoji emoji-id="5942826671290715541">⚙️</tg-emoji> <b>正在创建 Passkey</b>\n\n进度: {idx}/{len(accounts)}\n<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: {success_count} | <tg-emoji emoji-id="5886496611835581345">❌</tg-emoji> 失败: {fail_count}""",
                        parse_mode='HTML'
                    )
                except:
                    pass
            is_ok, reason = await create_single_passkey(session_file, json_file, out_dir, api_id, api_hash)
            if is_ok:
                success_count += 1
            else:
                fail_count += 1
            await asyncio.sleep(0.1)
        if success_count > 0:
            result_zip = Path(temp_dir) / "Passkeys_Exported.zip"
            with zipfile.ZipFile(result_zip, 'w') as zipf:
                for f in out_dir.iterdir():
                    zipf.write(f, f.name)
            with open(result_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"Passkeys_{int(time.time())}.zip",
                    caption=f"<tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> <b>创建完成！</b>\n成功提取: {success_count} 个",
                    parse_mode='HTML'
                )
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 全部创建失败", parse_mode='HTML')

async def process_passkey_login(update, context, zip_path, user_id, status_msg):
    api_id = int(os.getenv("TELEGRAM_APP_ID", "2040"))
    api_hash = os.getenv("TELEGRAM_APP_HASH", "b18441a1ff607e10a989891a5462e627")
    with tempfile.TemporaryDirectory() as temp_dir:
        extract_dir = Path(temp_dir) / "extracted"
        out_dir = Path(temp_dir) / "output"
        os.makedirs(extract_dir, exist_ok=True)
        os.makedirs(out_dir, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                safe_extract(zip_ref, extract_dir)
        except Exception as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 解压失败: {str(e)}", parse_mode='HTML')
            return
        passkey_files = list(extract_dir.rglob("*.Passkey"))
        if not passkey_files:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 未找到 .Passkey 凭据文件", parse_mode='HTML')
            return
        success_count = 0
        fail_count = 0
        for i, pk_file in enumerate(passkey_files, 1):
            if i % 3 == 0 or i == len(passkey_files):
                try:
                    await status_msg.edit_text(
                        f"""<tg-emoji emoji-id="5942826671290715541">⚙️</tg-emoji> <b>正在通过 Passkey 登录</b>\n\n进度: {i}/{len(passkey_files)}\n<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: {success_count} | <tg-emoji emoji-id="5886496611835581345">❌</tg-emoji> 失败: {fail_count}""",
                        parse_mode='HTML'
                    )
                except:
                    pass
            is_ok, reason = await login_single_passkey(pk_file, out_dir, api_id, api_hash)
            if is_ok:
                success_count += 1
            else:
                fail_count += 1
            await asyncio.sleep(0.1)
        if success_count > 0:
            result_zip = Path(temp_dir) / "Passkey_Sessions.zip"
            with zipfile.ZipFile(result_zip, 'w') as zipf:
                for f in out_dir.iterdir():
                    zipf.write(f, f.name)
            with open(result_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"Passkey_Login_{int(time.time())}.zip",
                    caption=f"<tg-emoji emoji-id='5920052658743283381'>✅</tg-emoji> <b>做号登录完成！</b>\n成功生成: {success_count} 个会话",
                    parse_mode='HTML'
                )
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 全部登录失败", parse_mode='HTML')
