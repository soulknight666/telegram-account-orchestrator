"""Build a faithful command for replacing the current TAO process."""
from __future__ import annotations

import sys
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Sequence

_AUTO_MODULE = object()
RESTART_EXIT_CODE = 75


def build_restart_argv(
    argv: Sequence[str] | None = None,
    *,
    executable: str | None = None,
    frozen: bool | None = None,
    main_module: str | None | object = _AUTO_MODULE,
) -> tuple[str, ...]:
    """Reconstruct the original entry point for source and packaged runs."""
    current = tuple(argv or sys.argv)
    if not current:
        raise RuntimeError("无法确定当前进程的启动参数")

    python = executable or sys.executable
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if is_frozen:
        return (python, *current[1:])

    if main_module is _AUTO_MODULE:
        main = sys.modules.get("__main__")
        spec = getattr(main, "__spec__", None)
        main_module = getattr(spec, "name", None)
    if main_module and main_module != "__main__":
        return (python, "-m", main_module, *current[1:])

    entry = current[0]
    if Path(entry).suffix.lower() in {".py", ".pyw"}:
        return (python, entry, *current[1:])
    return current


def spawn_restart_helper(command: Sequence[str], *, parent_pid: int | None = None) -> None:
    """Start a handle-free Windows helper that relaunches after this PID exits."""
    parent_pid = parent_pid or os.getpid()
    payload = json.dumps(list(command), ensure_ascii=False)
    helper = (
        sys.executable,
        "-m",
        "tam.restart",
        "--helper",
        str(parent_pid),
        os.getcwd(),
        payload,
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    subprocess.Popen(
        helper,
        close_fds=True,
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_parent_exit(parent_pid: int, timeout: float = 15.0) -> None:
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, parent_pid)
        if handle:
            try:
                ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000))
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)


def run_helper(parent_pid: int, cwd: str, payload: str) -> int:
    command = json.loads(payload)
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        return 2
    _wait_for_parent_exit(parent_pid)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    subprocess.Popen(
        command,
        cwd=cwd,
        env=os.environ.copy(),
        close_fds=True,
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 4 and args[0] == "--helper":
        return run_helper(int(args[1]), args[2], args[3])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
