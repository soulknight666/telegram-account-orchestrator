"""整合内核独立单测（纯标准库，不联网）。

跑法：python3 tam/tests/t_zhenghe.py
成功时最后一行输出 ZHENGHE_CORE_OK。
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

from tam.gaf.core import zhenghe  # noqa: E402
from tam.gaf.core.chaibao import UnpackError  # noqa: E402

fails = []


def chk(name, cond, extra=""):
    if cond:
        print("  ✓ " + name)
    else:
        fails.append(name)
        print("  ✗ " + name + (("  " + str(extra)) if extra else ""))


def make_pack(path, names):
    with zipfile.ZipFile(path, "w") as zf:
        for n in names:
            zf.writestr(n + ".session", "BODY-" + os.path.basename(path) + "-" + n)
            zf.writestr(n + ".json", json.dumps({"phone": "1" + n, "user_id": hash(n) % 100000}))


print("[1] 空输入 / 坏包")
with tempfile.TemporaryDirectory() as tmp:
    out = os.path.join(tmp, "out.zip")
    try:
        zhenghe.merge([], out)
        chk("空列表报错", False)
    except zhenghe.MergeError as e:
        chk("空列表报错", "没有" in str(e) or "待整合" in str(e), e)

    empty = os.path.join(tmp, "empty.zip")
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("readme.txt", "no sessions here")
    try:
        zhenghe.merge([empty], out)
        chk("无 session 报错", False)
    except zhenghe.MergeError as e:
        chk("无 session 报错", "session" in str(e).lower(), e)


print("[2] 基本合并 + 同名改名")
with tempfile.TemporaryDirectory() as tmp:
    a = os.path.join(tmp, "a.zip")
    b = os.path.join(tmp, "b.zip")
    make_pack(a, ["acc01", "acc02"])
    make_pack(b, ["acc01", "acc03"])  # acc01 重名
    out = os.path.join(tmp, "merged.zip")
    r = zhenghe.merge([a, b], out, workers=1)
    chk("总数 4", r["total"] == 4, r)
    chk("重名改名 1 次", r["renamed"] == 1, r)
    chk("sources 2", r["sources"] == 2, r)
    with zipfile.ZipFile(out) as zf:
        names = sorted(zf.namelist())
        sessions = [n for n in names if n.endswith(".session")]
        chk("4 个 session 在包里", len(sessions) == 4, sessions)
        bodies = {n: zf.read(n) for n in sessions}
        chk("内容互不覆盖", len(set(bodies.values())) == 4, bodies)


print("[3] 坏包被跳过，好包照常")
with tempfile.TemporaryDirectory() as tmp:
    good = os.path.join(tmp, "good.zip")
    bad = os.path.join(tmp, "bad.zip")
    make_pack(good, ["x1", "x2"])
    with open(bad, "wb") as f:
        f.write(b"not a zip at all")
    out = os.path.join(tmp, "m.zip")
    r = zhenghe.merge([good, bad], out, workers=1)
    chk("好包 2 个号在", r["total"] == 2, r)
    chk("坏包记入 skipped", len(r["skipped"]) == 1, r["skipped"])
    chk("sources 只算成功的", r["sources"] == 1, r)


print("[4] plan_merge 纯逻辑")
items, renamed = zhenghe.plan_merge([
    {"name": "a", "session": "1", "json": None},
    {"name": "a", "session": "2", "json": None},
    {"name": "b", "session": "3", "json": None},
])
chk("两个 a 改成 a 和 a_2", [it["final"] for it in items] == ["a", "a_2", "b"], items)
chk("renamed=1", renamed == 1, renamed)


print("")
if fails:
    print("FAILED=%d" % len(fails))
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("ZHENGHE_CORE_OK")
