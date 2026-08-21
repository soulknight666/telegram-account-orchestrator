from __future__ import annotations

import socket
import sys
import time
from dataclasses import replace
from pathlib import Path

from tam.process_controller import (
    ProcessController,
    RuntimeState,
    build_runtime_command,
    is_port_available,
)
from tam.release_config import ReleaseConfig


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition was not reached before timeout")


def test_build_runtime_command_for_source_and_frozen() -> None:
    config = replace(ReleaseConfig(), deploy="server", frontend="both", host="0.0.0.0", port=9900)

    source = build_runtime_command(config, executable=Path("python.exe"), frozen=False)
    frozen = build_runtime_command(config, executable=Path("TAO Launcher.exe"), frozen=True)

    assert source.argv[:4] == ("python.exe", "-m", "tam.run", "--deploy")
    assert "--no-menu" in source.argv
    assert source.env["TAM_SUPERVISED"] == "1"
    assert frozen.argv[:3] == ("TAO Launcher.exe", "--runtime", "--deploy")
    assert frozen.argv[-2:] == ("--no-menu", "--no-doctor")


def test_port_availability_detects_bound_port() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    try:
        assert not is_port_available("127.0.0.1", port)
    finally:
        sock.close()
    assert is_port_available("127.0.0.1", port)


def test_process_controller_streams_logs_and_stops(tmp_path: Path) -> None:
    logs: list[tuple[str, str]] = []
    states: list[RuntimeState] = []
    controller = ProcessController(
        log_callback=lambda stream, line: logs.append((stream, line)),
        state_callback=states.append,
    )
    command = (
        sys.executable,
        "-u",
        "-c",
        "import time; print('ready', flush=True); time.sleep(30)",
    )

    controller.start(command, cwd=tmp_path)
    _wait_until(lambda: controller.state == RuntimeState.RUNNING)
    _wait_until(lambda: ("stdout", "ready") in logs)

    assert controller.pid is not None
    assert controller.stop(timeout=2.0)
    _wait_until(lambda: controller.state == RuntimeState.STOPPED)
    assert RuntimeState.STARTING in states
    assert RuntimeState.RUNNING in states
    assert RuntimeState.STOPPING in states


def test_process_controller_records_failed_exit(tmp_path: Path) -> None:
    controller = ProcessController()
    controller.start((sys.executable, "-c", "raise SystemExit(7)"), cwd=tmp_path)
    _wait_until(lambda: controller.state == RuntimeState.FAILED)
    assert controller.exit_code == 7


def test_process_controller_restarts_on_hot_reload_exit_code(tmp_path: Path) -> None:
    marker = tmp_path / "restart.marker"
    logs: list[tuple[str, str]] = []
    controller = ProcessController(log_callback=lambda stream, line: logs.append((stream, line)))
    script = (
        "import pathlib,time; p=pathlib.Path(r'" + str(marker) + "'); "
        "first=not p.exists(); p.touch(); "
        "__import__('sys').exit(75) if first else time.sleep(30)"
    )

    first_pid = controller.start((sys.executable, "-c", script), cwd=tmp_path)
    _wait_until(lambda: controller.pid is not None and controller.pid != first_pid)

    assert controller.state == RuntimeState.RUNNING
    assert ("launcher", "收到热重载请求，正在重新启动 TAO 服务…") in logs
    controller.stop(timeout=2.0)
