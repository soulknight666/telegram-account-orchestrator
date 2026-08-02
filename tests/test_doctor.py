"""一键体检（doctor）的离线自检。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tam import doctor  # noqa: E402


def test_env_read_write(tmp: Path) -> None:
    p = tmp / ".env"
    p.write_text(
        "# 注释行\n"
        "TAM_MASTER_KEY=      # 待填\n"
        'TAM_WEB_TOKEN="quoted-token"\n'
        "TAM_API_ID=1234567  # 示例\n",
        encoding="utf-8",
    )
    env = doctor.read_env(p)
    assert env["TAM_MASTER_KEY"] == ""
    assert env["TAM_WEB_TOKEN"] == "quoted-token"
    assert env["TAM_API_ID"] == "1234567"

    doctor.set_env({"TAM_MASTER_KEY": "abc", "TAM_NEW": "1"}, p)
    text = p.read_text(encoding="utf-8")
    assert "# 注释行" in text                 # 保留注释
    assert "TAM_MASTER_KEY=abc" in text
    assert "TAM_NEW=1" in text
    env2 = doctor.read_env(p)
    assert env2["TAM_MASTER_KEY"] == "abc"
    # 幂等：再写一次不会重复行
    doctor.set_env({"TAM_NEW": "2"}, p)
    assert p.read_text(encoding="utf-8").count("TAM_NEW=") == 1
    print(".env 读写 / 幂等 OK")


def test_placeholder_detection() -> None:
    assert doctor._unset("") and doctor._unset("  ") and doctor._unset("change-me")
    assert doctor._unset("1234567") and doctor._unset("x" * 32)
    assert not doctor._unset(doctor.gen_key())
    print("占位值识别 OK")


def test_autofix_env(tmp: Path) -> None:
    """缺 .env / 缺密钥 → --fix 应自动补齐。"""
    old_env, old_example = doctor.ENV_PATH, doctor.EXAMPLE_PATH
    doctor.ENV_PATH = tmp / ".env"
    doctor.EXAMPLE_PATH = tmp / ".env.example"
    doctor.EXAMPLE_PATH.write_text("TAM_MASTER_KEY=\nTAM_WEB_TOKEN=\n", encoding="utf-8")
    saved = {k: os.environ.get(k) for k in ("TAM_MASTER_KEY", "TAM_WEB_TOKEN")}
    for k in saved:
        os.environ.pop(k, None)
    try:
        assert doctor.check_env_file(False).status == doctor.FAIL
        c = doctor.check_env_file(True)
        assert c.status == doctor.OK and c.fixed and doctor.ENV_PATH.exists()

        assert doctor.check_master_key(False).status == doctor.FAIL
        c = doctor.check_master_key(True)
        assert c.status == doctor.OK and c.fixed
        c = doctor.check_web_token(True)
        assert c.status == doctor.OK and c.fixed

        env = doctor.read_env(doctor.ENV_PATH)
        assert len(env["TAM_MASTER_KEY"]) > 20 and len(env["TAM_WEB_TOKEN"]) > 20
        # 再跑一次应该直接 OK 且不再改写
        again = doctor.check_master_key(True)
        assert again.status == doctor.OK and not again.fixed
        assert doctor.read_env(doctor.ENV_PATH)["TAM_MASTER_KEY"] == env["TAM_MASTER_KEY"]
        print("缺失配置自动修复 OK")
    finally:
        doctor.ENV_PATH, doctor.EXAMPLE_PATH = old_env, old_example
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_run_doctor_structure() -> None:
    """体检自身不能崩；沙箱里缺 fastapi 也应输出完整报告。"""
    res = doctor.run_doctor(fix=False)
    assert set(res) >= {"ok", "checks", "failed", "warned", "fixed"}
    assert len(res["checks"]) == len(doctor.CHECKS)
    for c in res["checks"]:
        assert c["status"] in (doctor.OK, doctor.WARN, doctor.FAIL)
        assert c["name"]
    names = [c["name"] for c in res["checks"]]
    assert any("Python" in n for n in names)
    assert any("opentele" in n for n in names)
    doctor.print_report(res, False)
    print("体检报告结构 OK")


def test_cli_wired() -> None:
    from tam import cli

    src = Path(cli.__file__).read_text(encoding="utf-8")
    for token in ("doctor", "--fix", "fix-opentele", "--no-doctor"):
        assert token in src, token
    from tam.api import app  # noqa: F401
    print("CLI/API 接线 OK")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        test_env_read_write(Path(d))
    test_placeholder_detection()
    with tempfile.TemporaryDirectory() as d:
        test_autofix_env(Path(d))
    test_run_doctor_structure()
    try:
        test_cli_wired()
    except ModuleNotFoundError as exc:
        print(f"CLI/API 接线检查跳过（沙箱缺依赖：{exc.name}）")
    print("\n全部体检自检通过")
