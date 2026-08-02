"""号包格式互转：session ↔ tdata（对齐机器人 /convert，供网页 ZIP 工具调用）。"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any


class ConvertError(Exception):
    pass


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for m in zf.infolist():
        fn = m.filename.replace("\\", "/").lstrip("/")
        if not fn or fn.endswith("/") or ".." in fn.split("/"):
            continue
        target = (dest / fn).resolve()
        try:
            target.relative_to(dest.resolve())
        except ValueError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not m.is_dir():
            with zf.open(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _find_sessions(root: Path) -> list[Path]:
    return sorted({p for p in root.rglob("*.session") if p.is_file()})


def _find_tdata_dirs(root: Path) -> list[Path]:
    from .tdata import find_tdata_dirs, is_tdata_dir

    if is_tdata_dir(root):
        return [root]
    cand = root / "tdata"
    if is_tdata_dir(cand):
        return [cand]
    return find_tdata_dirs(root)


async def session_zip_to_tdata_zip(
    raw: bytes,
    *,
    api_id: int = 0,
    api_hash: str = "",
) -> tuple[bytes, dict[str, Any]]:
    """session 号包 → tdata 号包。需要 opentele。"""
    try:
        from opentele.api import API, UseCurrentSession
        from opentele.tl import TelegramClient
    except Exception as exc:  # noqa: BLE001
        raise ConvertError(
            "session→tdata 需要 opentele。请使用网页「安装 opentele」或: pip install opentele"
        ) from exc

    from telethon.sessions import StringSession

    from .manager import AccountManager

    items: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="tam_cvt_s2t_") as tmp:
        root = Path(tmp)
        src, out = root / "in", root / "out"
        src.mkdir()
        out.mkdir()
        zpath = root / "in.zip"
        zpath.write_bytes(raw)
        try:
            with zipfile.ZipFile(zpath, "r") as zf:
                _safe_extract(zf, src)
        except zipfile.BadZipFile as exc:
            raise ConvertError(f"不是有效 zip：{exc}") from exc

        sessions = _find_sessions(src)
        if not sessions:
            raise ConvertError("zip 里没有找到 .session 文件")

        for sp in sessions:
            stem = "".join(c for c in sp.stem if c.isalnum() or c in "-_") or "acc"
            sub = out / stem / "tdata"
            try:
                plain = AccountManager.session_file_to_string(str(sp))
            except Exception as exc:  # noqa: BLE001
                items.append({"ok": False, "file": sp.name, "error": f"读 session 失败：{exc}"})
                continue
            api = API.TelegramDesktop.Generate()
            if api_id:
                try:
                    api.api_id = int(api_id)
                except (TypeError, ValueError):
                    pass
            if api_hash:
                api.api_hash = api_hash
            client = TelegramClient(
                str(root / f"_c_{stem}"),
                api=api,
                proxy=None,
                receive_updates=False,
            )
            client.session = StringSession(plain)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    items.append({"ok": False, "file": sp.name, "error": "会话未授权"})
                    continue
                tdesk = await client.ToTDesktop(flag=UseCurrentSession)
                sub.parent.mkdir(parents=True, exist_ok=True)
                tdesk.SaveTData(str(sub))
                items.append({"ok": True, "file": sp.name, "out": f"{stem}/tdata"})
            except Exception as exc:  # noqa: BLE001
                items.append({"ok": False, "file": sp.name, "error": str(exc)[:300]})
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

        ok_n = sum(1 for x in items if x.get("ok"))
        if ok_n == 0:
            raise ConvertError(
                "没有成功转换任何 session："
                + "; ".join(f"{x.get('file')}: {x.get('error')}" for x in items[:5])
            )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in out.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(out).as_posix())
            zf.writestr(
                "_convert_report.json",
                json.dumps({"mode": "session_to_tdata", "items": items},
                           ensure_ascii=False, indent=2),
            )
        return buf.getvalue(), {
            "ok": True, "total": len(items), "succeeded": ok_n,
            "failed": len(items) - ok_n, "items": items,
        }


async def tdata_zip_to_session_zip(
    raw: bytes,
    *,
    password: str | None = None,
    api_id: int = 0,
    api_hash: str = "",
) -> tuple[bytes, dict[str, Any]]:
    """tdata 号包 → StringSession 文本 + json。内置解析，无需 opentele。"""
    from .tdata import tdata_to_sessions

    items: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="tam_cvt_t2s_") as tmp:
        root = Path(tmp)
        src, out = root / "in", root / "out"
        src.mkdir()
        out.mkdir()
        zpath = root / "in.zip"
        zpath.write_bytes(raw)
        try:
            with zipfile.ZipFile(zpath, "r") as zf:
                _safe_extract(zf, src)
        except zipfile.BadZipFile as exc:
            raise ConvertError(f"不是有效 zip：{exc}") from exc

        dirs = _find_tdata_dirs(src)
        if not dirs:
            raise ConvertError("zip 里没有找到 tdata 目录（需含 key_datas）")

        for d in dirs:
            try:
                sessions = await tdata_to_sessions(
                    str(d),
                    api_id=api_id or 0,
                    api_hash=api_hash or "",
                    password=password,
                    use_desktop_api=True,
                    debug=False,
                )
            except Exception as exc:  # noqa: BLE001
                items.append({"ok": False, "path": d.name, "error": str(exc)[:300]})
                continue
            for i, sess in enumerate(sessions, 1):
                stem = d.name if len(sessions) == 1 else f"{d.name}-{i}"
                stem = "".join(c for c in stem if c.isalnum() or c in "-_") or f"acc{i}"
                (out / f"{stem}.session.txt").write_text(sess, encoding="utf-8")
                meta = {
                    "session_string": sess,
                    "source_tdata": d.name,
                    "index": i,
                    "twofa": password or "",
                    "password": password or "",
                }
                (out / f"{stem}.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                items.append({"ok": True, "path": d.name, "stem": stem})

        ok_n = sum(1 for x in items if x.get("ok"))
        if ok_n == 0:
            raise ConvertError("没有成功转换任何 tdata")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in out.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(out).as_posix())
            zf.writestr(
                "_convert_report.json",
                json.dumps({"mode": "tdata_to_session", "items": items},
                           ensure_ascii=False, indent=2),
            )
        return buf.getvalue(), {
            "ok": True, "total": len(items), "succeeded": ok_n,
            "failed": len(items) - ok_n, "items": items,
        }
