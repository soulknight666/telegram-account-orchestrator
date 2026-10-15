"""统一、可机器读取的错误码。

这个模块只负责把已有异常/结果补充结构化字段，不改变旧的 ``detail``、
``error`` 文本。未知的服务器异常对客户端使用通用文案，原始堆栈只写服务端日志。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


ERROR_CODES = frozenset({
    "auth_required", "readonly_mode", "not_found", "validation_error",
    "conflict", "internal_error", "session_missing", "session_invalid",
    "account_banned", "account_deleted", "account_restricted", "account_locked",
    "twofa_required", "twofa_wrong", "phone_invalid", "code_timeout", "code_invalid",
    "code_link_missing", "flood_wait", "proxy_error", "network_timeout", "dc_migrate",
    "import_bad_archive", "import_unknown_format", "import_duplicate", "tdata_passcode",
    "op_unknown", "op_param_missing", "task_cancelled", "peer_invalid",
    "privacy_restricted",
})


@dataclass
class ErrorInfo:
    code: str
    detail: Any
    params: dict[str, Any] | None = None
    retry_after: int | None = None

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {"detail": self.detail, "code": self.code}
        if self.params:
            out["params"] = self.params
        if self.retry_after is not None and self.code == "flood_wait":
            out["retry_after"] = max(0, int(self.retry_after))
        return out


class ApiError(Exception):
    """可直接由 API/任务抛出的结构化异常。"""

    def __init__(self, code: str, detail: Any, *, status: int = 400,
                 params: dict[str, Any] | None = None,
                 retry_after: int | None = None):
        self.code = code if code in ERROR_CODES else "internal_error"
        self.detail = detail
        self.status = int(status)
        self.params = params
        self.retry_after = retry_after
        super().__init__(detail if isinstance(detail, str) else str(detail))

    def info(self) -> ErrorInfo:
        return ErrorInfo(self.code, self.detail, self.params, self.retry_after)


_TYPE_CODES = {
    "AuthKeyUnregisteredError": "session_invalid",
    "SessionRevokedError": "session_invalid",
    "UserDeactivatedBanError": "account_banned",
    "UserDeactivatedError": "account_deleted",
    "UserRestrictedError": "account_restricted",
    "SessionPasswordNeededError": "twofa_required",
    "PasswordHashInvalidError": "twofa_wrong",
    "PhoneNumberInvalidError": "phone_invalid",
    "PhoneCodeExpiredError": "code_timeout",
    "PhoneCodeInvalidError": "code_invalid",
    "CodeInvalidError": "code_invalid",
    "FloodWaitError": "flood_wait",
    "FloodTestPhoneWaitError": "flood_wait",
    "ProxyError": "proxy_error",
    "ProxyConnectionError": "proxy_error",
    "ProxyTimeoutError": "proxy_error",
    "TimeoutError": "network_timeout",
    "ConnectionError": "network_timeout",
    "ConnectError": "network_timeout",
    "DcIdInvalidError": "dc_migrate",
    "InvalidDCError": "dc_migrate",
    "PeerIdInvalidError": "peer_invalid",
    "UsernameNotOccupiedError": "peer_invalid",
    "ChannelPrivateError": "privacy_restricted",
    "ChatWriteForbiddenError": "privacy_restricted",
}


def _chain(exc: BaseException):
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        cur = cur.__cause__ or cur.__context__


def _flood_seconds(exc: BaseException) -> int | None:
    for item in _chain(exc):
        value = getattr(item, "seconds", None)
        if value is None:
            value = getattr(item, "value", None)
        try:
            if value is not None:
                return max(0, int(value))
        except (TypeError, ValueError):
            pass
        match = re.search(r"(?:FLOOD_WAIT|A_WAIT|wait)[ _-]?(\d+)", str(item), re.I)
        if match:
            return int(match.group(1))
    return None


def telethon_error_to_code(exc: BaseException) -> str:
    """按异常类名/稳定 RPC 标识映射，不依赖具体 Telethon 版本。"""
    for item in _chain(exc):
        name = type(item).__name__
        if name in _TYPE_CODES:
            return _TYPE_CODES[name]
        upper = str(item).upper()
        if "FLOOD_WAIT" in upper:
            return "flood_wait"
        if "AUTH_KEY_UNREGISTERED" in upper or "SESSION_REVOKED" in upper:
            return "session_invalid"
        if "USER_DEACTIVATED_BAN" in upper:
            return "account_banned"
        if "USER_DEACTIVATED" in upper:
            return "account_deleted"
        if "SESSION_PASSWORD_NEEDED" in upper:
            return "twofa_required"
        if "PASSWORD_HASH_INVALID" in upper:
            return "twofa_wrong"
    return "internal_error"


def code_for_exception(exc: BaseException, *, status: int | None = None) -> str:
    if isinstance(exc, ApiError):
        return exc.code
    declared = getattr(exc, "code", None)
    if isinstance(declared, str) and declared in ERROR_CODES:
        return declared
    mapped = telethon_error_to_code(exc)
    if mapped != "internal_error":
        return mapped
    # API 旧代码会把底层异常包装为 RuntimeError/中文提示；按稳定关键词补齐
    # 机器码，文本本身仍由调用方原样返回。
    text = " ".join(str(item) for item in _chain(exc))
    if "账号不存在" in text or "not found" in text.lower():
        return "not_found"
    if "没有登录态" in text or "会话已清除" in text or "尚未登录" in text:
        return "session_missing"
    if "会话已失效" in text or "AUTH_KEY_UNREGISTERED" in text.upper() or "SESSION_REVOKED" in text.upper():
        return "session_invalid"
    if "代理" in text and ("失败" in text or "错误" in text or "timeout" in text.lower()):
        return "proxy_error"
    if "锁定" in text:
        return "account_locked"
    if "二验" in text and ("错误" in text or "密码" in text):
        return "twofa_wrong"
    if "连接 Telegram 失败" in text or "超时" in text or "timeout" in text.lower():
        return "network_timeout"
    status_map = {401: "auth_required", 404: "not_found", 409: "conflict",
                  422: "validation_error", 429: "flood_wait"}
    return status_map.get(status or 0, f"http_{status}" if status and status not in (500,) else "internal_error")


def error_payload(exc: BaseException, *, status: int | None = None,
                  detail: Any | None = None,
                  params: dict[str, Any] | None = None) -> dict[str, Any]:
    """生成不含 HTTP 状态码的统一 body；旧 detail 文本由调用方传入时原样保留。"""
    code = code_for_exception(exc, status=status)
    if isinstance(exc, ApiError):
        info = exc.info()
        if params and not info.params:
            info.params = params
        return info.payload()
    declared = getattr(exc, "code", None)
    declared_params = getattr(exc, "params", None)
    value = detail if detail is not None else str(exc)
    retry = _flood_seconds(exc) if code == "flood_wait" else None
    out = ErrorInfo(code, value, params or declared_params, retry).payload()
    return out


def enrich_failure(item: dict[str, Any], exc: BaseException | None = None,
                   *, code: str | None = None,
                   params: dict[str, Any] | None = None) -> dict[str, Any]:
    """复制批量失败项并补 code/可选参数，保留既有 error/detail 字段。"""
    out = dict(item)
    if code is None and exc is not None:
        code = code_for_exception(exc)
    out["code"] = code if code in ERROR_CODES else (code or "internal_error")
    if params:
        out.setdefault("params", params)
    if out["code"] == "flood_wait" and "retry_after" not in out and exc is not None:
        retry = _flood_seconds(exc)
        if retry is not None:
            out["retry_after"] = retry
    return out


def normalize_reason_code(code: str | None, reason: str = "") -> str:
    """将历史预览 reason_code 归一到公开错误码词表。"""
    aliases = {
        "not_authorized": "session_invalid",
        "not_healthy": "network_timeout",
        "not_runnable": "validation_error",
        "missing": "not_found",
    }
    value = aliases.get((code or "").strip(), (code or "").strip())
    if value in ERROR_CODES:
        return value
    text = str(reason).lower()
    if "锁定" in str(reason):
        return "account_locked"
    if "会话" in str(reason) or "未登录" in str(reason):
        return "session_invalid"
    if "不存在" in str(reason):
        return "not_found"
    if "代理" in str(reason):
        return "proxy_error"
    return "internal_error"
