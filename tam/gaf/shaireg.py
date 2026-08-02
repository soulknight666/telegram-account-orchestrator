import os
import zipfile
import shutil
import asyncio
import tempfile
import json
import sqlite3
import logging
import time
from datetime import datetime
from telegram import InlineKeyboardMarkup
import aiohttp
import aiohttp.client_exceptions

logger = logging.getLogger(__name__)

MAX_EXTRACT_SIZE = 200 * 1024 * 1024
CONCURRENT_REQUESTS = 3
API_URL = "https://regtime.miha.uk/regtime"
UUID = "@MrMiHa"

def safe_extract(zip_ref, target_dir):
    for member in zip_ref.infolist():
        member_path = os.path.normpath(member.filename)
        if member_path.startswith(('..', '/', '\\')):
            raise Exception(f"非法路径: {member.filename}")
        zip_ref.extract(member, target_dir)

def get_total_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total

def get_dc_id_from_session(session_path):
    if not os.path.exists(session_path):
        return None
    try:
        conn = sqlite3.connect(session_path)
        c = conn.cursor()
        c.execute("SELECT dc_id FROM sessions LIMIT 1")
        row = c.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"读取 session dc_id 失败 {session_path}: {e}")
        return None

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

async def fetch_regtime(session, user_id, dc_id):
    payload = {
        "uuid": UUID,
        "user_id": user_id,
        "dc_id": dc_id
    }
    try:
        async with session.post(API_URL, json=payload, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("date")
            else:
                logger.error(f"请求失败 status {resp.status}: {await resp.text()}")
                return None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.error(f"请求异常 user_id={user_id}: {e}")
        return None

async def handle_regtime_document(update, context, user_id, back_markup):
    document = update.message.document
    if not document.file_name.endswith('.zip'):
        await update.message.reply_text(
            "<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 请上传 ZIP 格式的压缩包",
            parse_mode='HTML',
            reply_markup=back_markup
        )
        return

    status_msg = await update.message.reply_text(
        "<tg-emoji emoji-id='5942826671290715541'>📥</tg-emoji> 正在下载文件...",
        parse_mode='HTML'
    )

    try:
        file = await context.bot.get_file(document.file_id)
        zip_path = f"downloads/regtime_{user_id}_{int(asyncio.get_event_loop().time())}.zip"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(zip_path)

        await status_msg.edit_text(
            "<tg-emoji emoji-id='5942826671290715541'>🔍</tg-emoji> 开始处理注册时间任务...",
            parse_mode='HTML'
        )

        result_text, result_zip = await process_regtime(zip_path, update, context)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=result_text,
            parse_mode='HTML'
        )

        if result_zip and os.path.exists(result_zip):
            with open(result_zip, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=os.path.basename(result_zip),
                    caption="""<b><tg-emoji emoji-id="5877332341331857066">📁</tg-emoji> 按注册时间分类的结果</b>""",
                    parse_mode='HTML'
                )
            os.remove(result_zip)

        os.remove(zip_path)

    except Exception as e:
        logger.error(f"处理注册时间失败: {e}")
        await update.message.reply_text(
            f"<tg-emoji emoji-id='5886496611835581345'>❌</tg-emoji> 处理失败: {str(e)}",
            parse_mode='HTML',
            reply_markup=back_markup
        )
    finally:
        await status_msg.delete()

async def process_regtime(zip_path, update, context):
    temp_dir = tempfile.mkdtemp(prefix="regtime_")
    try:
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zf:
            safe_extract(zf, extract_dir)
            if get_total_size(extract_dir) > MAX_EXTRACT_SIZE:
                raise Exception("解压后文件过大")

        json_files = []
        for root, _, files in os.walk(extract_dir):
            for f in files:
                if f.endswith('.json'):
                    json_files.append(os.path.join(root, f))

        if not json_files:
            raise Exception("未找到任何 .json 文件")

        account_infos = []

        for json_path in json_files:
            try:
                with open(json_path, 'r', encoding='utf-8') as jf:
                    data = json.load(jf)
                phone = data.get('phone')
                if not phone:
                    phone = os.path.splitext(os.path.basename(json_path))[0]
                user_id = data.get('user_id')
                if not user_id:
                    logger.warning(f"JSON {json_path} 缺少 user_id，跳过")
                    continue

                base = os.path.splitext(json_path)[0]
                session_path = base + '.session'
                if not os.path.exists(session_path):
                    session_path = None

                dc_id = None
                if session_path:
                    dc_id = get_dc_id_from_session(session_path)
                if dc_id is None:
                    dc_id = data.get('dc_id')
                if dc_id is None:
                    logger.warning(f"无法获取 dc_id for {phone}，跳过")
                    continue

                tdata_dir = None
                dir_path = os.path.dirname(json_path)
                for tdata in find_tdata_folders(dir_path):
                    if os.path.dirname(tdata) == dir_path or tdata == os.path.join(dir_path, 'tdata'):
                        tdata_dir = tdata
                        break

                account_infos.append((json_path, str(phone), str(user_id), str(dc_id) if dc_id is not None else None, session_path, tdata_dir))
            except Exception as e:
                logger.error(f"处理 JSON {json_path} 失败: {e}")

        if not account_infos:
            raise Exception("没有可用的账号信息")

        sem = asyncio.Semaphore(CONCURRENT_REQUESTS)
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async def bounded_fetch(info):
                _, _, user_id, dc_id, _, _ = info
                async with sem:
                    date = await fetch_regtime(session, user_id, dc_id)
                    return date

            tasks = [bounded_fetch(info) for info in account_infos]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        date_map = {}
        success_count = 0
        fail_count = 0
        for info, res in zip(account_infos, results):
            json_path, phone, user_id, dc_id, session_path, tdata_dir = info
            phone = str(phone)
            if isinstance(res, Exception):
                logger.error(f"获取 {phone} 注册时间失败: {res}")
                fail_count += 1
                date = "unknown"
            else:
                date = str(res) if res is not None else "unknown"
                if date != "unknown":
                    success_count += 1
                else:
                    fail_count += 1

            if date not in date_map:
                date_map[date] = {}
            if phone not in date_map[date]:
                date_map[date][phone] = {
                    "json": json_path,
                    "session": session_path,
                    "tdata": tdata_dir
                }

        output_dir = os.path.join(temp_dir, "regtime_output")
        os.makedirs(output_dir, exist_ok=True)

        for date, phones in date_map.items():
            date_dir = os.path.join(output_dir, date)
            os.makedirs(date_dir, exist_ok=True)
            for phone, files in phones.items():
                phone_dir = os.path.join(date_dir, phone)
                os.makedirs(phone_dir, exist_ok=True)
                if os.path.exists(files["json"]):
                    shutil.copy2(files["json"], os.path.join(phone_dir, os.path.basename(files["json"])))
                if files["session"] and os.path.exists(files["session"]):
                    shutil.copy2(files["session"], os.path.join(phone_dir, os.path.basename(files["session"])))
                if files["tdata"] and os.path.exists(files["tdata"]):
                    target_tdata = os.path.join(phone_dir, "tdata")
                    if os.path.exists(target_tdata):
                        shutil.rmtree(target_tdata)
                    shutil.copytree(files["tdata"], target_tdata)

        result_zip = os.path.join(temp_dir, "regtime_result.zip")
        with zipfile.ZipFile(result_zip, 'w') as zf:
            for root, _, files in os.walk(output_dir):
                for f in files:
                    file_path = os.path.join(root, f)
                    arcname = os.path.relpath(file_path, output_dir)
                    zf.write(file_path, arcname)

        final_zip = os.path.join("downloads", f"regtime_result_{int(time.time())}.zip")
        shutil.move(result_zip, final_zip)
        result_zip = final_zip

        total = len(account_infos)
        result_text = f"""<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> <b>注册时间筛选完成</b>

<tg-emoji emoji-id="5879770735999717115">👤</tg-emoji> 总账号: <b>{total}</b>
<tg-emoji emoji-id="5920052658743283381">✅</tg-emoji> 成功: <b>{success_count}</b>
<tg-emoji emoji-id="5922712343011135025">❌</tg-emoji> 失败: <b>{fail_count}</b>
<tg-emoji emoji-id="5879770735999717115">📁</tg-emoji> 日期分类: <b>{len(date_map)}</b> 个文件夹"""

        return result_text, result_zip

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
