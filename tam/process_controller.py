"""Cross-platform process supervision used by the desktop launcher."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .release_config import ReleaseConfig


class RuntimeState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(frozen=True)
class RuntimeCommand:
    argv: tuple[str, ...]
    env: dict[str, str]


def build_runtime_command(
    config: ReleaseConfig,
    *,
    executable: Path | None = None,
    frozen: bool | None = None,
) -> RuntimeCommand:
    executable = executable or Path(sys.executable)
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    prefix = (str(executable), "--runtime") if frozen else (str(executable), "-m", "tam.run")
    host = config.host or ("0.0.0.0" if config.deploy == "server" else "127.0.0.1")
    argv = prefix + (
        "--deploy",
        config.deploy,
        "--frontend",
        config.frontend,
        "--host",
        host,
        "--port",
        str(config.port),
        "--no-menu",
        "--no-doctor",
    )
    env = os.environ.copy()
    env.update(config.to_env())
    env["PYTHONUNBUFFERED"] = "1"
    return RuntimeCommand(argv=argv, env=env)


def is_port_available(host: str, port: int) -> bool:
    bind_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.bind((bind_host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


LogCallback = Callable[[str, str], None]
StateCallback = Callable[[RuntimeState], None]


class ProcessController:
    def __init__(
        self,
        *,
        log_callback: LogCallback | None = None,
        state_callback: StateCallback | None = None,
    ) -> None:
        self.log_callback = log_callback or (lambda _stream, _line: None)
        self.state_callback = state_callback or (lambda _state: None)
        self._process: subprocess.Popen[str] | None = None
        self._state = RuntimeState.STOPPED
        self._exit_code: int | None = None
        self._expected_stop = False
        self._lock = threading.RLock()

    @property
    def state(self) -> RuntimeState:
        with self._lock:
            return self._state

    @property
    def exit_code(self) -> int | None:
        with self._lock:
            return self._exit_code

    @property
    def pid(self) -> int | None:
        with self._lock:
            return self._process.pid if self._process and self._process.poll() is None else None

    def _set_state(self, state: RuntimeState) -> None:
        with self._lock:
            if self._state == state:
                return
            self._state = state
        self.state_callback(state)

    def start(
        self,
        command: RuntimeCommand | Iterable[str],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> int:
        with self._lock:
            if self._process and self._process.poll() is None:
                raise RuntimeError("TAO runtime is already running")
            self._expected_stop = False
            self._exit_code = None
        self._set_state(RuntimeState.STARTING)

        if isinstance(command, RuntimeCommand):
            argv = command.argv
            child_env = command.env.copy()
            if env:
                child_env.update(env)
        else:
            argv = tuple(str(part) for part in command)
            child_env = os.environ.copy()
            if env:
                child_env.update(env)

        creationflags = 0
        startupinfo = None
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            process = subprocess.Popen(
                argv,
                cwd=str(Path(cwd)) if cwd is not None else None,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
        except Exception:
            self._set_state(RuntimeState.FAILED)
            raise

        with self._lock:
            self._process = process
        self._set_state(RuntimeState.RUNNING)
        self._start_reader(process.stdout, "stdout")
        self._start_reader(process.stderr, "stderr")
        threading.Thread(target=self._watch_process, args=(process,), daemon=True).start()
        return process.pid

    def _start_reader(self, stream, name: str) -> None:
        if stream is None:
            return

        def reader() -> None:
            for line in iter(stream.readline, ""):
                self.log_callback(name, line.rstrip("\r\n"))
            stream.close()

        threading.Thread(target=reader, daemon=True).start()

    def _watch_process(self, process: subprocess.Popen[str]) -> None:
        code = process.wait()
        with self._lock:
            if self._process is not process:
                return
            self._exit_code = code
            expected = self._expected_stop
            self._process = None
        self._set_state(RuntimeState.STOPPED if expected or code == 0 else RuntimeState.FAILED)

    def stop(self, *, timeout: float = 5.0) -> bool:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                self._process = None
                self._set_state(RuntimeState.STOPPED)
                return True
            self._expected_stop = True
        self._set_state(RuntimeState.STOPPING)
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=max(timeout, 1.0))
        with self._lock:
            self._exit_code = process.returncode
            if self._process is process:
                self._process = None
        self._set_state(RuntimeState.STOPPED)
        return True
