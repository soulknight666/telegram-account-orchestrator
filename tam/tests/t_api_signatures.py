"""静态校验：api.py 里对内部方法的调用签名对不对得上。

为什么需要这个：
`py_compile` 只查语法，不查名字也不查参数。db.log() 写错参数个数、
把 action 当成第一个位置参传进去（实际会被当成 account_id），都能顺利
编译，只有真正跑到那行才报 TypeError。而 api.py 里很多就是错路径才走的
日志记录，平时测不到，真出事时反而再炸一次。

本脚本用 AST 扫 api.py，不导入 fastapi，所以没装依赖也能跑。
跑法：python3 tam/tests/t_api_signatures.py  →  API_SIG_OK
"""
import ast
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(PKG))

from tam.db import Database  # noqa: E402  (改完 sys.path 才能导)

API_PATH = os.path.join(PKG, "api.py")
fails = []


def bindable(sig, call):
    """拿真实签名试绑一下这个调用的形状。"""
    args = [object()] * len(call.args)
    kwargs = {}
    for kw in call.keywords:
        if kw.arg is None:      # **kwargs 展开，静态算不了，放行
            return True, ""
        kwargs[kw.arg] = object()
    try:
        sig.bind(*args, **kwargs)
        return True, ""
    except TypeError as e:
        return False, str(e)


def attr_chain(node):
    """把 db.log / self.db.log 这种链拼成字符串。"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


# 要盯的方法：调用链后缀 -> 真实函数
WATCH = {"db.log": Database.log}

src = open(API_PATH, encoding="utf-8").read()
tree = ast.parse(src, filename="api.py")

checked = 0
for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
        continue
    name = attr_chain(node.func)
    for suffix, fn in WATCH.items():
        if not (name == suffix or name.endswith("." + suffix)):
            continue
        checked += 1
        # 绑定方法，self 不用算
        sig = inspect.signature(fn)
        sig = sig.replace(parameters=list(sig.parameters.values())[1:])
        ok, err = bindable(sig, node)
        if not ok:
            fails.append(f"api.py:{node.lineno} {name}() {err}")
            print(f"✗ api.py:{node.lineno} {name}() 签名对不上：{err}")
            continue

        # 额外一道：account_id 位置上被塞了字符串 = 把 action 当成了 id
        if suffix == "db.log" and node.args and \
                isinstance(node.args[0], ast.Constant) and \
                isinstance(node.args[0].value, str):
            bad = node.args[0].value
            fails.append(f"api.py:{node.lineno} db.log 首参是字符串 {bad!r}")
            print(f"✗ api.py:{node.lineno} db.log 第一个位置参是字符串 "
                  f"{bad!r}，但那个位置是 account_id，不是 action")
            continue

        print(f"✓ api.py:{node.lineno} {name}() 签名对得上")

print(f"\n共检查 {checked} 处调用")
if checked == 0:
    print("API_SIG_FAILED 一处都没扫到，检查器本身坏了")
    sys.exit(1)

sig = inspect.signature(Database.log)
print(f"db.log 真实签名：log{sig}")

if fails:
    print("API_SIG_FAILED")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("API_SIG_OK")
