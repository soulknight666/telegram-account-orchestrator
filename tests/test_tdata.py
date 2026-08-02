"""tdata 导入自检（离线，不需 opentele / telethon）。

运行：python3 tests/test_tdata.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tam.tdata import find_tdata_dirs, is_tdata_dir, tdata_to_sessions  # noqa: E402
from tam.tools import HUMAN_ONLY, list_tools  # noqa: E402


def _make_tdata(root: Path, name: str = "tdata", key: bool = True) -> Path:
    d = root / name
    (d / "D877F783D5D3EF8C").mkdir(parents=True)
    if key:
        (d / "key_datas").write_bytes(b"TDF$fake")
    return d


def test_detect() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        d = _make_tdata(root)
        assert is_tdata_dir(d)
        # 缺 key_datas 但有分片目录也认
        assert is_tdata_dir(_make_tdata(root, "tdata2", key=False))
        # 普通目录与不存在路径
        (root / "plain").mkdir()
        assert not is_tdata_dir(root / "plain")
        assert not is_tdata_dir(root / "nope")
    print("tdata 目录识别 OK")


def test_scan() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # 直指 tdata 本身
        direct = _make_tdata(root, "tdata")
        assert find_tdata_dirs(direct) == [direct]
        # 父目录下多份：batch/号A/tdata、batch/号B/tdata
        batch = root / "batch"
        a = _make_tdata(batch / "号A")
        b = _make_tdata(batch / "号B")
        found = sorted(str(p) for p in find_tdata_dirs(batch))
        assert found == sorted([str(a), str(b)]), found
        # 不会把分片子目录重复计入
        assert len(find_tdata_dirs(batch)) == 2
        # 空目录返回空、不存在报错
        (root / "empty").mkdir()
        assert find_tdata_dirs(root / "empty") == []
        try:
            find_tdata_dirs(root / "missing")
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("不存在路径应报错")
    print("tdata 扫描/批量发现 OK")


def test_convert_guard() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        plain = Path(tmp) / "plain"
        plain.mkdir()
        try:
            asyncio.run(tdata_to_sessions(plain))
        except Exception as exc:  # noqa: BLE001 - 内置解析器抛 TDataError
            # 未装 opentele 时提示依赖；装了则报“不是有效 tdata”
            assert "opentele" in str(exc) or "tdata" in str(exc), exc
        else:
            raise AssertionError("非 tdata 目录应报错")
    print("转换前置校验 OK")


def test_not_exposed_to_agent() -> None:
    names = {t["name"] for t in list_tools()}
    assert "import_tdata" not in names, "tdata 导入涉及凭证，不得开放给 Agent"
    assert "import_tdata" in HUMAN_ONLY
    print("仅人工可用限制 OK")


if __name__ == "__main__":
    test_detect()
    test_scan()
    test_convert_guard()
    test_not_exposed_to_agent()
    print("\n全部 tdata 导入自检通过")
