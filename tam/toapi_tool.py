"""网页端「转 API」：号包 ZIP → session + api.json + 取码链接清单。

能力对齐机器人侧 GAFBot zhuanapi（MIT，见 NOTICE.GAFBot），但不依赖
python-telegram-bot，也不写全局 acd 目录；结果落在临时 job 目录供下载。
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import string
import tempfile
import zipfile
from pathlib import Path
from typing import Any


class ToApiError(Exception):
    """转 API 失败（人话消息）。"""


def _safe_extract(zf: zipfile.ZipFile, target: Path) -> None:
    for member in zf.infolist():
        name = os.path.normpath(member.filename)
        if name.startswith(("..", "/", "\\")) or Path(name).is_absolute():
            raise ToApiError(f"非法路径：{member.filename}")
        dest = target / name
        if member.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)


def _generate_id(n: int = 10) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))


def sanitize_2fa(text: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    if len(text) > 64:
        raise ToApiError("2FA 密码过长（≤64）")
    return text


def clean_phone(phone: str) -> str:
    cleaned = re.sub(r"[^\d\+\(\)\s\-]", "", str(phone)).strip()
    return cleaned if cleaned else "unknown"


def find_tdata_folders(root: Path) -> list[Path]:
    found: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        p = Path(dirpath)
        if p.name == "tdata" and any(f in filenames for f in ("key_datas", "map")):
            found.add(p)
        elif "tdata" in dirnames:
            pot = p / "tdata"
            if pot.is_dir():
                sub = {x.name for x in pot.iterdir()}
                if "key_datas" in sub or "map" in sub:
                    found.add(pot)
    return sorted(found)


def _read_2fa_nearby(folder: Path) -> str | None:
    for base in (folder, folder.parent):
        for name in ("2fa.txt", "2fa", "password.txt", "2FA.txt"):
            f = base / name
            if f.is_file():
                try:
                    return f.read_text(encoding="utf-8", errors="replace").strip() or None
                except OSError:
                    pass
    return None


def _session_from_string(session_str: str, out_path: Path) -> None:
    from telethon.sessions import SQLiteSession, StringSession

    src = StringSession(session_str)
    if not getattr(src, "auth_key", None):
        raise ToApiError("StringSession 无效：没有 auth_key")
    dst = SQLiteSession(str(out_path))
    dst.set_dc(src.dc_id, src.server_address, src.port)
    dst.auth_key = src.auth_key
    dst.save()


def _tdata_to_session_files(
    tdata_dir: Path, out_dir: Path, passcode: str | None = None
) -> list[tuple[str, Path, dict]]:
    from .tdata_native import tdata_string_sessions

    sessions, report = tdata_string_sessions(tdata_dir, passcode)
    if not sessions:
        raise ToApiError(report.error or "tdata 中没有已登录账号")
    out: list[tuple[str, Path, dict]] = []
    parent = tdata_dir.parent.name or "tdata"
    for i, s in enumerate(sessions):
        stem = f"tdata_{parent}_{i}" if len(sessions) > 1 else f"tdata_{parent}"
        stem = re.sub(r"[^\w\-]+", "_", stem)[:40] or f"tdata_{i}"
        path = out_dir / f"{stem}.session"
        _session_from_string(s, path)
        meta: dict[str, Any] = {}
        if i < len(report.accounts):
            acc = report.accounts[i]
            meta["user_id"] = acc.user_id
            meta["dc_id"] = acc.main_dc
        out.append((stem, path, meta))
    return out


def _collect_accounts(
    extract_dir: Path, work: Path, tdata_passcode: str | None
) -> list[tuple[str, Path, Path | None]]:
    session_files: list[Path] = []
    json_files: dict[str, Path] = {}
    for dirpath, _, filenames in os.walk(extract_dir):
        for f in filenames:
            p = Path(dirpath) / f
            if f.endswith(".session"):
                session_files.append(p)
            elif f.endswith(".json"):
                json_files[Path(f).stem] = p

    if session_files:
        return [(s.stem, s, json_files.get(s.stem)) for s in session_files]

    tdata_dirs = find_tdata_folders(extract_dir)
    if not tdata_dirs:
        raise ToApiError("ZIP 里既没有 .session，也没有可识别的 tdata 目录")

    conv_dir = work / "converted"
    conv_dir.mkdir(parents=True, exist_ok=True)
    accounts: list[tuple[str, Path, Path | None]] = []
    errors: list[str] = []
    for td in tdata_dirs:
        try:
            for stem, path, meta in _tdata_to_session_files(td, conv_dir, tdata_passcode):
                j = None
                for cand in (td.parent / f"{stem}.json", td.parent / "account.json"):
                    if cand.is_file():
                        j = cand
                        break
                if meta and j is None:
                    jpath = conv_dir / f"{stem}.json"
                    jpath.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
                    j = jpath
                accounts.append((stem, path, j))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{td}: {exc}")
    if not accounts:
        raise ToApiError("tdata 转换失败：" + "；".join(errors[:5]))
    return accounts


def convert_zip(
    zip_path: str | Path,
    out_zip: str | Path,
    *,
    mode: str = "from_json",
    manual_2fa: str | None = None,
    api_base: str | None = None,
    default_api_id: int | None = None,
    default_api_hash: str | None = None,
    tdata_passcode: str | None = None,
) -> dict[str, Any]:
    mode = (mode or "from_json").strip().lower()
    if mode not in {"no_2fa", "manual", "from_json"}:
        raise ToApiError("mode 只能是 no_2fa / manual / from_json")
    if mode == "manual":
        if not (manual_2fa or "").strip():
            raise ToApiError("manual 模式必须提供 2FA 密码")
        manual_2fa = sanitize_2fa(manual_2fa.strip())

    api_id = int(default_api_id or os.getenv("TAM_API_ID") or os.getenv("TELEGRAM_APP_ID") or "2040")
    api_hash = (
        default_api_hash
        or os.getenv("TAM_API_HASH")
        or os.getenv("TELEGRAM_APP_HASH")
        or "b18441a1ff607e10a989891a5462e627"
    )
    base = (api_base or os.getenv("TAM_TOAPI_BASE") or os.getenv("DM") or "").rstrip("/")
    if not base:
        ip = os.getenv("SERVER_IP") or "127.0.0.1"
        port = os.getenv("API_PORT") or "5099"
        base = f"http://{ip}:{port}"

    zip_path = Path(zip_path)
    out_zip = Path(out_zip)
    with tempfile.TemporaryDirectory(prefix="tam_toapi_") as tmp:
        tmp_p = Path(tmp)
        extract_dir = tmp_p / "in"
        extract_dir.mkdir()
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                _safe_extract(zf, extract_dir)
        except ToApiError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ToApiError(f"解压失败：{exc}") from exc

        accounts = _collect_accounts(extract_dir, tmp_p, tdata_passcode)
        out_dir = tmp_p / "out"
        out_dir.mkdir()
        api_data: dict[str, Any] = {}
        lines: list[str] = []
        used: set[str] = set()
        ok = 0
        errors: list[dict[str, str]] = []

        for stem, sess_path, json_path in accounts:
            try:
                new_id = _generate_id()
                while new_id in used:
                    new_id = _generate_id()
                used.add(new_id)
                shutil.copy2(sess_path, out_dir / f"{new_id}.session")

                json_config: dict[str, Any] = {}
                if json_path and Path(json_path).is_file():
                    try:
                        json_config = json.loads(Path(json_path).read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001
                        json_config = {}

                _app_id = json_config.get("app_id") or json_config.get("api_id") or api_id
                try:
                    _app_id = int(_app_id)
                except (TypeError, ValueError):
                    _app_id = api_id
                _app_hash = json_config.get("app_hash") or json_config.get("api_hash") or api_hash

                phone_number = "unknown"
                for field in ("phone", "number", "phone_number", "Phone", "账号", "电话号码", "手机号"):
                    val = json_config.get(field)
                    if val:
                        phone_number = str(val)
                        break
                if phone_number == "unknown":
                    phone_number = stem
                phone_number = clean_phone(phone_number)

                two_fa = None
                if mode == "manual":
                    two_fa = manual_2fa
                elif mode == "from_json":
                    two_fa = (
                        json_config.get("2fa")
                        or json_config.get("2FA")
                        or json_config.get("two_fa")
                        or json_config.get("password")
                        or json_config.get("twofa")
                    )
                    if not two_fa:
                        two_fa = _read_2fa_nearby(Path(sess_path).parent)
                    if two_fa:
                        two_fa = sanitize_2fa(str(two_fa))

                api_data[new_id] = {
                    "phone": phone_number,
                    "two_fa": two_fa or "",
                    "app_id": _app_id,
                    "app_hash": _app_hash,
                    "device_model": json_config.get("device_model"),
                    "app_version": json_config.get("app_version"),
                    "system_lang_code": json_config.get("system_lang_code"),
                    "system_vision": json_config.get("sdk") or json_config.get("system_version"),
                    "lang_pack": json_config.get("lang_pack"),
                    "source": stem,
                }
                line = f"{phone_number} --- {base}/getcode?id={new_id}"
                if two_fa:
                    line += f" (2FA: {two_fa})"
                lines.append(line)
                ok += 1
            except Exception as exc:  # noqa: BLE001
                errors.append({"source": stem, "error": str(exc)})

        if ok == 0:
            raise ToApiError(
                "没有成功转换任何账号：" + "; ".join(e["error"] for e in errors[:5])
            )

        (out_dir / "api.json").write_text(
            json.dumps(api_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (out_dir / "api_links.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

        out_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in out_dir.iterdir():
                zf.write(f, f.name)

        return {
            "ok": True,
            "total": ok,
            "failed": len(errors),
            "errors": errors[:20],
            "api_base": base,
            "mode": mode,
            "filename": out_zip.name,
        }
