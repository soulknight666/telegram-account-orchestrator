from __future__ import annotations

from tam.restart import RESTART_EXIT_CODE, build_restart_argv
from tam.run import _windows_child_command


def test_restart_argv_preserves_module_launch() -> None:
    command = build_restart_argv(
        [r"C:\repo\tam\run.py", "--port", "8848"],
        executable=r"C:\Python\python.exe",
        frozen=False,
        main_module="tam.run",
    )

    assert command == (
        r"C:\Python\python.exe",
        "-m",
        "tam.run",
        "--port",
        "8848",
    )


def test_restart_argv_does_not_duplicate_frozen_executable() -> None:
    command = build_restart_argv(
        [r"C:\TAO\TAO-Launcher.exe", "--runtime", "--port", "8848"],
        executable=r"C:\TAO\TAO-Launcher.exe",
        frozen=True,
    )

    assert command == (
        r"C:\TAO\TAO-Launcher.exe",
        "--runtime",
        "--port",
        "8848",
    )


def test_restart_argv_preserves_console_entry_point() -> None:
    assert build_restart_argv(
        ["tao-run", "--port", "8848"],
        executable="python",
        frozen=False,
        main_module=None,
    ) == ("tao-run", "--port", "8848")


def test_restart_exit_code_is_stable_for_process_supervisors() -> None:
    assert RESTART_EXIT_CODE == 75


def test_windows_source_supervisor_preserves_runtime_arguments() -> None:
    command = _windows_child_command(["--deploy", "local", "--port", "8848"])

    assert command[1:4] == ("-m", "tam.run", "--runtime-child")
    assert command[-4:] == ("--deploy", "local", "--port", "8848")
