"""注册时间内核独立单测（纯标准库，默认不联网）。

跑法：python3 tam/tests/t_shaireg.py
成功时最后一行输出 SHAIREG_CORE_OK。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(PKG))

from tam.gaf.core import shaireg  # noqa: E402

fails = []


def chk(name, cond, extra=""):
    if cond:
        print("  ✓ " + name)
    else:
        fails.append(name)
        print("  ✗ " + name + (("  " + str(extra)) if extra else ""))


print("[1] _safe_label 防路径穿越")
chk("正常日期", shaireg._safe_label("2023-05-01") == "2023-05-01")
chk("剥离 ../", ".." not in shaireg._safe_label("../etc/passwd"))
chk("空变 unknown", shaireg._safe_label("") == "unknown")
chk("None 变 unknown", shaireg._safe_label(None) == "unknown")


print("[2] 离线按本地 date 分组")
with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "src.zip")
    with zipfile.ZipFile(src, "w") as zf:
        for i, d in enumerate(["2021-01-01", "2021-01-01", "2022-06-15"]):
            n = "a%02d" % i
            zf.writestr(n + "/" + n + ".json",
                        json.dumps({"phone": "1" + n, "user_id": i, "dc_id": 2, "date": d}))
            zf.writestr(n + "/" + n + ".session", "S" + n)
    out = os.path.join(tmp, "out.zip")
    r = shaireg.regtime(src, out, resolver=None, workers=1)
    chk("total=3", r["total"] == 3, r)
    chk("全部本地解析", r["resolved"] == 3 and r["unknown"] == 0, r)
    chk("完全离线", r["online"] is False, r)
    chk("分组正确", r["groups"].get("2021-01-01") == 2 and r["groups"].get("2022-06-15") == 1, r["groups"])
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        chk("按日期目录落盘", any(n.startswith("2021-01-01/") for n in names), names)
        chk("含 session+json", sum(1 for n in names if n.endswith(".session")) == 3)


print("[3] 无本地日期 + 无 resolver = unknown")
with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "src.zip")
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("x.json", json.dumps({"phone": "100", "user_id": 1, "dc_id": 2}))
        zf.writestr("x.session", "SX")
    out = os.path.join(tmp, "out.zip")
    r = shaireg.regtime(src, out, resolver=None)
    chk("归入 unknown", r["groups"].get("unknown") == 1 and r["unknown"] == 1, r)


print("[4] 显式 resolver 才会联网（用本地 spy）")
calls = []

def spy(uid, dc):
    calls.append((uid, dc))
    return "2099-12-31"

with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "src.zip")
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("y.json", json.dumps({"phone": "200", "user_id": 42, "dc_id": 5}))
        zf.writestr("y.session", "SY")
        # 有本地 date 的不该调 resolver
        zf.writestr("z.json", json.dumps({"phone": "201", "user_id": 43, "dc_id": 5, "date": "2020-01-01"}))
        zf.writestr("z.session", "SZ")
    out = os.path.join(tmp, "out.zip")
    r = shaireg.regtime(src, out, resolver=spy, workers=1)
    chk("online 标记", r["online"] is True)
    chk("只查缺日期的那个", calls == [(42, 5)], calls)
    chk("远程日期进组", r["groups"].get("2099-12-31") == 1, r["groups"])
    chk("本地日期保留", r["groups"].get("2020-01-01") == 1, r["groups"])


print("[5] 空包报错")
with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "empty.zip")
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("readme.txt", "nope")
    out = os.path.join(tmp, "o.zip")
    try:
        shaireg.regtime(src, out)
        chk("空包报错", False)
    except shaireg.RegTimeError as e:
        chk("空包报错", "json" in str(e).lower() or "账号" in str(e), e)


print("")
if fails:
    print("FAILED=%d" % len(fails))
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("SHAIREG_CORE_OK")
