"""Tkinter desktop launcher for Telegram Account Orchestrator releases."""
from __future__ import annotations

import json
import os
import sys
import webbrowser
from dataclasses import fields, replace
from pathlib import Path
from typing import Mapping

from .process_controller import (
    ProcessController,
    RuntimeState,
    build_runtime_command,
    is_port_available,
)
from .release_config import (
    ReleaseConfig,
    ensure_release_secrets,
    load_release_config,
    mask_secret,
    save_release_config,
    validate_release_config,
)

APP_NAME = "Telegram Account Orchestrator"
SHORT_NAME = "TAO"
SECRET_FIELDS = {"api_hash", "bot_token", "web_token", "master_key"}


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative


def icon_path() -> Path:
    bundled = resource_path("tam/assets/tao-icon.png")
    if bundled.exists():
        return bundled
    return Path(__file__).resolve().parent / "assets" / "tao-icon.png"


def display_fields(config: ReleaseConfig, *, reveal_secrets: bool = False) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in fields(config):
        value = getattr(config, field.name)
        if isinstance(value, bool):
            text = "1" if value else "0"
        else:
            text = str(value)
        if field.name in SECRET_FIELDS and not reveal_secrets:
            text = mask_secret(text)
        values[field.name] = text
    return values


def build_config_from_fields(values: Mapping[str, str], base: ReleaseConfig) -> ReleaseConfig:
    updates: dict[str, object] = {}
    masked = display_fields(base)
    for field in fields(base):
        name = field.name
        raw = str(values.get(name, getattr(base, name))).strip()
        if name in SECRET_FIELDS and raw == masked[name]:
            updates[name] = getattr(base, name)
        elif name in {"port", "workers", "batch_concurrency"}:
            try:
                updates[name] = int(raw)
            except ValueError:
                updates[name] = 0
        elif name == "no_auth":
            updates[name] = raw.lower() in {"1", "true", "yes", "on"}
        else:
            updates[name] = raw
    return replace(base, **updates)


def web_console_url(config: ReleaseConfig) -> str:
    host = config.host or ("0.0.0.0" if config.deploy == "server" else "127.0.0.1")
    shown = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{shown}:{config.port}"


def smoke_test_payload() -> dict[str, object]:
    import tkinter  # noqa: F401 - validates packaged Tcl/Tk support

    return {
        "app": APP_NAME,
        "short_name": SHORT_NAME,
        "tkinter": True,
        "icon_exists": icon_path().exists(),
        "frozen": bool(getattr(sys, "frozen", False)),
    }


class LauncherApp:
    def __init__(self, env_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.env_path = env_path or Path(os.getenv("TAM_ENV_FILE", ".env"))
        self.config = ensure_release_secrets(load_release_config(self.env_path))
        self.controller = ProcessController(
            log_callback=self._on_log_thread,
            state_callback=self._on_state_thread,
        )

        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} ({SHORT_NAME})")
        self.root.geometry("1040x700")
        self.root.minsize(900, 620)
        self.root.configure(bg="#f4f6fa")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._window_icon = None
        try:
            self._window_icon = tk.PhotoImage(file=str(icon_path()))
            self.root.iconphoto(True, self._window_icon)
        except tk.TclError:
            pass

        self.variables: dict[str, tk.StringVar] = {
            name: tk.StringVar(value=value) for name, value in display_fields(self.config).items()
        }
        self.status_var = tk.StringVar(value="已就绪")
        self.address_var = tk.StringVar(value=web_console_url(self.config))
        self.pages: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.log_text = None
        self.start_button = None
        self.stop_button = None
        self._build_style()
        self._build_layout()
        self.show_page("overview")
        self._refresh_actions(RuntimeState.STOPPED)

    def _build_style(self) -> None:
        style = self.ttk.Style(self.root)
        try:
            style.theme_use("vista" if os.name == "nt" else "clam")
        except self.tk.TclError:
            pass
        style.configure("TLabel", background="#f4f6fa", foreground="#172033", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 20), background="#f4f6fa")
        style.configure("Section.TLabel", font=("Segoe UI Semibold", 12), background="#f4f6fa")
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 10))

    def _build_layout(self) -> None:
        sidebar = self.tk.Frame(self.root, bg="#111827", width=205)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = self.tk.Frame(sidebar, bg="#111827")
        brand.pack(fill="x", padx=18, pady=(22, 28))
        if self._window_icon is not None:
            self.tk.Label(brand, image=self._window_icon, bg="#111827", width=72, height=48).pack(anchor="w")
        self.tk.Label(brand, text="TAO", bg="#111827", fg="white", font=("Segoe UI Semibold", 22)).pack(anchor="w")
        self.tk.Label(
            brand,
            text="Telegram Account\nOrchestrator",
            bg="#111827",
            fg="#aeb8ca",
            justify="left",
            font=("Segoe UI", 9),
        ).pack(anchor="w")

        for key, text in (
            ("overview", "概览"),
            ("basic", "基础配置"),
            ("advanced", "高级选项"),
            ("logs", "运行日志"),
        ):
            button = self.tk.Button(
                sidebar,
                text=text,
                command=lambda page=key: self.show_page(page),
                bg="#111827",
                fg="#d8deea",
                activebackground="#253149",
                activeforeground="white",
                bd=0,
                padx=20,
                pady=11,
                anchor="w",
                font=("Segoe UI", 10),
            )
            button.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = button

        self.container = self.tk.Frame(self.root, bg="#f4f6fa")
        self.container.pack(side="left", fill="both", expand=True, padx=28, pady=24)
        self._build_overview()
        self._build_basic()
        self._build_advanced()
        self._build_logs()

    def _new_page(self, key: str, title: str, subtitle: str) -> object:
        frame = self.tk.Frame(self.container, bg="#f4f6fa")
        self.ttk.Label(frame, text=title, style="Title.TLabel").pack(anchor="w")
        self.ttk.Label(frame, text=subtitle).pack(anchor="w", pady=(3, 20))
        self.pages[key] = frame
        return frame

    def _build_overview(self) -> None:
        page = self._new_page("overview", "服务概览", "配置、启动并管理 TAO 服务")
        card = self.tk.Frame(page, bg="white", highlightbackground="#dce2eb", highlightthickness=1)
        card.pack(fill="x", pady=(0, 18), ipady=12)
        self.tk.Label(card, text="运行状态", bg="white", fg="#667085", font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=18, pady=(12, 2))
        self.tk.Label(card, textvariable=self.status_var, bg="white", fg="#16803b", font=("Segoe UI Semibold", 15)).grid(row=1, column=0, sticky="w", padx=18)
        self.tk.Label(card, text="Web 地址", bg="white", fg="#667085", font=("Segoe UI", 9)).grid(row=0, column=1, sticky="w", padx=18, pady=(12, 2))
        self.tk.Label(card, textvariable=self.address_var, bg="white", fg="#1f4d8f", font=("Segoe UI", 11)).grid(row=1, column=1, sticky="w", padx=18)
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=2)

        actions = self.tk.Frame(page, bg="#f4f6fa")
        actions.pack(fill="x", pady=(0, 18))
        self.start_button = self.ttk.Button(actions, text="启动服务", style="Primary.TButton", command=self.start_service)
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = self.ttk.Button(actions, text="停止服务", command=self.stop_service)
        self.stop_button.pack(side="left", padx=8)
        self.ttk.Button(actions, text="保存配置", command=self.save_config).pack(side="left", padx=8)
        self.ttk.Button(actions, text="打开 Web 控制台", command=self.open_console).pack(side="left", padx=8)

        self.ttk.Label(page, text="快速检查", style="Section.TLabel").pack(anchor="w", pady=(4, 8))
        self.check_text = self.tk.Text(page, height=13, bg="#111827", fg="#d1fae5", insertbackground="white", relief="flat", padx=12, pady=10, font=("Consolas", 9))
        self.check_text.pack(fill="both", expand=True)
        self._render_check_summary()

    def _entry_row(self, parent, row: int, label: str, name: str, *, secret: bool = False, values: tuple[str, ...] | None = None) -> None:
        self.ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=8)
        if values:
            widget = self.ttk.Combobox(parent, textvariable=self.variables[name], values=values, state="readonly")
        else:
            widget = self.ttk.Entry(parent, textvariable=self.variables[name], show="*" if secret else "")
        widget.grid(row=row, column=1, sticky="ew", pady=8)

    def _build_basic(self) -> None:
        page = self._new_page("basic", "基础配置", "设置部署位置、前端和 Telegram 凭据")
        form = self.tk.Frame(page, bg="#f4f6fa")
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        rows = [
            ("部署模式", "deploy", False, ("local", "server")),
            ("前端模式", "frontend", False, ("web", "bot", "both")),
            ("监听地址", "host", False, None),
            ("端口", "port", False, None),
            ("Telegram API ID", "api_id", False, None),
            ("Telegram API Hash", "api_hash", True, None),
            ("Bot Token", "bot_token", True, None),
            ("Web 访问令牌", "web_token", True, None),
            ("默认代理", "default_proxy", False, None),
        ]
        for index, (label, name, secret, values) in enumerate(rows):
            self._entry_row(form, index, label, name, secret=secret, values=values)
        self.ttk.Button(page, text="保存基础配置", command=self.save_config).pack(anchor="e", pady=18)

    def _build_advanced(self) -> None:
        page = self._new_page("advanced", "高级选项", "调整数据目录、并发和日志行为")
        form = self.tk.Frame(page, bg="#f4f6fa")
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        rows = [
            ("数据目录", "data_dir", False, None),
            ("Worker 数", "workers", False, None),
            ("批量并发", "batch_concurrency", False, None),
            ("日志级别", "log_level", False, ("debug", "info", "warning", "error", "critical")),
            ("免令牌模式（0/1）", "no_auth", False, ("0", "1")),
            ("主密钥", "master_key", True, None),
            ("Bot 管理员 ID", "bot_admin_id", False, None),
        ]
        for index, (label, name, secret, values) in enumerate(rows):
            self._entry_row(form, index, label, name, secret=secret, values=values)
        self.ttk.Button(page, text="保存高级配置", command=self.save_config).pack(anchor="e", pady=18)

    def _build_logs(self) -> None:
        page = self._new_page("logs", "运行日志", "查看 TAO 后台运行输出")
        bar = self.tk.Frame(page, bg="#f4f6fa")
        bar.pack(fill="x", pady=(0, 8))
        self.ttk.Button(bar, text="清空显示", command=lambda: self.log_text.delete("1.0", "end")).pack(side="left")
        self.log_text = self.tk.Text(page, bg="#0b1220", fg="#d6e4ff", insertbackground="white", relief="flat", padx=12, pady=10, font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

    def show_page(self, key: str) -> None:
        for frame in self.pages.values():
            frame.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        for name, button in self.nav_buttons.items():
            button.configure(bg="#253149" if name == key else "#111827", fg="white" if name == key else "#d8deea")

    def _current_config(self) -> ReleaseConfig:
        values = {name: variable.get() for name, variable in self.variables.items()}
        return build_config_from_fields(values, self.config)

    def _sync_variables(self) -> None:
        for name, value in display_fields(self.config).items():
            self.variables[name].set(value)
        self.address_var.set(web_console_url(self.config))

    def save_config(self, *, quiet: bool = False) -> bool:
        config = ensure_release_secrets(self._current_config())
        errors = [issue for issue in validate_release_config(config) if issue.severity == "error"]
        if errors:
            message = "\n".join(f"• {issue.message}" for issue in errors)
            self.messagebox.showerror("配置有误", message)
            return False
        save_release_config(config, self.env_path)
        self.config = config
        self._sync_variables()
        self._render_check_summary()
        if not quiet:
            self.messagebox.showinfo("配置已保存", f"配置已写入：\n{self.env_path.resolve()}")
        return True

    def _render_check_summary(self) -> None:
        config = self._current_config()
        issues = validate_release_config(config)
        lines = [f"配置文件: {self.env_path.resolve()}", f"模式: {config.deploy} / {config.frontend}", f"地址: {web_console_url(config)}", ""]
        if issues:
            for issue in issues:
                lines.append(f"[{issue.severity.upper()}] {issue.field}: {issue.message}")
        else:
            lines.append("[OK] 配置检查通过")
        self.check_text.configure(state="normal")
        self.check_text.delete("1.0", "end")
        self.check_text.insert("end", "\n".join(lines))
        self.check_text.configure(state="disabled")

    def start_service(self) -> None:
        if not self.save_config(quiet=True):
            return
        host = self.config.host or ("0.0.0.0" if self.config.deploy == "server" else "127.0.0.1")
        if self.config.frontend in {"web", "both"} and not is_port_available(host, self.config.port):
            self.messagebox.showerror("端口被占用", f"端口 {self.config.port} 已被占用，请修改后重试。")
            return
        try:
            command = build_runtime_command(self.config)
            self.controller.start(command, cwd=self.env_path.resolve().parent)
            self._append_log("launcher", "TAO 服务正在启动…")
        except Exception as exc:  # noqa: BLE001 - converted to user-facing dialog
            self.messagebox.showerror("启动失败", str(exc))

    def stop_service(self) -> None:
        self.controller.stop()

    def open_console(self) -> None:
        webbrowser.open(web_console_url(self.config))

    def _on_log_thread(self, stream: str, line: str) -> None:
        self.root.after(0, self._append_log, stream, line)

    def _append_log(self, stream: str, line: str) -> None:
        if self.log_text is None:
            return
        self.log_text.insert("end", f"[{stream}] {line}\n")
        self.log_text.see("end")

    def _on_state_thread(self, state: RuntimeState) -> None:
        self.root.after(0, self._refresh_actions, state)

    def _refresh_actions(self, state: RuntimeState) -> None:
        labels = {
            RuntimeState.STOPPED: "已停止",
            RuntimeState.STARTING: "正在启动",
            RuntimeState.RUNNING: "运行中",
            RuntimeState.STOPPING: "正在停止",
            RuntimeState.FAILED: f"运行失败（退出码 {self.controller.exit_code}）",
        }
        self.status_var.set(labels[state])
        running = state in {RuntimeState.STARTING, RuntimeState.RUNNING, RuntimeState.STOPPING}
        if self.start_button is not None:
            self.start_button.configure(state="disabled" if running else "normal")
        if self.stop_button is not None:
            self.stop_button.configure(state="normal" if running else "disabled")

    def _on_close(self) -> None:
        if self.controller.state in {RuntimeState.STARTING, RuntimeState.RUNNING, RuntimeState.STOPPING}:
            if not self.messagebox.askyesno("退出 TAO", "后台服务仍在运行，是否停止服务并退出？"):
                return
            self.controller.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--runtime":
        from .run import main as runtime_main

        runtime_main(args[1:])
        return 0
    if args == ["--smoke-test"]:
        print(json.dumps(smoke_test_payload(), ensure_ascii=False, sort_keys=True))
        return 0
    env_path = Path(os.getenv("TAM_ENV_FILE", ".env"))
    LauncherApp(env_path).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
