"""Shared configuration model for desktop and headless release workflows."""
from __future__ import annotations

import base64
import os
import re
import secrets
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class ConfigIssue:
    field: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class ReleaseConfig:
    deploy: str = "local"
    frontend: str = "web"
    host: str = ""
    port: int = 8848
    api_id: str = "0"
    api_hash: str = ""
    bot_token: str = ""
    bot_admin_id: str = ""
    web_token: str = ""
    master_key: str = ""
    default_proxy: str = ""
    data_dir: str = "./data"
    workers: int = 4
    batch_concurrency: int = 3
    log_level: str = "info"
    no_auth: bool = False

    def to_env(self) -> dict[str, str]:
        return {
            "TAM_DEPLOY": self.deploy,
            "TAM_FRONTEND": self.frontend,
            "TAM_HOST": self.host,
            "TAM_PORT": str(self.port),
            "TAM_API_ID": self.api_id,
            "TAM_API_HASH": self.api_hash,
            "TAM_BOT_TOKEN": self.bot_token,
            "TAM_BOT_ADMIN_ID": self.bot_admin_id,
            "TAM_WEB_TOKEN": self.web_token,
            "TAM_MASTER_KEY": self.master_key,
            "TAM_DEFAULT_PROXY": self.default_proxy,
            "TAM_DATA_DIR": self.data_dir,
            "TAM_WORKERS": str(self.workers),
            "TAM_BATCH_CONCURRENCY": str(self.batch_concurrency),
            "TAM_LOG_LEVEL": self.log_level,
            "TAM_NO_AUTH": "1" if self.no_auth else "0",
        }


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif value.startswith("#"):
            value = ""
        else:
            value = re.sub(r"\s+#.*$", "", value).strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def _as_int(value: str | None, default: int) -> int:
    try:
        return int((value or "").strip())
    except ValueError:
        return default


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_release_config(env_path: str | os.PathLike[str] = ".env") -> ReleaseConfig:
    values = _parse_env(Path(env_path))
    return ReleaseConfig(
        deploy=values.get("TAM_DEPLOY", "local") or "local",
        frontend=values.get("TAM_FRONTEND", "web") or "web",
        host=values.get("TAM_HOST", ""),
        port=_as_int(values.get("TAM_PORT"), 8848),
        api_id=values.get("TAM_API_ID", "0") or "0",
        api_hash=values.get("TAM_API_HASH", ""),
        bot_token=values.get("TAM_BOT_TOKEN", values.get("BOT_TOKEN", "")),
        bot_admin_id=values.get("TAM_BOT_ADMIN_ID", values.get("ADMIN_ID", "")),
        web_token=values.get("TAM_WEB_TOKEN", ""),
        master_key=values.get("TAM_MASTER_KEY", ""),
        default_proxy=values.get("TAM_DEFAULT_PROXY", ""),
        data_dir=values.get("TAM_DATA_DIR", "./data") or "./data",
        workers=_as_int(values.get("TAM_WORKERS"), 4),
        batch_concurrency=_as_int(values.get("TAM_BATCH_CONCURRENCY"), 3),
        log_level=values.get("TAM_LOG_LEVEL", "info") or "info",
        no_auth=_as_bool(values.get("TAM_NO_AUTH")),
    )


def ensure_release_secrets(config: ReleaseConfig) -> ReleaseConfig:
    master_key = config.master_key or base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    web_token = config.web_token or secrets.token_urlsafe(24)
    if master_key == config.master_key and web_token == config.web_token:
        return config
    return replace(config, master_key=master_key, web_token=web_token)


def validate_release_config(config: ReleaseConfig) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []
    if config.deploy not in {"local", "server"}:
        issues.append(ConfigIssue("deploy", "部署模式只能是 local 或 server"))
    if config.frontend not in {"web", "bot", "both"}:
        issues.append(ConfigIssue("frontend", "前端模式只能是 web、bot 或 both"))
    if not 1 <= config.port <= 65535:
        issues.append(ConfigIssue("port", "端口必须在 1 到 65535 之间"))
    if config.api_id and config.api_id != "0" and not config.api_id.isdigit():
        issues.append(ConfigIssue("api_id", "API ID 必须是数字"))
    if config.workers < 1:
        issues.append(ConfigIssue("workers", "并发 worker 至少为 1"))
    if config.batch_concurrency < 1:
        issues.append(ConfigIssue("batch_concurrency", "批量并发至少为 1"))
    if config.log_level.lower() not in {"debug", "info", "warning", "error", "critical"}:
        issues.append(ConfigIssue("log_level", "日志级别无效"))
    if config.frontend in {"bot", "both"} and not config.bot_token:
        issues.append(ConfigIssue("bot_token", "Bot 模式需要 TAM_BOT_TOKEN"))
    if config.deploy == "server":
        if config.no_auth:
            issues.append(ConfigIssue("no_auth", "服务器模式必须启用访问令牌"))
        if not config.web_token:
            issues.append(ConfigIssue("web_token", "服务器模式需要访问令牌"))
        if config.host in {"127.0.0.1", "localhost"}:
            issues.append(ConfigIssue("host", "服务器模式当前只监听本机", "warning"))
    if not config.master_key:
        issues.append(ConfigIssue("master_key", "缺少主密钥"))
    if not config.data_dir.strip():
        issues.append(ConfigIssue("data_dir", "数据目录不能为空"))
    return issues


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}***{value[-4:]}"


def _render_env(existing_text: str, updates: dict[str, str]) -> str:
    lines = existing_text.splitlines()
    written: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            output.append(line)
    if output and output[-1].strip():
        output.append("")
    for key, value in updates.items():
        if key not in written:
            output.append(f"{key}={value}")
    return "\n".join(output).rstrip() + "\n"


def save_release_config(
    config: ReleaseConfig,
    env_path: str | os.PathLike[str] = ".env",
) -> Path:
    return update_env_values(config.to_env(), env_path, backup=True)


def update_env_values(
    updates: dict[str, str],
    env_path: str | os.PathLike[str] = ".env",
    *,
    backup: bool = False,
) -> Path:
    path = Path(env_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    rendered = _render_env(existing, updates)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return path
