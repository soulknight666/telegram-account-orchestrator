"""混合 ZIP 导入的安全扫描与拆分工具。

本模块只负责文件识别、解压和元数据读取；真正的账号写入仍由
``AccountManager.import_tdata`` / ``import_session_files`` 完成，因此旧导入
接口的行为保持不变。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


IGNORE_NAMES = {"__macosx", ".ds_store", "thumbs.db"}
PHONE_RE = re.compile(r"^\+?\d{5,20}$")


class MixedArchiveError(ValueError):
    """ZIP 结构不安全、损坏或超出限制。"""


def limits() -> tuple[int, int]:
    """返回解压总字节数和文件数上限，均可用环境变量覆盖。"""
    try:
        mb = int(os.environ.get("TAM_MIXED_MAX_EXTRACT_MB", "2048"))
    except ValueError:
        mb = 2048
    try:
        files = int(os.environ.get("TAM_MIXED_MAX_FILES", "20000"))
    except ValueError:
        files = 20000
    return max(1, mb) * 1024 * 1024, max(1, files)


def _safe_member(name: str) -> str | None:
    """规范化 ZIP 成员路径；不安全路径返回 None。"""
    value = (name or "").replace("\\", "/")
    if not value or "\x00" in value:
        return None
    value = value.lstrip("/")
    parts = [p for p in value.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    # Windows 盘符、UNC 路径和隐藏系统目录都不参与导入。
    if len(parts[0]) == 2 and parts[0][1] == ":":
        return None
    if any(p.lower() in IGNORE_NAMES for p in parts):
        return None
    return "/".join(parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return (mode & 0o170000) == 0o120000


def _write_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path,
                  state: dict[str, int], max_bytes: int, max_files: int) -> None:
    if _is_symlink(info):
        raise MixedArchiveError(f"ZIP 包含符号链接：{info.filename}")
    if info.is_dir() or info.filename.endswith(("/", "\\")):
        return
    state["files"] += 1
    if state["files"] > max_files:
        raise MixedArchiveError(f"解压文件数超过上限（{max_files}）")
    expected = max(0, int(getattr(info, "file_size", 0) or 0))
    if state["bytes"] + expected > max_bytes:
        raise MixedArchiveError(f"解压总大小超过上限（{max_bytes // 1024 // 1024}MB）")
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with zf.open(info) as src, dest.open("wb") as out:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if state["bytes"] + written > max_bytes:
                raise MixedArchiveError(
                    f"解压总大小超过上限（{max_bytes // 1024 // 1024}MB）"
                )
            out.write(chunk)
    state["bytes"] += written


def extract_mixed_zip(raw: bytes, destination: Path) -> dict[str, Any]:
    """安全解压 ZIP，并只展开一层内嵌 ZIP。

    返回 ``root``、``unsafe`` 和统计信息；调用方负责在 finally 中删除
    ``destination``。内嵌 ZIP 不再继续递归展开。
    """
    max_bytes, max_files = limits()
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / "__outer__.zip"
    archive.write_bytes(raw)
    state = {"bytes": 0, "files": 0}
    try:
        with zipfile.ZipFile(archive) as outer:
            nested: list[tuple[str, bytes]] = []
            for info in outer.infolist():
                safe = _safe_member(info.filename)
                if safe is None:
                    # 忽略系统文件，路径穿越/盘符则拒绝整个包。
                    value = (info.filename or "").replace("\\", "/")
                    if any(x in value.split("/") for x in ("..",)) or value.startswith("/"):
                        raise MixedArchiveError(f"ZIP 路径不安全：{info.filename}")
                    continue
                if info.is_dir() or info.filename.endswith(("/", "\\")):
                    continue
                if safe.lower().endswith(".zip"):
                    if _is_symlink(info):
                        raise MixedArchiveError(f"ZIP 包含符号链接：{info.filename}")
                    # 内嵌 ZIP 本身也计入大小/文件数，避免压缩炸弹绕过限制。
                    state["files"] += 1
                    if state["files"] > max_files:
                        raise MixedArchiveError(f"解压文件数超过上限（{max_files}）")
                    expected = max(0, int(getattr(info, "file_size", 0) or 0))
                    if state["bytes"] + expected > max_bytes:
                        raise MixedArchiveError(
                            f"解压总大小超过上限（{max_bytes // 1024 // 1024}MB）"
                        )
                    data = outer.read(info)
                    state["bytes"] += len(data)
                    if state["bytes"] > max_bytes:
                        raise MixedArchiveError(
                            f"解压总大小超过上限（{max_bytes // 1024 // 1024}MB）"
                        )
                    nested.append((safe, data))
                    continue
                _write_member(outer, info, destination / safe, state,
                              max_bytes, max_files)

            # 只展开一层：内层 zip 中再次出现 zip 时按普通未知文件保留，
            # 不递归处理，避免恶意嵌套消耗资源。
            for nested_index, (nested_name, data) in enumerate(nested, 1):
                prefix = f"{nested_index}_{Path(nested_name).with_suffix('').as_posix()}"
                nested_path = destination / ("__nested__" + prefix.replace("/", "__") + ".zip")
                nested_path.write_bytes(data)
                try:
                    with zipfile.ZipFile(nested_path) as inner:
                        for info in inner.infolist():
                            safe_inner = _safe_member(info.filename)
                            if safe_inner is None:
                                value = (info.filename or "").replace("\\", "/")
                                if ".." in value.split("/") or value.startswith("/"):
                                    raise MixedArchiveError(f"内嵌 ZIP 路径不安全：{info.filename}")
                                continue
                            if info.is_dir() or info.filename.endswith(("/", "\\")):
                                continue
                            if safe_inner.lower().endswith(".zip"):
                                # 仅记录为未知文件，绝不再次展开。
                                target = destination / "__nested_files__" / prefix / safe_inner
                            else:
                                target = destination / "__nested_files__" / prefix / safe_inner
                            _write_member(inner, info, target, state,
                                          max_bytes, max_files)
                except zipfile.BadZipFile as exc:
                    raise MixedArchiveError(f"内嵌 ZIP 损坏：{nested_name}") from exc
                finally:
                    nested_path.unlink(missing_ok=True)
    except zipfile.BadZipFile as exc:
        raise MixedArchiveError("不是有效的 ZIP 文件") from exc
    finally:
        archive.unlink(missing_ok=True)
    return {"root": destination, "files": state["files"], "bytes": state["bytes"]}


def _phone(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = re.sub(r"[^0-9+]", "", text)
    if not PHONE_RE.fullmatch(digits):
        return None
    return digits if digits.startswith("+") else "+" + digits


def _read_sidecar(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > 1024 * 1024:
            return {}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def scan_mixed_tree(root: Path) -> dict[str, Any]:
    """识别 tdata、session、JSON 配对文件和未知文件。"""
    tdata_dirs: list[Path] = []
    for key in sorted(root.rglob("key_datas"), key=lambda p: len(p.parts)):
        if not key.is_file():
            continue
        parent = key.parent
        if any(parent == existing or existing in parent.parents for existing in tdata_dirs):
            continue
        tdata_dirs.append(parent)
    tdata_dirs.sort(key=lambda p: p.relative_to(root).as_posix())
    tdata_set = {p.resolve() for p in tdata_dirs}

    sessions: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(root.rglob("*.session"), key=lambda p: p.relative_to(root).as_posix()):
        if any(part.lower() in IGNORE_NAMES for part in path.relative_to(root).parts):
            continue
        sidecar = path.with_suffix(".json")
        meta = _read_sidecar(sidecar) if sidecar.is_file() else {}
        sessions.append((path, meta))

    items: list[dict[str, Any]] = []
    covered: set[Path] = set()
    for d in tdata_dirs:
        rel = d.relative_to(root).as_posix()
        if rel == ".":
            rel = "tdata"
        phone = _phone(d.parent.name)
        if phone is None:
            phone = _phone(d.name)
        items.append({"path": rel, "kind": "tdata", "phone": phone,
                      "source": d, "status": "ready", "account_id": None,
                      "code": None, "reason": None})
        covered.update(p for p in d.rglob("*") if p.is_file())

    session_paths = {p.resolve() for p, _ in sessions}
    for path, meta in sessions:
        rel = path.relative_to(root).as_posix()
        phone = _phone(meta.get("phone") or meta.get("phone_number") or meta.get("number"))
        if phone is None:
            phone = _phone(path.stem)
        items.append({"path": rel, "kind": "session", "phone": phone,
                      "source": path, "sidecar": meta, "status": "ready",
                      "account_id": None, "code": None, "reason": None})
        covered.add(path.resolve())
        sidecar = path.with_suffix(".json")
        if sidecar.is_file():
            covered.add(sidecar.resolve())

    skipped: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if not path.is_file() or path.resolve() in covered:
            continue
        rel_parts = path.relative_to(root).parts
        if any(part.lower() in IGNORE_NAMES for part in rel_parts):
            continue
        # 已识别 tdata 内部文件全部属于该账号，不作为 skipped。
        if any(parent.resolve() in tdata_set for parent in [path, *path.parents]):
            continue
        skipped.append({"path": path.relative_to(root).as_posix(),
                        "code": "import_unknown_format", "reason": "无法识别"})

    return {"items": items, "skipped": skipped}


def cleanup_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


__all__ = ["MixedArchiveError", "extract_mixed_zip", "scan_mixed_tree", "limits", "cleanup_tree"]
