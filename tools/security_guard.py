"""安全守卫工具 - Security Guard Tools

提供集中式安全策略管理，在工具调用前自动进行安全检查。
"""

import re
from typing import Tuple, Any

from tools.registry import registry
from tools.utils import (
    is_path_allowed,
    is_dangerous_command,
    redact_sensitive_text,
    BLOCKED_PATHS,
    SENSITIVE_PREFIXES
)

# URL 白名单/黑名单
SAFE_URL_PREFIXES = (
    "http://127.0.0.1",
    "http://localhost",
    "https://github.com",
    "https://api.github.com",
    "https://huggingface.co",
    "https://api-inference.huggingface.co",
    "https://replicate.com",
)
DANGEROUS_URL_PATTERNS = (
    re.compile(r"^file://"),
    re.compile(r"^ftp://"),
    re.compile(r"^telnet://"),
)


def is_url_safe(url: str) -> bool:
    """检查 URL 是否安全

    Args:
        url: 要检查的 URL

    Returns:
        True 表示 URL 安全，False 表示危险
    """
    url_lower = url.lower()

    # 检查 URL 是否是危险协议
    for pattern in DANGEROUS_URL_PATTERNS:
        if pattern.match(url_lower):
            return False

    # 检查 URL 是否在白名单中
    for safe_prefix in SAFE_URL_PREFIXES:
        if url_lower.startswith(safe_prefix.lower()):
            return True

    # 默认允许 HTTP/HTTPS URL
    if url_lower.startswith("http://") or url_lower.startswith("https://"):
        return True

    # 其他 URL 视为不安全
    return False


def redact_secrets(text: str) -> str:
    """脱敏敏感信息（API key、token 等）

    Args:
        text: 要脱敏的文本

    Returns:
        脱敏后的文本
    """
    return redact_sensitive_text(text)


def guard_check_before_dispatch(tool_name: str, args: dict) -> Tuple[bool, str]:
    """在 registry.dispatch 前调用的守卫检查

    Args:
        tool_name: 工具名称
        args: 工具参数

    Returns:
        (allowed: bool, reason: str)
    """
    # 检查文件工具路径安全
    if tool_name in ("read_file", "write_file", "patch_file", "search_files"):
        path = args.get("path") or args.get("file_path") or args.get("root_dir")
        if path and not is_path_allowed(path):
            return False, f"Path '{path}' is blocked by security policy"

    # 检查终端工具命令安全
    elif tool_name in ("run_shell_command", "execute_code"):
        command = args.get("command") or args.get("code")
        if command and tool_name == "run_shell_command" and is_dangerous_command(command):
            return False, "Command is identified as potentially dangerous"
        if command and tool_name == "execute_code":
            language = args.get("language", "python")
            if language == "shell" and is_dangerous_command(command):
                return False, "Command is identified as potentially dangerous"

    # 检查浏览器 URL 安全
    elif tool_name in ("browser_navigate"):
        url = args.get("url")
        if url and not is_url_safe(url):
            return False, f"URL '{url}' is blocked by security policy"

    # 检查 Docker 命令安全
    elif tool_name == "docker_run":
        command = args.get("command", "")
        if "privileged" in str(command).lower():
            return False, "Privileged container execution is blocked"
        if is_dangerous_command(command):
            return False, "Command is identified as potentially dangerous"

    # 所有检查通过
    return True, "Allowed by security policy"


def register_all():
    """注册安全守卫相关工具（可选）"""
    # Security guard is primarily an internal utility used before dispatch
    # No tools need to be exposed by default
    pass
