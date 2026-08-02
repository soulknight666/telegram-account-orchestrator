"""工具箱纯逻辑单测（不需要 telethon，也不连网）。

跑法：python3 tam/tests/t_toolbox.py
成功时最后一行输出 TOOLBOX_OK。
"""
import asyncio
import importlib.util as iu
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TB_PATH = os.path.join(os.path.dirname(HERE), "toolbox.py")

_spec = iu.spec_from_file_location("tam_toolbox", TB_PATH)
tb = iu.module_from_spec(_spec)
_spec.loader.exec_module(tb)

fails = []


def chk(name, fn, expect_err=False):
    try:
        r = fn()
        if expect_err:
            fails.append(name)
            print(f"✗ {name} 应该报错，却返回 {r!r}")
        else:
            print(f"✓ {name} -> {r!r}")
    except tb.ToolboxError as e:
        if expect_err:
            print(f"✓ {name} 拦住：{e}")
        else:
            fails.append(name)
            print(f"✗ {name} 不该报错：{e}")


print("== 注册表 ==")
assert set(s["op"] for s in tb.OP_SPECS) == set(tb.OPS)
print(f"✓ OP_SPECS 与 OPS 一一对应，共 {len(tb.OPS)} 项")
for s in tb.OP_SPECS:
    assert s.get("label") and s.get("desc"), f"{s['op']} 缺标题或说明"
print("✓ 每项都有标题与说明（网页要拿去渲染）")

# 不可逆的操作必须被标成危险，前端靠这个决定要不要弹二次确认
DANGER_MUST = {"twofa", "contacts_clear", "dialogs_clear", "profile_clear",
               "terminate_others", "logout"}
marked = {s["op"] for s in tb.OP_SPECS if s.get("danger")}
assert DANGER_MUST <= marked, f"这些没标危险：{DANGER_MUST - marked}"
print(f"✓ 危险操作已标记：{sorted(marked)}")

print("== 参数校验 ==")
chk("销号未确认", lambda: tb.validate_params("logout", {}), True)
chk("销号已确认", lambda: tb.validate_params("logout", {"confirm": True}))
chk("销号勾了又取消", lambda: tb.validate_params("logout", {"confirm": False}),
    True)
chk("未知操作", lambda: tb.validate_params("nope", {}), True)
chk("未知参数被拒", lambda: tb.validate_params("alive", {"evil": 1}), True)
chk("超时填字符串", lambda: tb.validate_params("alive", {"timeout": "abc"}),
    True)
chk("超时默认回填", lambda: tb.validate_params("alive", {}))
chk("筛料空号码", lambda: tb.validate_params("check_phones", {}), True)
chk("bool 字符串 on",
    lambda: tb.validate_params("contacts_clear", {"dry_run": "on"}))
chk("bool 字符串 0",
    lambda: tb.validate_params("contacts_clear", {"dry_run": "0"}))
chk("profile 默认值", lambda: tb.validate_params("profile_clear", {}))
chk("隐私项透传",
    lambda: tb.validate_params("privacy", {"items": {"phone": "nobody"}}))

assert tb.validate_params("contacts_clear", {"dry_run": "0"})["dry_run"] is False
print("✓ 字符串 '0' 没被当成真（否则会把「只统计」变成真删）")

print("== 批量执行 ==")


class FakeMgr:
    """假的 manager，只用来验证并发与保序，不碰网络。"""

    def __init__(self, delays):
        self.delays = delays
        self.peak = 0
        self.live = 0

    async def run_batch(self, ids, task, concurrency=None):
        if concurrency is None:
            concurrency = int(os.getenv("TAM_BATCH_CONCURRENCY", "3"))
        concurrency = max(1, min(int(concurrency), 32))
        sem = asyncio.Semaphore(concurrency)
        out = [{} for _ in ids]

        async def worker(i, aid):
            async with sem:
                self.live += 1
                self.peak = max(self.peak, self.live)
                try:
                    await asyncio.sleep(self.delays[i])
                    out[i] = {"account_id": aid, "ok": True, "result": aid}
                finally:
                    self.live -= 1

        await asyncio.gather(*(worker(i, a) for i, a in enumerate(ids)))
        return out


async def main():
    ids = [101, 102, 103, 104, 105, 106]
    # 故意让后面的先跑完，验证结果不会插队
    delays = [0.06, 0.05, 0.04, 0.03, 0.02, 0.01]

    m = FakeMgr(delays)
    r = await tb.run_op_batch(m, ids, "alive", {}, concurrency=6)
    got = [x["account_id"] for x in r["results"]]
    assert got == ids, f"顺序错了：{got}"
    print(f"✓ 结果严格保序（后面的先跑完也不插队）：{got}")
    assert r["ok"] == 6 and r["failed"] == 0
    print(f"✓ 汇总正确：total={r['total']} ok={r['ok']} failed={r['failed']}")
    assert m.peak == 6, f"并发度没生效，峰值只有 {m.peak}"
    print(f"✓ 并发 6 真的并了（峰值 {m.peak}）")

    m2 = FakeMgr(delays)
    await tb.run_op_batch(m2, ids, "alive", {}, concurrency=1)
    assert m2.peak == 1, f"串行模式峰值应为 1，实际 {m2.peak}"
    print("✓ 并发 1 = 完全串行")

    m3 = FakeMgr(delays)
    await tb.run_op_batch(m3, ids, "alive", {}, concurrency=0)
    assert m3.peak == 1, "传 0 应该夹成 1，不能报错也不能变无限"
    print("✓ 传 0 夹成 1（没被 or 吞掉）")

    os.environ["TAM_BATCH_CONCURRENCY"] = "4"
    m4 = FakeMgr(delays)
    await tb.run_op_batch(m4, ids, "alive", {})
    assert m4.peak == 4, f"环境变量没生效，峰值 {m4.peak}"
    print("✓ 不传参数时读 TAM_BATCH_CONCURRENCY=4")
    os.environ.pop("TAM_BATCH_CONCURRENCY")

    # 参数错应该开跑前就拦，不能跑到第 50 个号才发现
    try:
        await tb.run_op_batch(FakeMgr(delays), ids, "logout", {})
        fails.append("批量销号未确认居然放行")
        print("✗ 批量销号未确认居然放行")
    except tb.ToolboxError as e:
        print(f"✓ 参数错在开跑前就拦住：{e}")

    try:
        await tb.run_op_batch(FakeMgr([]), [], "alive", {})
        fails.append("空账号列表未拦")
        print("✗ 空账号列表未拦")
    except tb.ToolboxError as e:
        print(f"✓ 空账号列表拦住：{e}")


asyncio.run(main())

if fails:
    print("TOOLBOX_FAILED " + str(fails))
    sys.exit(1)
print("TOOLBOX_OK")
