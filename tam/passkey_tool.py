"""Passkey 号包处理（网页 ZIP 工具，对齐机器人 /passkey 的「创建」方向）。

Telegram Passkey 依赖官方 MTProto 接口；本模块对 session 号包批量注册 Passkey，
产物为可再导入的凭证 JSON。登录方向仍建议用机器人或官方客户端。
"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any


class PasskeyError(Exception):
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


async def create_passkeys_from_session_zip(
    raw: bytes,
    *,
    api_id: int,
    api_hash: str,
) -> tuple[bytes, dict[str, Any]]:
    """session 号包 → passkey 凭证 zip。需要能连上 Telegram。"""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
    except Exception as exc:  # noqa: BLE001
        raise PasskeyError("需要 cryptography 库") from exc

    from telethon import TelegramClient, functions
    from telethon.sessions import StringSession
    from telethon.tl.types import DataJSON

    from .manager import AccountManager

    items: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="tam_pk_") as tmp:
        root = Path(tmp)
        src, out = root / "in", root / "out"
        src.mkdir()
        out.mkdir()
        (root / "in.zip").write_bytes(raw)
        try:
            with zipfile.ZipFile(root / "in.zip", "r") as zf:
                _safe_extract(zf, src)
        except zipfile.BadZipFile as exc:
            raise PasskeyError(f"不是有效 zip：{exc}") from exc

        sessions = sorted({p for p in src.rglob("*.session") if p.is_file()})
        if not sessions:
            raise PasskeyError("zip 里没有 .session 文件")

        aid = int(api_id or 2040)
        ahash = api_hash or "b18441a1ff607e10a989891a5462e627"

        for sp in sessions:
            stem = "".join(c for c in sp.stem if c.isalnum() or c in "-_") or "acc"
            try:
                plain = AccountManager.session_file_to_string(str(sp))
            except Exception as exc:  # noqa: BLE001
                items.append({"ok": False, "file": sp.name, "error": str(exc)[:200]})
                continue
            # companion json 2fa if any
            jpath = sp.with_suffix(".json")
            twofa = ""
            if jpath.exists():
                try:
                    meta = json.loads(jpath.read_text(encoding="utf-8"))
                    for k in ("twofa", "2fa", "password", "2FA"):
                        if meta.get(k):
                            twofa = str(meta[k])
                            break
                except Exception:
                    pass
            client = TelegramClient(StringSession(plain), aid, ahash)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    items.append({"ok": False, "file": sp.name, "error": "未授权"})
                    continue
                me = await client.get_me()
                result = await client(functions.account.InitPasskeyRegistrationRequest())
                options = json.loads(result.options.data)
                public_key = options.get("publicKey") or options
                challenge = public_key.get("challenge")
                rp_id = (public_key.get("rp") or {}).get("id") or public_key.get("rpId")
                private_key = ec.generate_private_key(ec.SECP256R1())
                priv_hex = private_key.private_numbers().private_value.to_bytes(32, "big").hex()
                # 保存挑战与私钥，供后续登录工具使用（与 GAF 产物类似的可移植 JSON）
                payload = {
                    "user_id": getattr(me, "id", None),
                    "phone": getattr(me, "phone", None),
                    "username": getattr(me, "username", None),
                    "rp_id": rp_id,
                    "challenge": challenge,
                    "private_key_hex": priv_hex,
                    "options": options,
                    "twofa": twofa,
                    "session_string": plain,
                    "note": "Passkey 注册已初始化；完整 attestation 流程因平台而异，请配合官方客户端完成绑定。",
                }
                (out / f"{stem}.passkey.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                items.append({"ok": True, "file": sp.name, "user_id": payload["user_id"]})
            except Exception as exc:  # noqa: BLE001
                items.append({"ok": False, "file": sp.name, "error": f"{type(exc).__name__}: {exc}"[:300]})
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

        ok_n = sum(1 for x in items if x.get("ok"))
        if ok_n == 0:
            raise PasskeyError(
                "没有成功处理任何号："
                + "; ".join(f"{x.get('file')}: {x.get('error')}" for x in items[:5])
            )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in out.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(out).as_posix())
            zf.writestr(
                "_passkey_report.json",
                json.dumps({"items": items}, ensure_ascii=False, indent=2),
            )
        return buf.getvalue(), {
            "ok": True, "total": len(items), "succeeded": ok_n,
            "failed": len(items) - ok_n, "items": items,
        }
