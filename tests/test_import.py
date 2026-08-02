"""导入格式与取码解析自检：手机号|取码链接

运行：python3 tests/test_import.py（无需联网 / telethon）
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tam.codefetch import extract_code, strip_html  # noqa: E402
from tam.db import Database  # noqa: E402
from tam.importer import import_accounts, parse_line, parse_text  # noqa: E402

REAL = "+18129773632|https://tgapi.puonl.com/@cof333/8dc96736-3efb-4353-a78b-274f20c5779f/GetHTML"


def test_parse() -> None:
    a = parse_line(REAL)
    assert a.phone == "+18129773632"
    assert a.code_url.endswith("/GetHTML")
    # 转义管道符、全角管道符、制表符、逗号、逆序、空格、手机号无加号
    for variant in (
        REAL.replace("|", "\\|"),
        REAL.replace("|", "\uff5c"),
        REAL.replace("|", "\t"),
        REAL.replace("|", " , "),
        "  " + REAL + "  ",
        "18129773632|" + REAL.split("|", 1)[1],
        REAL.split("|", 1)[1] + "|" + "+18129773632",
    ):
        p = parse_line(variant)
        assert p.phone == "+18129773632" and p.code_url.endswith("/GetHTML"), variant
    # 第三段当备注/别名
    assert parse_line(REAL + "|美国号A").label == "美国号A"
    # 注释与空行
    assert parse_line("# 注释") is None and parse_line("   ") is None
    # 只有手机号也合法
    assert parse_line("+8613800138000").code_url is None
    print("行解析 OK")


def test_batch() -> None:
    text = "\n".join([
        "# 批次 2026-07",
        REAL,
        "+18129773633|https://tgapi.puonl.com/@cof333/aaaa/GetHTML",
        "",
        "乱数据行",
    ])
    items, bad = parse_text(text)
    assert len(items) == 2 and len(bad) == 1 and bad[0]["line"] == "5"

    db = Database(Path(tempfile.mkdtemp()) / "t.db")
    res = import_accounts(db, text, tags=["batch1"], dry_run=True)
    assert res["dry_run"] and len(res["added"]) == 2 and not db.list()

    res = import_accounts(db, text, tags=["batch1"])
    assert len(res["added"]) == 2
    acc = db.list()[0]
    assert acc.phone == "+18129773632" and acc.code_url.endswith("/GetHTML")
    assert acc.tags == ["batch1"] and acc.label == "+18129773632"

    # 幂等：重复导入不新增
    res2 = import_accounts(db, text)
    assert res2["added"] == [] and len(db.list()) == 2
    # 取码链接变更则补写
    res3 = import_accounts(db, "+18129773632|https://tgapi.puonl.com/@cof333/new/GetHTML")
    assert len(res3["updated"]) == 1
    assert db.get(acc.id).code_url.endswith("/new/GetHTML")
    print("批量导入 + 幂等 + 旧库迁移 OK")


def test_code_extract() -> None:
    page = """<html><body><table><tr><td>Telegram</td>
    <td>Login code: 51234. Do not give this code to anyone.</td>
    <td>2026-07-25 16:20</td></tr>
    <tr><td>Telegram</td><td>登录码：48210，勿告知他人。</td></tr></table></body></html>"""
    assert extract_code(page) == "51234"
    assert "<td>" not in strip_html(page)
    assert extract_code("你的 Telegram 验证码是 123456") == "123456"
    assert extract_code('{"sms":"12345 is your Telegram code"}') == "12345"
    assert extract_code("暂无短信") is None
    # 不能把年份/长串数字误认为验证码
    assert extract_code("order 8829301122 at 2026") is None
    print("验证码抽取 OK")


async def test_tools() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_agent import make_ctx

    ctx, db = make_ctx(Path(tempfile.mkdtemp()))
    from tam.tools import call_tool, list_tools

    assert {"import_accounts", "preview_import", "auto_login"} <= {t["name"] for t in list_tools()}
    r = await call_tool(ctx, "preview_import", {"text": REAL})
    assert r["ok"] and r["result"]["parsed"][0]["phone"] == "+18129773632"
    r = await call_tool(ctx, "import_accounts", {"text": REAL, "tags": ["t"]})
    assert r["ok"] and len(r["result"]["added"]) == 1
    aid = db.list()[0].id
    # auto_login 默认不执行，只出预览
    r = await call_tool(ctx, "auto_login", {"account_id": aid})
    assert r["result"]["executed"] is False
    # 无取码链接的账号报错
    r = await call_tool(ctx, "import_accounts", {"text": "+8613800138000"})
    aid2 = db.get_by_label("+8613800138000").id
    r = await call_tool(ctx, "auto_login", {"account_id": aid2, "confirm": True})
    assert r["error"]["code"] == "bad_request"
    print("导入/自动登录工具 OK")


if __name__ == "__main__":
    test_parse()
    test_batch()
    test_code_extract()
    asyncio.run(test_tools())
    print("\n全部导入格式自检通过")
