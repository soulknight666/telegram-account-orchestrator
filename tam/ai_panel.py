"""网页 AI 对话面板：策略、权限与 OpenAI 兼容对话（带工具调用）。"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .tools import HUMAN_ONLY, ToolContext, call_tool, list_tools
from .tools import _REGISTRY as TOOLS

SETTING_KEY = "ai_panel_config"

PRESETS: dict[str, str] = {
    "readonly": "只读：仅查询/检查类工具",
    "safe": "安全：只读 + 改元数据/预览导入（无对外副作用）",
    "standard": "标准：多数运维能力（发消息/踢设备/改二验等），不含注销账号与退出登录",
    "full": "完整：已注册工具全部允许（仍排除 HUMAN_ONLY；含注销/退出登录）",
    "custom": "自定义：按下方开关逐项配置",
}

# write 里相对「安全」的（不直接对外发消息/踢设备/删号）
SAFE_WRITE = {
    "add_account", "update_account", "import_accounts", "preview_import", "spam_check",
    "mark_read", "twofa_reset_cancel", "update_settings",
}

# 「标准」预设仍排除的不可逆/极高危工具（完整预设才开）
STANDARD_EXCLUDE = {
    "delete_tg_account",  # 真正注销 Telegram 账号
    "logout_session",     # 作废当前 session
    "delete_account",     # 删本地条目
}


# 简短有效的系统提示词预设（可在面板一键套用）
PROMPT_PRESETS: dict[str, str] = {
    "ops": (
        "你是 TAM 运维助手。只用已授权工具查改本机托管号；禁止编造工具结果。"
        "先查再改；危险操作先说明风险。简体中文，一两段说清。"
    ),
    "readonly": (
        "你是只读顾问。仅用查询/检查类工具；不建议、不执行任何写入。"
        "结论基于工具返回。简体中文，条目化。"
    ),
    "batch": (
        "你是批量操作助手。先 list/stats 摸清状态，再按用户范围执行已授权工具。"
        "汇报：成功数/失败数/失败原因。简体中文，短句。"
    ),
    "troubleshoot": (
        "你是排障助手。根据健康检查、日志、设备列表定位问题，给出可执行的下一步。"
        "不猜测会话密钥。简体中文。"
    ),
}

PROMPT_PRESET_LABELS: dict[str, str] = {
    "ops": "运维（默认）",
    "readonly": "只读顾问",
    "batch": "批量执行",
    "troubleshoot": "排障",
}



PROVIDER_META: dict[str, dict[str, str]] = {
    "openai_compatible": {
        "label": "OpenAI 兼容（Chat Completions）",
        "hint": "OpenAI / DeepSeek / Grok / Moonshot / 通义 / 智谱 / Ollama / OneAPI / NewAPI 等",
        "default_base": "https://api.openai.com/v1",
    },
    "anthropic": {
        "label": "Anthropic Claude（Messages）",
        "hint": "官方 api.anthropic.com，或兼容 Claude Messages 的中转",
        "default_base": "https://api.anthropic.com",
    },
    "gemini": {
        "label": "Google Gemini（generateContent）",
        "hint": "Google AI Studio / Gemini API",
        "default_base": "https://generativelanguage.googleapis.com/v1beta",
    },
    "azure_openai": {
        "label": "Azure OpenAI",
        "hint": "Base 填到资源端点，模型名填部署名",
        "default_base": "https://YOUR_RESOURCE.openai.azure.com",
    },
}

def default_config() -> dict[str, Any]:
    tools = {name: (spec["danger"] == "read") for name, spec in TOOLS.items()}
    return {
        "enabled": False,
        "preset": "readonly",
        "provider": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
        "prompt_preset": "ops",
        "system_prompt": PROMPT_PRESETS["ops"],
        "max_tool_rounds": 6,
        "require_confirm_destructive": True,
        "allow_account_ids": [],  # 空=不限制
        "temperature": 0.2,
        "auto_compress": True,
        "context_keep_recent": 8,   # 保留最近若干条完整消息
        "context_max_chars": 14000,  # 超出则压缩更早内容
        "tools": tools,
        "updated_at": 0.0,
    }


def _preset_flag(name: str, danger: str, preset: str) -> bool:
    """按预设决定某个工具默认是否开启。"""
    if name in HUMAN_ONLY:
        return False
    if preset == "readonly":
        return danger == "read"
    if preset == "safe":
        return danger == "read" or name in SAFE_WRITE
    if preset == "standard":
        return name not in STANDARD_EXCLUDE
    if preset == "full":
        return True
    return danger == "read"  # custom 新增工具：默认只开只读


def _apply_preset(cfg: dict[str, Any], preset: str) -> dict[str, Any]:
    tools = dict(cfg.get("tools") or {})
    for name, spec in TOOLS.items():
        tools[name] = _preset_flag(name, spec.get("danger") or "read", preset)
    cfg["tools"] = tools
    cfg["preset"] = preset
    return cfg


def load_config(db) -> dict[str, Any]:
    raw = db.get_setting(SETTING_KEY)
    base = default_config()
    if not raw:
        return base
    try:
        data = json.loads(raw)
    except Exception:
        return base
    if not isinstance(data, dict):
        return base
    base.update({k: v for k, v in data.items() if k in base or k == "tools"})
    # 合并升级后新增的工具：按当前预设决定默认开/关（custom 则只开只读）
    tools = dict(base.get("tools") or {})
    preset = str(base.get("preset") or "readonly")
    changed = False
    for name, spec in TOOLS.items():
        if name not in tools:
            tools[name] = _preset_flag(name, spec.get("danger") or "read", preset)
            changed = True
    # 清掉已删除工具的残留开关
    for name in list(tools.keys()):
        if name not in TOOLS:
            tools.pop(name, None)
            changed = True
    base["tools"] = tools
    if "enabled" in base:
        base["enabled"] = base["enabled"] in (True, 1, "1", "true", "True", "yes")
    return base


def save_config(db, cfg: dict[str, Any]) -> dict[str, Any]:
    cur = load_config(db)
    # api_key：空字符串表示不改；显式 null 可清空（前端用 clear_api_key）
    if cfg.get("clear_api_key"):
        cur["api_key"] = ""
    elif "api_key" in cfg and cfg["api_key"]:
        cur["api_key"] = str(cfg["api_key"]).strip()

    for k in (
        "enabled", "provider", "base_url", "model", "system_prompt", "prompt_preset",
        "max_tool_rounds", "require_confirm_destructive", "allow_account_ids",
        "temperature", "auto_compress", "context_keep_recent", "context_max_chars",
    ):
        if k in cfg:
            cur[k] = cfg[k]
    # 若只选了提示词预设名且未手写 system_prompt，套用文案
    if cfg.get("prompt_preset") in PROMPT_PRESETS and "system_prompt" not in cfg:
        cur["system_prompt"] = PROMPT_PRESETS[cfg["prompt_preset"]]
        cur["prompt_preset"] = cfg["prompt_preset"]

    if "tools" in cfg and isinstance(cfg["tools"], dict):
        merged = dict(cur.get("tools") or {})
        for name in TOOLS:
            if name in cfg["tools"]:
                merged[name] = bool(cfg["tools"][name])
        cur["tools"] = merged
        cur["preset"] = "custom"
    elif "preset" in cfg and cfg["preset"] in ("readonly", "safe", "standard", "full"):
        cur = _apply_preset(cur, cfg["preset"])
    elif "preset" in cfg and cfg["preset"] == "custom":
        cur["preset"] = "custom"

    cur["max_tool_rounds"] = max(1, min(int(cur.get("max_tool_rounds") or 6), 12))
    try:
        cur["temperature"] = max(0.0, min(float(cur.get("temperature") or 0.2), 2.0))
    except (TypeError, ValueError):
        cur["temperature"] = 0.2
    if not isinstance(cur.get("allow_account_ids"), list):
        cur["allow_account_ids"] = []
    else:
        ids = []
        for x in cur["allow_account_ids"]:
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                pass
        cur["allow_account_ids"] = ids
    # 显式归一化：避免字符串 "false" 被 bool() 当成 True
    def _as_bool(v: Any, default: bool = False) -> bool:
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        s = str(v).strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off", ""):
            return False
        return default

    cur["enabled"] = _as_bool(cur.get("enabled"), False)
    cur["require_confirm_destructive"] = _as_bool(
        cur.get("require_confirm_destructive"), True)
    cur["auto_compress"] = _as_bool(cur.get("auto_compress"), True)
    try:
        cur["context_keep_recent"] = max(2, min(int(cur.get("context_keep_recent") or 8), 80))
    except (TypeError, ValueError):
        cur["context_keep_recent"] = 8
    try:
        cur["context_max_chars"] = max(2000, min(int(cur.get("context_max_chars") or 14000), 200000))
    except (TypeError, ValueError):
        cur["context_max_chars"] = 14000
    cur["updated_at"] = time.time()
    db.set_setting(SETTING_KEY, json.dumps(cur, ensure_ascii=False))
    return cur


def public_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """返回给前端：隐藏 api_key 明文。"""
    out = dict(cfg)
    key = out.get("api_key") or ""
    out["api_key_set"] = bool(key)
    out["api_key"] = ""
    if key:
        out["api_key_masked"] = (key[:3] + "…" + key[-4:]) if len(key) > 8 else "****"
    else:
        out["api_key_masked"] = ""
    out["presets"] = PRESETS
    out["catalog"] = tool_catalog()
    out["prompt_presets"] = {
        k: {"label": PROMPT_PRESET_LABELS.get(k, k), "text": v}
        for k, v in PROMPT_PRESETS.items()
    }
    out["providers"] = PROVIDER_META
    return out


def tool_catalog() -> list[dict[str, Any]]:
    rows = []
    for name, spec in TOOLS.items():
        rows.append({
            "name": name,
            "description": spec.get("description") or "",
            "danger": spec.get("danger") or "read",
            "human_only": name in HUMAN_ONLY,
        })
    order = {"read": 0, "write": 1, "destructive": 2}
    rows.sort(key=lambda r: (order.get(r["danger"], 9), r["name"]))
    return rows


def allowed_tools(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    flags = cfg.get("tools") or {}
    out = []
    for t in list_tools(include_danger=True, readonly=False):
        name = t["name"]
        if name in HUMAN_ONLY:
            continue
        if not flags.get(name, False):
            continue
        out.append(t)
    return out



def _tool_params(t: dict[str, Any]) -> dict[str, Any]:
    schema = t.get("inputSchema") or t.get("parameters") or {"type": "object", "properties": {}}
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    if "type" not in schema:
        schema = {"type": "object", **schema}
    return schema


def _openai_tools(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    tools = []
    for t in allowed_tools(cfg):
        tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description") or t["name"],
                "parameters": _tool_params(t),
            },
        })
    return tools


def _anthropic_tools(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": t["name"],
            "description": t.get("description") or t["name"],
            "input_schema": _tool_params(t),
        }
        for t in allowed_tools(cfg)
    ]


def _gemini_function_decls(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": t["name"],
            "description": t.get("description") or t["name"],
            "parameters": _tool_params(t),
        }
        for t in allowed_tools(cfg)
    ]


def _detect_provider(cfg: dict[str, Any]) -> str:
    p = (cfg.get("provider") or "openai_compatible").strip().lower().replace("-", "_")
    aliases = {
        "openai": "openai_compatible",
        "openai_compatible": "openai_compatible",
        "compatible": "openai_compatible",
        "claude": "anthropic",
        "anthropic": "anthropic",
        "gemini": "gemini",
        "google": "gemini",
        "azure": "azure_openai",
        "azure_openai": "azure_openai",
    }
    p = aliases.get(p, p)
    if p in PROVIDER_META:
        return p
    base = (cfg.get("base_url") or "").lower()
    if "anthropic" in base:
        return "anthropic"
    if "generativelanguage.googleapis" in base or "/v1beta" in base and "google" in base:
        return "gemini"
    if "openai.azure.com" in base or "cognitive.microsoft" in base:
        return "azure_openai"
    return "openai_compatible"


def _http_json(
    url: str,
    payload: dict[str, Any] | None,
    *,
    headers: dict[str, str],
    method: str = "POST",
    timeout: float = 120.0,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"LLM HTTP {exc.code}: {err}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"LLM 请求失败：{exc}") from exc



def _normalize_base(base: str, provider: str) -> str:
    b = (base or "").strip().rstrip("/")
    if not b:
        b = PROVIDER_META.get(provider, {}).get("default_base", "https://api.openai.com/v1")
    return b.rstrip("/")


def _call_openai_compatible(
    *,
    base: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    temperature: float,
    azure: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    """返回 (assistant_text, tool_calls_as_openai_shape)."""
    if azure:
        # base: https://{resource}.openai.azure.com
        # deployment = model name
        url = f"{base}/openai/deployments/{model}/chat/completions?api-version=2024-02-15-preview"
        headers = {"api-key": api_key}
        payload: dict[str, Any] = {"messages": messages, "temperature": temperature}
    else:
        # 允许 base 已含 /v1 或用户只填到 host
        if base.endswith("/v1"):
            url = f"{base}/chat/completions"
        elif base.endswith("/chat/completions"):
            url = base
        else:
            url = f"{base}/chat/completions"
            # 许多中转要 /v1
            if "/v1/" not in url and not url.endswith("/v1/chat/completions"):
                if not base.endswith("/v1"):
                    url = f"{base}/v1/chat/completions" if "/v1" not in base else f"{base}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {"model": model, "messages": messages, "temperature": temperature}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    data = _http_json(url, payload, headers=headers)
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    if isinstance(content, list):
        # 部分兼容实现 content 为分段
        content = "".join(
            (c.get("text") or "") if isinstance(c, dict) else str(c) for c in content
        )
    tool_calls = msg.get("tool_calls") or []
    # 旧版 function_call
    if not tool_calls and msg.get("function_call"):
        fc = msg["function_call"]
        tool_calls = [{
            "id": "call_0",
            "type": "function",
            "function": {"name": fc.get("name"), "arguments": fc.get("arguments") or "{}"},
        }]
    return str(content or ""), tool_calls


def _call_anthropic(
    *,
    base: str,
    api_key: str,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    temperature: float,
) -> tuple[str, list[dict[str, Any]]]:
    url = f"{base.rstrip('/')}/v1/messages"
    if base.rstrip("/").endswith("/v1"):
        url = f"{base.rstrip('/')}/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Authorization": f"Bearer {api_key}",  # 部分中转只认 Bearer
    }
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 4096,
        "temperature": temperature,
        "messages": messages,
    }
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = tools
    data = _http_json(url, payload, headers=headers)
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in data.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text_parts.append(block.get("text") or "")
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": block.get("id") or f"toolu_{len(tool_calls)}",
                "type": "function",
                "function": {
                    "name": block.get("name"),
                    "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                },
            })
    return "".join(text_parts), tool_calls


def _call_gemini(
    *,
    base: str,
    api_key: str,
    model: str,
    system: str,
    contents: list[dict[str, Any]],
    function_decls: list[dict[str, Any]],
    temperature: float,
) -> tuple[str, list[dict[str, Any]]]:
    # base: https://generativelanguage.googleapis.com/v1beta
    model_id = model if model.startswith("models/") else model
    url = f"{base.rstrip('/')}/models/{model_id}:generateContent?key={api_key}"
    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"temperature": temperature},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    if function_decls:
        payload["tools"] = [{"function_declarations": function_decls}]
    data = _http_json(url, payload, headers={})
    cands = data.get("candidates") or [{}]
    parts = ((cands[0].get("content") or {}).get("parts")) or []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for i, part in enumerate(parts):
        if not isinstance(part, dict):
            continue
        if part.get("text"):
            text_parts.append(part["text"])
        fc = part.get("functionCall") or part.get("function_call")
        if fc:
            args = fc.get("args") or fc.get("arguments") or {}
            if isinstance(args, str):
                arg_s = args
            else:
                arg_s = json.dumps(args, ensure_ascii=False)
            tool_calls.append({
                "id": f"gem_{i}",
                "type": "function",
                "function": {"name": fc.get("name"), "arguments": arg_s},
            })
    return "".join(text_parts), tool_calls


def _msgs_to_anthropic(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system = ""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            system = (system + "\n" + str(m.get("content") or "")).strip()
            continue
        if role == "tool":
            # Anthropic: tool_result in user message
            out.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id") or "",
                    "content": str(m.get("content") or "")[:20000],
                }],
            })
            continue
        if role == "assistant":
            content_blocks: list[dict[str, Any]] = []
            if m.get("content"):
                content_blocks.append({"type": "text", "text": str(m["content"])})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    inp = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    inp = {}
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id") or fn.get("name") or "tool",
                    "name": fn.get("name"),
                    "input": inp if isinstance(inp, dict) else {},
                })
            out.append({"role": "assistant", "content": content_blocks or [{"type": "text", "text": ""}]})
            continue
        if role == "user":
            out.append({"role": "user", "content": str(m.get("content") or "")})
    # Anthropic 要求 user/assistant 严格交替
    merged: list[dict[str, Any]] = []
    for m in out:
        if merged and merged[-1]["role"] == m["role"]:
            prev, cur = merged[-1]["content"], m["content"]
            if isinstance(prev, str) and isinstance(cur, str):
                merged[-1]["content"] = (prev + "\n" + cur).strip()
            elif isinstance(prev, list) and isinstance(cur, list):
                merged[-1]["content"] = prev + cur
            elif isinstance(prev, list) and isinstance(cur, str):
                merged[-1]["content"] = prev + [{"type": "text", "text": cur}]
            elif isinstance(prev, str) and isinstance(cur, list):
                merged[-1]["content"] = [{"type": "text", "text": prev}] + cur
            else:
                merged.append(m)
        else:
            merged.append(m)
    if merged and merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": "(continue)"})
    return system, merged


def _msgs_to_gemini(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system = ""
    contents: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            system = (system + "\n" + str(m.get("content") or "")).strip()
            continue
        if role == "tool":
            # function response
            try:
                resp = json.loads(m.get("content") or "{}")
            except Exception:
                resp = {"result": m.get("content")}
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": m.get("name") or m.get("tool_call_id") or "tool",
                        "response": resp if isinstance(resp, dict) else {"result": resp},
                    }
                }],
            })
            continue
        if role == "assistant":
            parts: list[dict[str, Any]] = []
            if m.get("content"):
                parts.append({"text": str(m["content"])})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                parts.append({"functionCall": {"name": fn.get("name"), "args": args}})
            contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            continue
        if role == "user":
            contents.append({"role": "user", "parts": [{"text": str(m.get("content") or "")}]})
    return system, contents



def _check_account_scope(cfg: dict[str, Any], args: dict[str, Any]) -> str | None:
    """若配置了 allow_account_ids，则限制工具只能操作这些账号。"""
    allow = cfg.get("allow_account_ids") or []
    if not allow:
        return None
    allow_set: set[int] = set()
    for x in allow:
        try:
            allow_set.add(int(x))
        except (TypeError, ValueError):
            pass
    if not allow_set:
        return None
    ids: list[int] = []
    if args.get("account_id") is not None:
        try:
            ids.append(int(args["account_id"]))
        except (TypeError, ValueError):
            pass
    if isinstance(args.get("account_ids"), list):
        for x in args["account_ids"]:
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                pass
    for i in ids:
        if i not in allow_set:
            return f"账号 {i} 不在 AI 允许范围 {sorted(allow_set)}"
    return None



def _msg_chars(messages: list[dict[str, Any]]) -> int:
    n = 0
    for m in messages:
        n += len(str(m.get("content") or ""))
        for tc in m.get("tool_calls") or []:
            fn = (tc.get("function") or {})
            n += len(str(fn.get("arguments") or "")) + len(str(fn.get("name") or ""))
    return n


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 12)].rstrip() + "…(已截断)"


def _summarize_old_messages(old: list[dict[str, Any]], *, budget: int = 2500) -> str:
    """本地压缩：不额外请求 LLM，提取角色与要点。"""
    lines: list[str] = ["[对话摘要·自动压缩]"]
    per = max(80, budget // max(1, len(old)))
    for m in old:
        role = m.get("role") or "?"
        if role == "system":
            continue
        if role == "tool":
            name = m.get("name") or m.get("tool_call_id") or "tool"
            body = _clip(str(m.get("content") or ""), min(per, 400))
            lines.append(f"- 工具 {name}: {body}")
            continue
        content = str(m.get("content") or "").strip()
        if not content and m.get("tool_calls"):
            names = []
            for tc in m.get("tool_calls") or []:
                names.append(((tc.get("function") or {}).get("name")) or "?")
            content = "调用工具 " + ", ".join(names)
        tag = "用户" if role == "user" else ("助手" if role == "assistant" else role)
        lines.append(f"- {tag}: {_clip(content, per)}")
    out = "\n".join(lines)
    return _clip(out, budget)


def compress_messages(
    messages: list[dict[str, Any]],
    *,
    keep_recent: int = 8,
    max_chars: int = 14000,
    force: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """压缩对话上下文：保留 system + 最近消息，更早内容收成一条摘要。

    返回 (新消息列表, 元信息)。
    """
    if not messages:
        return messages, {"compressed": False}

    system_msgs = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    # 单条过长先截断
    trimmed: list[dict[str, Any]] = []
    for m in rest:
        mm = dict(m)
        if mm.get("role") == "tool":
            mm["content"] = _clip(str(mm.get("content") or ""), 3500)
        else:
            mm["content"] = _clip(str(mm.get("content") or ""), 8000)
        if mm.get("tool_calls"):
            tcs = []
            for tc in mm["tool_calls"]:
                tc2 = dict(tc)
                fn = dict(tc2.get("function") or {})
                fn["arguments"] = _clip(str(fn.get("arguments") or ""), 2000)
                tc2["function"] = fn
                tcs.append(tc2)
            mm["tool_calls"] = tcs
        trimmed.append(mm)

    total = _msg_chars(system_msgs + trimmed)
    meta: dict[str, Any] = {
        "compressed": False,
        "before_chars": total,
        "before_msgs": len(system_msgs) + len(trimmed),
    }
    keep_recent = max(2, int(keep_recent))
    max_chars = max(2000, int(max_chars))

    need = force or total > max_chars or len(trimmed) > keep_recent + 4
    if not need:
        meta["after_chars"] = total
        meta["after_msgs"] = meta["before_msgs"]
        return system_msgs + trimmed, meta

    if len(trimmed) <= keep_recent:
        # 仅截断后仍超长：再砍 content
        keep = trimmed
        old: list[dict[str, Any]] = []
    else:
        old = trimmed[:-keep_recent]
        keep = trimmed[-keep_recent:]

    summary = _summarize_old_messages(old, budget=min(3000, max_chars // 3)) if old else ""
    out: list[dict[str, Any]] = list(system_msgs)
    if summary:
        out.append({
            "role": "user",
            "content": summary + "\n\n(以上为更早对话的压缩摘要，请结合后续完整消息继续)",
        })
        # 占位 assistant 以免部分上游对 user 连发敏感
        out.append({
            "role": "assistant",
            "content": "已了解此前摘要，继续根据最新消息处理。",
        })
    out.extend(keep)

    # 仍超长则继续丢掉 keep 的最前部
    while len(out) > len(system_msgs) + 2 and _msg_chars(out) > max_chars:
        # 删掉摘要后的第一条 keep
        # structure: system* + optional summary pair + keep
        idx = len(system_msgs) + (2 if summary else 0)
        if idx < len(out):
            out.pop(idx)
        else:
            break

    meta.update({
        "compressed": True,
        "dropped": len(old),
        "after_chars": _msg_chars(out),
        "after_msgs": len(out),
    })
    return out, meta


async def run_chat(
    *,
    db,
    settings,
    manager,
    user_messages: list[dict[str, Any]],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """多轮工具调用对话，支持 OpenAI 兼容 / Anthropic / Gemini / Azure。"""
    cfg = cfg or load_config(db)
    if not cfg.get("enabled"):
        return {"ok": False, "error": "AI 面板未启用，请先在配置里打开开关"}
    api_key = (cfg.get("api_key") or "").strip()
    if not api_key:
        return {"ok": False, "error": "未配置 API Key"}

    provider = _detect_provider(cfg)
    base = _normalize_base(cfg.get("base_url") or "", provider)
    model = (cfg.get("model") or "gpt-4o-mini").strip()
    temperature = float(cfg.get("temperature") or 0.2)

    tools_oai = _openai_tools(cfg)
    tools_ant = _anthropic_tools(cfg)
    tools_gem = _gemini_function_decls(cfg)
    if not tools_oai:
        return {"ok": False, "error": "当前权限下没有任何可用工具，请在权限里至少开启一项"}

    system = (cfg.get("system_prompt") or "").strip() or default_config()["system_prompt"]
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in user_messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant", "system"):
            continue
        messages.append({"role": role, "content": str(m.get("content") or "")[:12000]})

    auto_compress = bool(cfg.get("auto_compress", True))
    keep_recent = int(cfg.get("context_keep_recent") or 8)
    max_chars = int(cfg.get("context_max_chars") or 14000)
    compress_meta: dict[str, Any] = {"compressed": False}
    if auto_compress:
        messages, compress_meta = compress_messages(
            messages, keep_recent=keep_recent, max_chars=max_chars,
        )

    ctx = ToolContext(settings, db, manager, readonly=False)
    trace: list[dict[str, Any]] = []
    max_rounds = int(cfg.get("max_tool_rounds") or 6)
    flags = cfg.get("tools") or {}
    require_confirm = bool(cfg.get("require_confirm_destructive", True))

    import asyncio

    final_text = ""
    for _ in range(max_rounds + 1):
        if auto_compress:
            messages, round_meta = compress_messages(
                messages, keep_recent=keep_recent, max_chars=max_chars,
            )
            if round_meta.get("compressed"):
                compress_meta = round_meta
        if provider == "anthropic":
            sys_a, msgs_a = _msgs_to_anthropic(messages)
            content, tool_calls = await asyncio.to_thread(
                _call_anthropic,
                base=base, api_key=api_key, model=model,
                system=sys_a or system, messages=msgs_a,
                tools=tools_ant, temperature=temperature,
            )
        elif provider == "gemini":
            sys_g, contents = _msgs_to_gemini(messages)
            content, tool_calls = await asyncio.to_thread(
                _call_gemini,
                base=base, api_key=api_key, model=model,
                system=sys_g or system, contents=contents,
                function_decls=tools_gem, temperature=temperature,
            )
        elif provider == "azure_openai":
            content, tool_calls = await asyncio.to_thread(
                _call_openai_compatible,
                base=base, api_key=api_key, model=model,
                messages=messages, tools=tools_oai,
                temperature=temperature, azure=True,
            )
        else:
            content, tool_calls = await asyncio.to_thread(
                _call_openai_compatible,
                base=base, api_key=api_key, model=model,
                messages=messages, tools=tools_oai,
                temperature=temperature, azure=False,
            )

        if content:
            final_text = content
        if not tool_calls:
            messages.append({"role": "assistant", "content": content or ""})
            break

        messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": tool_calls,
        })
        for tc in tool_calls:
            fn = (tc.get("function") or {})
            name = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}

            if name not in TOOLS or name in HUMAN_ONLY or not flags.get(name, False):
                result: dict[str, Any] = {
                    "ok": False,
                    "error": {"code": "forbidden", "message": f"工具未授权：{name}"},
                }
            else:
                scope_err = _check_account_scope(cfg, args)
                if scope_err:
                    result = {"ok": False, "error": {"code": "forbidden", "message": scope_err}}
                else:
                    danger = TOOLS[name]["danger"]
                    if require_confirm and danger == "destructive" and not args.get("confirm"):
                        args = dict(args)
                        args["confirm"] = True
                    result = await call_tool(ctx, name, args)

            trace.append({"tool": name, "arguments": args, "result": result})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or name,
                "name": name,
                "content": json.dumps(result, ensure_ascii=False, default=str)[:20000],
            })
    else:
        if not final_text:
            final_text = "（达到最大工具轮次，已停止）"

    return {
        "ok": True,
        "provider": provider,
        "message": {"role": "assistant", "content": final_text or "（无文本回复）"},
        "trace": trace,
        "tools_available": [t["function"]["name"] for t in tools_oai],
        "context": compress_meta,
    }
