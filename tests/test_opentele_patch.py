"""opentele Python 3.13 兼容补丁的离线测试（不需要真的装 opentele）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tam import opentele_patch as op  # noqa: E402

FAKE = '''class Meta(type):
    def __new__(cls, name, bases, ns):
        if name != "ok":
            print("[__firstlineno__] 1 - 2")
            raise BaseException("err")
        return super().__new__(cls, name, bases, ns)
'''


def test_patch_and_revert(tmp: Path) -> None:
    pkg = tmp / "opentele"
    pkg.mkdir(parents=True)
    utils = pkg / "utils.py"
    utils.write_text(FAKE, encoding="utf-8")

    orig = op.utils_path
    op.utils_path = lambda: utils  # type: ignore[assignment]
    try:
        assert op.installed() is True
        assert op.patched() is False

        res = op.apply_patch()
        assert res["ok"] and res["changed"] == 1, res
        text = utils.read_text(encoding="utf-8")
        assert not [l for l in text.splitlines() if l.strip().startswith("raise BaseException")]
        assert op.MARK in text
        assert op.patched() is True
        # 补丁后仍是合法 Python
        compile(text, str(utils), "exec")
        # 缩进保留
        line = [l for l in text.splitlines() if "pass" in l][0]
        assert line.startswith("            pass"), repr(line)

        # 幂等
        again = op.apply_patch()
        assert again["ok"] and again["changed"] == 0, again

        # 备份与回滚
        bak = utils.with_suffix(utils.suffix + op.BAK_SUFFIX)
        assert bak.exists()
        rv = op.revert()
        assert rv["ok"], rv
        assert utils.read_text(encoding="utf-8") == FAKE
        assert op.patched() is False

        st = op.status()
        assert st["opentele_installed"] is True
        assert st["needs_patch"] == (sys.version_info >= (3, 13))
    finally:
        op.utils_path = orig  # type: ignore[assignment]


def test_missing_opentele() -> None:
    orig = op.utils_path
    op.utils_path = lambda: None  # type: ignore[assignment]
    try:
        assert op.installed() is False
        assert op.patched() is False
        assert op.needs_patch() is False
        assert op.apply_patch()["ok"] is False
        assert op.revert()["ok"] is False
    finally:
        op.utils_path = orig  # type: ignore[assignment]


def test_ensure_opentele_message() -> None:
    """没装 opentele 时应得到人话错误，而不是裸崩。"""
    from tam.tdata import ensure_opentele

    try:
        ensure_opentele()
    except RuntimeError as exc:
        assert "opentele" in str(exc)
    except BaseException as exc:  # noqa: BLE001
        raise AssertionError(f"应该转成 RuntimeError，实际：{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        test_patch_and_revert(Path(d))
    test_missing_opentele()
    test_ensure_opentele_message()
    print("test_opentele_patch OK")
