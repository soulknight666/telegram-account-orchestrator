"""
统一异常体系
提供标准异常类和错误码,便于上层统一处理
"""


class TAMError(Exception):
    """TAM 基础异常"""
    def __init__(self, message: str, code: str = "internal_error"):
        super().__init__(message)
        self.message = message
        self.code = code


class AccountNotFoundError(TAMError):
    """账号不存在"""
    def __init__(self, account_id: int):
        super().__init__(f"账号 {account_id} 不存在", "account_not_found")
        self.account_id = account_id


class SessionError(TAMError):
    """会话相关错误"""
    def __init__(self, message: str, code: str = "session_error"):
        super().__init__(message, code)


class NetworkError(TAMError):
    """网络相关错误"""
    def __init__(self, message: str, code: str = "network_error"):
        super().__init__(message, code)


class AuthError(TAMError):
    """认证相关错误"""
    def __init__(self, message: str, code: str = "auth_error"):
        super().__init__(message, code)


class ValidationError(TAMError):
    """参数验证错误"""
    def __init__(self, message: str, field: str = ""):
        super().__init__(message, "validation_error")
        self.field = field


class ConfigError(TAMError):
    """配置错误"""
    def __init__(self, message: str):
        super().__init__(message, "config_error")


class ToolError(TAMError):
    """工具执行错误"""
    def __init__(self, message: str, tool_name: str = ""):
        super().__init__(message, "tool_error")
        self.tool_name = tool_name


def format_error(exc: Exception) -> dict:
    """
    将异常格式化为标准错误响应

    Returns:
        {"code": str, "message": str, "details": dict}
    """
    if isinstance(exc, TAMError):
        result = {"code": exc.code, "message": exc.message}
        if isinstance(exc, AccountNotFoundError):
            result["details"] = {"account_id": exc.account_id}
        elif isinstance(exc, ValidationError):
            result["details"] = {"field": exc.field}
        elif isinstance(exc, ToolError):
            result["details"] = {"tool_name": exc.tool_name}
        return result
    else:
        return {
            "code": "internal_error",
            "message": str(exc),
        }
