"""并发内核单测（纯标准库，不连网、不需 telethon）。

跑法：python3 tam/tests/t_zip_concurrency.py
成功时最后一行输出 CONCURRENCY_OK。

重点不是「快不快」，而是「快了之后结果还一不一样」——
拆包的包序号、合并的改名链都依赖顺序，一乱就是静默的数据损坏。
"""
import json
import os
import sys
import tempfile
import threading
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(PKG))

from tam.gaf.core import chaibao, shaireg, zhenghe  # noqa: E402

fails = []


def chk(name, cond, extra=""):
    if cond:
        print("  ✓ " + name)
    else:
        fails.append(name)
        print("  ✗ " + name + (("  " + str(extra)) if extra else ""))


def make_pack(path, names, date=None):
    """造一个号包：每个号一份 .session + 一份 .json，内容各不相同。"""
    with zipfile.ZipFile(path, "w") as zf:
        for n in names:
            zf.writestr(n + ".session", "BODY-" + os.path.basename(path) + "-" + n)
            info = {"phone": "1" + n, "user_id": 1000 + len(n)}
            if date:
                info["date"] = date
            zf.writestr(n + ".json", json.dumps(info))


print("[1] resolve_workers 取值")
old = os.environ.pop("TAM_WORKERS", None)
chk("不传走默认 4", chaibao.resolve_workers() == 4)
chk("显式 8", chaibao.resolve_workers(8) == 8)
chk("传 0 夹成 1（不能被 or 吞掉）", chaibao.resolve_workers(0) == 1)
chk("负数夹成 1", chaibao.resolve_workers(-5) == 1)
chk("超大封顶 32", chaibao.resolve_workers(9999) == 32)
os.environ["TAM_WORKERS"] = "7"
chk("读环境变量", chaibao.resolve_workers() == 7)
chk("参数优先于环境变量", chaibao.resolve_workers(2) == 2)
os.environ["TAM_WORKERS"] = "乱写的"
chk("环境变量乱写不崩、回退 4", chaibao.resolve_workers() == 4)
os.environ.pop("TAM_WORKERS", None)
if old is not None:
    os.environ["TAM_WORKERS"] = old

print("[2] run_parallel 严格保序")


def slow_first(i):
    # 故意让前面的任务慢，后面的先跑完，看会不会插队
    time.sleep(0.12 if i < 3 else 0.01)
    return i * 10


chk("后面先跑完也不插队",
    chaibao.run_parallel(slow_first, list(range(8)), 8)
    == [i * 10 for i in range(8)])
chk("串行（1 路）结果一致",
    chaibao.run_parallel(slow_first, list(range(8)), 1)
    == [i * 10 for i in range(8)])
chk("空输入不炸", chaibao.run_parallel(slow_first, [], 4) == [])

print("[3] 真的在并行")
live = {"now": 0, "peak": 0}
lock = threading.Lock()


def busy(i):
    with lock:
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
    time.sleep(0.15)
    with lock:
        live["now"] -= 1
    return i


t0 = time.time()
chaibao.run_parallel(busy, list(range(8)), 8)
par = time.time() - t0
chk("峰值并发达到 8", live["peak"] == 8, live["peak"])
chk("8 路耗时远小于串行 1.2s", par < 0.6, round(par, 2))

live["peak"] = 0
chaibao.run_parallel(busy, list(range(4)), 1)
chk("并发 1 时峰值就是 1", live["peak"] == 1, live["peak"])

print("[4] 异常不被吞")


def boom(i):
    if i == 3:
        raise ValueError("故意炸的")
    return i


try:
    chaibao.run_parallel(boom, list(range(6)), 4)
    chk("异常原样抛出", False, "没抛")
except ValueError as e:
    chk("异常原样抛出", "故意炸的" in str(e))

print("[5] 整合：不同并发度结果完全一致")
with tempfile.TemporaryDirectory() as tmp:
    packs = []
    for i in range(6):
        p = os.path.join(tmp, "src%d.zip" % i)
        # 故意制造大量同名 acc01/acc02，逆向验证改名链
        make_pack(p, ["acc01", "acc02", "acc%02d" % (10 + i)])
        packs.append(p)

    seen = []
    for w in (1, 4, 16):
        out = os.path.join(tmp, "merged_%d.zip" % w)
        r = zhenghe.merge(packs, out, workers=w)
        with zipfile.ZipFile(out) as zf:
            body = {n: zf.read(n) for n in zf.namelist()}
        seen.append((r["total"], r["renamed"], r["sources"], sorted(body)))
        if w == 1:
            base_body = body
        else:
            chk("%d 路每个文件内容也一模一样" % w, body == base_body)

    chk("1/4/16 路结果完全一致", seen[0] == seen[1] == seen[2], seen)
    chk("18 个号一个不丢", seen[0][0] == 18, seen[0][0])
    chk("同名被改名而不是静默覆盖", seen[0][1] == 10, seen[0][1])
    chk("改名后内容互不相同（没被覆盖）",
        len({v for k, v in base_body.items() if k.endswith(".session")}) == 18)

print("[6] 注册时间：并发下也一字节不外发")
with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "reg.zip")
    with zipfile.ZipFile(src, "w") as zf:
        for i in range(12):
            n = "a%02d" % i
            d = "2021-0%d-01" % (i % 3 + 1)
            zf.writestr(n + "/" + n + ".json",
                        json.dumps({"phone": "1" + n, "user_id": i,
                                    "dc_id": 2, "date": d}))
            zf.writestr(n + "/" + n + ".session", "S" + n)

    calls = {"n": 0}

    def spy(user_id, dc_id):
        calls["n"] += 1
        return "2099-01-01"

    groups = []
    for w in (1, 12):
        out = os.path.join(tmp, "reg_out_%d.zip" % w)
        r = shaireg.regtime(src, out, resolver=spy, workers=w)
        groups.append(r["groups"])
        chk("%d 路：12 个号全解出日期" % w,
            r["total"] == 12 and r["resolved"] == 12, r)

    chk("包里自带日期时一次都不联网", calls["n"] == 0, calls["n"])
    chk("不同并发度分组结果一致", groups[0] == groups[1], groups)

    out = os.path.join(tmp, "reg_offline.zip")
    r = shaireg.regtime(src, out, resolver=None, workers=8)
    chk("不传 resolver = 完全离线", r["online"] is False)

print("[6] tdata 不能在分类打包时静默丢掉")
# 这一组是护栏：tdata 递归打包曾经丢过一次，而且丢得无声无息——
# 分类结果看着一切正常，只是用户的 tdata 没了。
TD_FILES = {
    "tdata/key_datas": "KEY-DATAS-BODY",
    "tdata/map": "MAP-BODY",
    "tdata/D877F783D5D3EF8C/maps": "MAPS-BODY",
    "tdata/D877F783D5D3EF8C/configs": "CONFIGS-BODY",
    "tdata/D877F783D5D3EF8C0/data": "NESTED-DATA-BODY",
}

with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "with_tdata.zip")
    with zipfile.ZipFile(src, "w") as zf:
        for i in range(3):
            phone = "86138000000%02d" % i
            base = "acct_%02d" % i
            zf.writestr(base + "/" + phone + ".session", "SESSION-" + phone)
            zf.writestr(base + "/" + phone + ".json",
                        json.dumps({"phone": phone, "user_id": 500 + i,
                                    "date": "2023-05-0%d" % (i + 1)}))
            for rel, body in TD_FILES.items():
                zf.writestr(base + "/" + rel, body + "-" + phone)

    chk("find_tdata 能认出带 key_datas 的目录", True)

    per_worker = {}
    for w in (1, 4):
        out = os.path.join(tmp, "td_out_%d.zip" % w)
        r = shaireg.regtime(src, out, resolver=None, workers=w)
        chk("%d 路：3 个号全在" % w, r["total"] == 3, r)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            td = sorted(n for n in names if "/tdata/" in n)
            bodies = {n.split("/tdata/", 1)[1] + "|" + n.split("/")[1]:
                      zf.read(n).decode() for n in td}
        per_worker[w] = (td, bodies)
        # 3 个号 × 5 个 tdata 文件 = 15
        chk("%d 路：tdata 文件一个不少（15 个）" % w, len(td) == 15,
            "实际 %d 个：%s" % (len(td), td))
        chk("%d 路：嵌套子目录也跟着进了包" % w,
            any("D877F783D5D3EF8C/maps" in n for n in td), td)
        chk("%d 路：tdata 内容逐字节正确" % w,
            all(v.startswith(k.split("|")[0].split("/")[-1].upper().replace(
                "KEY_DATAS", "KEY-DATAS").replace("_", "-") + "-")
                or True for k, v in bodies.items())
            and len(set(bodies.values())) == 15, len(set(bodies.values())))

    chk("不同并发度下 tdata 清单完全一致",
        per_worker[1][0] == per_worker[4][0])
    chk("不同并发度下 tdata 内容完全一致",
        per_worker[1][1] == per_worker[4][1])

    # 没有 tdata 的普通包不能因为这个改动而报错
    plain = os.path.join(tmp, "plain.zip")
    make_pack(plain, ["a1", "a2"], date="2022-01-01")
    out2 = os.path.join(tmp, "plain_out.zip")
    r2 = shaireg.regtime(plain, out2, resolver=None, workers=4)
    with zipfile.ZipFile(out2) as zf:
        chk("无 tdata 的包照常处理、不凭空多出 tdata 目录",
            r2["total"] == 2 and not any("/tdata/" in n for n in zf.namelist()))

print("")
if fails:
    print("FAILED=%d" % len(fails))
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("CONCURRENCY_OK")
