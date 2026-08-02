"""Terminal-first setup workflow for Linux servers without a desktop."""
from __future__ import annotations

import argparse
import html
import secrets
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from .release_config import (
    ReleaseConfig,
    ensure_release_secrets,
    load_release_config,
    mask_secret,
    save_release_config,
    validate_release_config,
)


@dataclass
class OneTimeSetupToken:
    token: str
    expires_at: float
    used: bool = False

    @classmethod
    def create(
        cls,
        *,
        now: float | None = None,
        lifetime: float = 900.0,
        token: str | None = None,
    ) -> "OneTimeSetupToken":
        current = time.time() if now is None else now
        return cls(token=token or secrets.token_urlsafe(32), expires_at=current + lifetime)

    def verify(self, candidate: str, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        return not self.used and current <= self.expires_at and secrets.compare_digest(candidate, self.token)

    def consume(self, candidate: str, *, now: float | None = None) -> bool:
        if not self.verify(candidate, now=now):
            return False
        self.used = True
        return True


def setup_bind_address(port: int = 8849) -> tuple[str, int]:
    if not 1 <= port <= 65535:
        raise ValueError("setup port must be between 1 and 65535")
    return "127.0.0.1", port


def build_ssh_tunnel_command(user: str, host: str, port: int = 8849) -> str:
    setup_bind_address(port)
    return f"ssh -L {port}:127.0.0.1:{port} {user}@{host}"


def apply_setup_values(config: ReleaseConfig, values: Mapping[str, str]) -> ReleaseConfig:
    updates: dict[str, object] = {}
    for name, raw_value in values.items():
        if not hasattr(config, name) or raw_value is None:
            continue
        raw = str(raw_value).strip()
        if name in {"port", "workers", "batch_concurrency"}:
            try:
                updates[name] = int(raw)
            except ValueError:
                updates[name] = 0
        elif name == "no_auth":
            updates[name] = raw.lower() in {"1", "true", "yes", "on"}
        else:
            updates[name] = raw
    return replace(config, **updates)


def write_headless_config(config: ReleaseConfig, env_path: Path) -> ReleaseConfig:
    ready = ensure_release_secrets(config)
    errors = [issue for issue in validate_release_config(ready) if issue.severity == "error"]
    if errors:
        raise ValueError("; ".join(issue.message for issue in errors))
    save_release_config(ready, env_path)
    return ready


def _ask(label: str, default: str, *, secret: bool = False) -> str:
    shown = mask_secret(default) if secret else default
    suffix = f" [{shown}]" if shown else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def terminal_setup(config: ReleaseConfig) -> ReleaseConfig:
    print("\nTAO Linux 无桌面配置向导")
    print("直接回车保留括号中的当前值。\n")
    values = {
        "deploy": _ask("部署模式 local/server", config.deploy),
        "frontend": _ask("前端 web/bot/both", config.frontend),
        "host": _ask("监听地址（留空自动选择）", config.host),
        "port": _ask("Web 端口", str(config.port)),
        "api_id": _ask("Telegram API ID", config.api_id),
        "api_hash": _ask("Telegram API Hash", config.api_hash, secret=True),
        "bot_token": _ask("Bot Token（Web 模式可留空）", config.bot_token, secret=True),
        "web_token": _ask("Web 访问令牌（留空自动生成）", config.web_token, secret=True),
        "default_proxy": _ask("默认代理（可留空）", config.default_proxy),
        "data_dir": _ask("数据目录", config.data_dir),
        "workers": _ask("Worker 数", str(config.workers)),
        "batch_concurrency": _ask("批量并发", str(config.batch_concurrency)),
        "log_level": _ask("日志级别", config.log_level),
        "no_auth": _ask("免令牌模式 0/1", "1" if config.no_auth else "0"),
    }
    return apply_setup_values(config, values)


def _setup_page(config: ReleaseConfig) -> str:
    def value(name: str) -> str:
        return html.escape(str(getattr(config, name)))

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TAO Linux 配置</title><style>
body{{font:15px system-ui;background:#f4f6fa;color:#172033;margin:0}}main{{max-width:780px;margin:30px auto;background:white;padding:28px;border-radius:14px}}
h1{{margin-top:0}}label{{display:block;margin:13px 0 5px;font-weight:600}}input,select{{width:100%;box-sizing:border-box;padding:10px;border:1px solid #cbd5e1;border-radius:7px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:0 18px}}button{{margin-top:20px;padding:11px 18px;background:#2563eb;color:white;border:0;border-radius:7px;font-weight:700}}
pre{{white-space:pre-wrap;background:#111827;color:#d1fae5;padding:12px;border-radius:8px}}@media(max-width:700px){{.grid{{display:block}}}}
</style></head><body><main><h1>TAO Linux 配置</h1><p>此页面仅通过 SSH 隧道访问，保存后一次性令牌立即失效。</p>
<form id="form"><label>一次性令牌</label><input name="token" type="password" required>
<div class="grid"><div><label>部署模式</label><select name="deploy"><option>server</option><option>local</option></select></div>
<div><label>前端模式</label><select name="frontend"><option>web</option><option>bot</option><option>both</option></select></div></div>
<div class="grid"><div><label>监听地址</label><input name="host" value="{value('host')}"></div><div><label>端口</label><input name="port" value="{value('port')}"></div></div>
<div class="grid"><div><label>API ID</label><input name="api_id" value="{value('api_id')}"></div><div><label>API Hash</label><input name="api_hash" type="password"></div></div>
<label>Bot Token</label><input name="bot_token" type="password"><label>Web 访问令牌</label><input name="web_token" type="password">
<label>默认代理</label><input name="default_proxy" value="{value('default_proxy')}"><label>数据目录</label><input name="data_dir" value="{value('data_dir')}">
<button type="submit">保存配置</button></form><pre id="result">等待保存…</pre>
<script>document.getElementById('form').addEventListener('submit',async(e)=>{{e.preventDefault();const data=Object.fromEntries(new FormData(e.target));const r=await fetch('/save',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(data)}});document.getElementById('result').textContent=await r.text();}});</script>
</main></body></html>"""


def create_setup_app(config: ReleaseConfig, env_path: Path, session: OneTimeSetupToken, server_holder: dict):
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="TAO One-Time Setup", docs_url=None, redoc_url=None)

    @app.get("/", response_class=None)
    async def index():
        from fastapi.responses import HTMLResponse

        return HTMLResponse(_setup_page(config))

    @app.post("/save")
    async def save(payload: dict[str, str]):
        token = str(payload.pop("token", ""))
        if not session.consume(token):
            raise HTTPException(status_code=403, detail="一次性令牌无效或已过期")
        updated = apply_setup_values(config, payload)
        saved = write_headless_config(updated, env_path)
        server = server_holder.get("server")
        if server is not None:
            server.should_exit = True
        return {"ok": True, "env_file": str(env_path), "deploy": saved.deploy, "frontend": saved.frontend}

    return app


def run_setup_web(config: ReleaseConfig, env_path: Path, *, port: int = 8849, ssh_user: str = "user", ssh_host: str = "server") -> None:
    import uvicorn

    host, port = setup_bind_address(port)
    session = OneTimeSetupToken.create()
    holder: dict[str, object] = {}
    app = create_setup_app(config, env_path, session, holder)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    holder["server"] = server
    print("\n一次性高级配置已启动，仅监听服务器本机。")
    print("在你的电脑执行：")
    print(f"  {build_ssh_tunnel_command(ssh_user, ssh_host, port)}")
    print("然后打开：")
    print(f"  http://127.0.0.1:{port}")
    print("一次性令牌：")
    print(f"  {session.token}")
    print("令牌 15 分钟内有效，保存一次后立即失效。\n")
    server.run()


def add_setup_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--headless", action="store_true", help="使用无桌面终端配置流程")
    parser.add_argument("--non-interactive", action="store_true", help="只使用参数和默认值，不询问")
    parser.add_argument("--web", action="store_true", help="保存基础配置后启动一次性 Web 配置页")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--deploy", choices=("local", "server"))
    parser.add_argument("--frontend", choices=("web", "bot", "both"))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--api-id")
    parser.add_argument("--api-hash")
    parser.add_argument("--bot-token")
    parser.add_argument("--proxy")
    parser.add_argument("--data-dir")
    parser.add_argument("--ssh-user", default="user")
    parser.add_argument("--ssh-host", default="server")
    parser.add_argument("--setup-port", type=int, default=8849)


def run_headless_setup(args: argparse.Namespace) -> ReleaseConfig:
    env_path = Path(args.env_file)
    config = load_release_config(env_path)
    values = {
        "deploy": args.deploy,
        "frontend": args.frontend,
        "host": args.host,
        "port": str(args.port) if args.port is not None else None,
        "api_id": args.api_id,
        "api_hash": args.api_hash,
        "bot_token": args.bot_token,
        "default_proxy": args.proxy,
        "data_dir": args.data_dir,
    }
    config = apply_setup_values(config, {k: v for k, v in values.items() if v is not None})
    if not args.non_interactive and sys.stdin.isatty():
        config = terminal_setup(config)
    saved = write_headless_config(config, env_path)
    print(f"TAO 配置已保存：{env_path.resolve()}")
    print(f"Web 访问令牌：{saved.web_token}")
    if args.web:
        run_setup_web(saved, env_path, port=args.setup_port, ssh_user=args.ssh_user, ssh_host=args.ssh_host)
    return saved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tao-setup", description="TAO Linux 无桌面配置")
    add_setup_arguments(parser)
    args = parser.parse_args(argv)
    run_headless_setup(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
