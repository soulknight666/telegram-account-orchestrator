"""参数面板 _coerce_setting 类型转换单测。

跑法：python3 tam/tests/t_coerce_setting.py
成功时最后一行输出 COERCE_SETTING_OK。

不启动 FastAPI：只把 _coerce_setting 源码抽出来在干净命名空间执行。
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = HERE.parent / "api.py"
fails = []


def chk(name, cond, extra=""):
    if cond:
        print("  ✓ " + name)
    else:
        fails.append(name)
        print("  ✗ " + name + (("  " + str(extra)) if extra else ""))


def load_coerce():
    """从 api.py 抠出 _coerce_setting，避免 import fastapi。"""
    src = API.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_coerce_setting":
            fn = node
            break
    if fn is None:
        raise RuntimeError("api.py 里找不到 _coerce_setting")

    class HTTPException(Exception):
        def __init__(self, status_code=400, detail=""):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)

    # 把 `from .config import _duration_env` 改成从已注入的符号取
    class _Rewriter(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            if node.module in {".config", "config"} or (
                isinstance(node.module, str) and node.module.endswith("config")
            ):
                # 删掉相对导入，改用全局 _duration_env
                return None
            return node

    fn = _Rewriter().visit(fn)
    ast.fix_missing_locations(fn)
    sys.path.insert(0, str(HERE.parent.parent))
    from tam.config import _duration_env  # noqa: E402

    mod = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {
        "Any": object,
        "HTTPException": HTTPException,
        "os": os,
        "_duration_env": _duration_env,
    }
    exec(compile(mod, str(API), "exec"), ns)  # noqa: S102
    return ns["_coerce_setting"], HTTPException


coerce, HTTPException = load_coerce()

print("[1] bool")
chk("True -> 1", coerce("X", "bool", True) == "1")
chk("False -> 0", coerce("X", "bool", False) == "0")
chk("yes -> 1", coerce("X", "bool", "yes") == "1")
chk("off -> 0", coerce("X", "bool", "off") == "0")

print("[2] int + 并发上限")
chk("普通整数", coerce("TAM_PORT", "int", "8848") == "8848")
chk("workers 封顶 32", coerce("TAM_WORKERS", "int", "999") == "32")
chk("workers 下限 1", coerce("TAM_WORKERS", "int", "0") == "1")
chk("batch 封顶 32", coerce("TAM_BATCH_CONCURRENCY", "int", "100") == "32")
try:
    coerce("TAM_PORT", "int", "99999")
    chk("端口越界报错", False)
except HTTPException as e:
    chk("端口越界报错", "端口" in str(e.detail), e.detail)
try:
    coerce("TAM_FOO", "int", "abc")
    chk("非整数报错", False)
except HTTPException as e:
    chk("非整数报错", "整数" in str(e.detail), e.detail)

print("[3] float")
chk("浮点", coerce("TAM_RATE", "float", "0.5") == "0.5")
try:
    coerce("TAM_RATE", "float", "-1")
    chk("负数报错", False)
except HTTPException as e:
    chk("负数报错", "负数" in str(e.detail), e.detail)

print("[4] choice")
chk("合法选项", coerce("TAM_DEPLOY", "choice:local,server", "local") == "local")
try:
    coerce("TAM_DEPLOY", "choice:local,server", "cloud")
    chk("非法选项报错", False)
except HTTPException as e:
    chk("非法选项报错", "只能是" in str(e.detail), e.detail)

print("[5] string 原样 strip")
chk("字符串", coerce("TAM_HOST", "str", "  127.0.0.1  ") == "127.0.0.1")

print("[6] duration（依赖 config._duration_env）")
try:
    out = coerce("TAM_KICK_RETRY", "duration", "10m")
    chk("10m 保留原写法", out == "10m", out)
except Exception as e:
    # 若 config 导入失败则降级提示
    chk("duration 可解析", False, e)

print("")
if fails:
    print("FAILED=%d" % len(fails))
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("COERCE_SETTING_OK")
