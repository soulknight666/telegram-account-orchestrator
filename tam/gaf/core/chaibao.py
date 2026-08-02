"""拆包内核：把一个含多个 .session 的号包拆成若干个小包。

纯本地文件操作，不联网、不认识 Telegram。
"""
from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

# 原 GAFBot 里这个上限读的是名为 MK_TIME 的环境变量（看名字是个时间），
# 而且默认只有 4MB——一个不到 50 个号的包就能顶穿。这里改成名副其实的
# 变量名和一个能干活的默认值，调用方也可以显式传参覆盖。
DEFAULT_MAX_EXTRACT_MB = int(os.getenv("TAM_MAX_EXTRACT_MB", "512"))


class UnpackError(Exception):
    """拆包失败。消息已经是人话，两个前端直接展示即可。"""


def safe_extract(zip_path: str | Path, target_dir: str | Path) -> None:
    """解压，并拦住路径穿越（zip slip）。

    不能只看字符串开头：`C:\\x` 这种 Windows 绝对路径在 Linux 上
    既不以 / 开头也不以 .. 开头，会直接漏过去。改成解析完绝对路径后
    判断是不是真的落在目标目录里。
    """
    target = Path(target_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            name = member.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            # 剔掉盘符和开头的 /，强制当相对路径处理
            if len(name) > 1 and name[1] == ":":
                name = name[2:]
            name = name.lstrip("/")
            dest = (target / name).resolve()
            if not str(dest).startswith(str(target) + os.sep) and dest != target:
                raise UnpackError(f"压缩包里有非法路径，已拒绝：{member.filename}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)


def get_total_size(path: str | Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total


def find_sessions(root: str | Path) -> list[dict[str, Any]]:
    """找出所有 .session 及其同名 .json。

    按路径排序：os.walk 的顺序依赖文件系统，不排的话同一个包拆两次
    可能得到不同的分组结果，出了问题没法复现。
    """
    out: list[dict[str, Any]] = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".session"):
                continue
            sp = os.path.join(dirpath, fn)
            stem = os.path.splitext(fn)[0]
            jp = os.path.join(dirpath, f"{stem}.json")
            out.append({"session": sp, "json": jp if os.path.exists(jp) else None,
                        "name": stem})
    out.sort(key=lambda s: s["session"])
    return out


def parse_format(text: str, total_count: int) -> tuple[str, Any]:
    """解析拆分格式：`-9-` 固定每包 9 个；`5,5,5` 逐包指定。"""
    import re

    text = (text or "").strip()
    m = re.match(r"^-(\d+)-$", text)
    if m:
        num = int(m.group(1))
        if num <= 0:
            raise ValueError("每包数量必须大于0")
        return "fixed", num

    if "," in text or text.isdigit():
        numbers = []
        for p in text.split(","):
            p = p.strip()
            if not p.isdigit():
                raise ValueError(f"无效的数字：{p}")
            num = int(p)
            if num <= 0:
                raise ValueError("每包数量必须大于0")
            numbers.append(num)
        if sum(numbers) > total_count:
            raise ValueError(f"指定总数({sum(numbers)})超过实际账号数({total_count})")
        return "specified", numbers

    raise ValueError("格式错误，请使用 -9- 或 5,5,5 格式")


def plan_packs(format_type: str, numbers: Any, total_count: int) -> list[int]:
    """算出每个包装几个号。剩下的零头单独成一包，不丢。"""
    if total_count <= 0:
        return []
    if format_type == "fixed":
        size = int(numbers)
        sizes = [size] * (total_count // size)
        rem = total_count % size
        if rem:
            sizes.append(rem)
        return sizes
    sizes = [int(n) for n in numbers]
    rest = total_count - sum(sizes)
    if rest > 0:
        sizes.append(rest)
    return sizes


def _cap_bytes(max_extract_mb: int | None) -> int:
    """注意用 is None 而不是 or：传 0 是“一点都不允许”，
    用 or 会把 0 当成没传而静静换成默认值，上限就失效了。"""
    mb = DEFAULT_MAX_EXTRACT_MB if max_extract_mb is None else int(max_extract_mb)
    return max(mb, 0) * 1024 * 1024


def analyze(zip_path: str | Path, max_extract_mb: int | None = None) -> dict[str, Any]:
    """只看不动：解压到临时目录数一数里面有多少个号。"""
    import tempfile

    cap = _cap_bytes(max_extract_mb)
    with tempfile.TemporaryDirectory() as tmp:
        ex = os.path.join(tmp, "extracted")
        safe_extract(zip_path, ex)
        size = get_total_size(ex)
        if size > cap:
            raise UnpackError(
                f"解压后太大（{size // 1024 // 1024}MB > {cap // 1024 // 1024}MB）"
            )
        sessions = find_sessions(ex)
        return {"count": len(sessions), "names": [s["name"] for s in sessions],
                "extracted_bytes": size}


def unpack(zip_path: str | Path, out_dir: str | Path, fmt: str,
           max_extract_mb: int | None = None,
           prefix: str = "pack") -> dict[str, Any]:
    """拆包主入口。

    :param fmt: 拆分格式文本，如 `-9-` 或 `5,5,5`
    :param out_dir: 生成的小包放哪里（由调用方负责清理）
    :return: {total, packs:[{index,size,path,filename}]}
    """
    import tempfile

    cap = _cap_bytes(max_extract_mb)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        ex = os.path.join(tmp, "extracted")
        safe_extract(zip_path, ex)
        size = get_total_size(ex)
        if size > cap:
            raise UnpackError(
                f"解压后太大（{size // 1024 // 1024}MB > {cap // 1024 // 1024}MB）"
            )

        sessions = find_sessions(ex)
        total = len(sessions)
        if total == 0:
            raise UnpackError("压缩包里没找到 .session 文件")

        format_type, numbers = parse_format(fmt, total)
        sizes = plan_packs(format_type, numbers, total)

        packs: list[dict[str, Any]] = []
        start = 0
        for i, n in enumerate(sizes, 1):
            group = sessions[start:start + n]
            if not group:
                break
            filename = f"{prefix}_{i:02d}.zip"
            target = out / filename
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
                for s in group:
                    zf.write(s["session"], os.path.basename(s["session"]))
                    if s["json"]:
                        zf.write(s["json"], os.path.basename(s["json"]))
            packs.append({"index": i, "size": len(group), "path": str(target),
                          "filename": filename})
            start += n

        return {"total": total, "pack_count": len(packs), "packs": packs}


# --------------------------------------------------------------------------
# 并发支持
#
# 注意：zipfile 不是线程安全的，不能掏出线程池就往上套：
#   - 往同一个 ZipFile 里写不安全（官方文档对线程安全只字未提）
#   - 多线程 extractall 到同一个目录会报错（cpython #112998）
#   - 共用同一个 ZipFile 对象并发读都会 Bad CRC-32（cpython #86535）
# 所以并发只加在确实安全的位置，写同一个输出包的环节一律保持串行。
# --------------------------------------------------------------------------

DEFAULT_WORKERS = 4
MAX_WORKERS = 32


def resolve_workers(workers: int | None = None) -> int:
    """算出实际并发度。

    优先级：函数参数 > 环境变量 TAM_WORKERS > 默认 4，封顶 32。

    用 is None 判空，不能用 or：0 是合法入参（意思是不并发），
    用 or 会把它当成「没传」而静默改成 4。
    """
    if workers is None:
        try:
            workers = int(os.getenv("TAM_WORKERS", str(DEFAULT_WORKERS)))
        except ValueError:
            # 环境变量乱写不能把整个流程弄挂，回退默认就行
            workers = DEFAULT_WORKERS
    try:
        workers = int(workers)
    except (TypeError, ValueError):
        workers = DEFAULT_WORKERS
    return max(1, min(workers, MAX_WORKERS))


def run_parallel(fn, items, workers: int | None = None) -> list:
    """并发跑 fn，**严格保持输入顺序**返回结果。

    保序不是好看：拆包的包序号、合并的改名链都依赖顺序，
    一乱就是静默的数据损坏。ThreadPoolExecutor.map 本身按输入顺序产出。

    fn 里绝不能共用同一个 ZipFile 对象，也不能多个线程往同一个目录解压。
    """
    from concurrent.futures import ThreadPoolExecutor

    items = list(items)
    n = resolve_workers(workers)
    if n <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=min(n, len(items))) as pool:
        return list(pool.map(fn, items))
