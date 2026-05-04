import re
import os
from typing import Optional

DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+\*",
    r"dd\s+if=.*of=.*bs=",
    r":\(\)\{.*:\|.*:&.*\}",
    r"mkfs\.",
    r"格式化",
    r"del\s+/[fqs]\s+",
    r"Remove-Item\s+-Recurse\s+-Force",
    r"shutdown",
    r"restart\s+",
    r"pkill\s+",
    r"killall\s+",
    r"eval\s+",
    r"base64\s+-d\s+.*\|",
    r"curl\s+.*\|\s*sh",
    r"wget\s+.*\|\s*sh",
]

SENSITIVE_PATTERNS = [
    (r"(?i)(password|密码)\s*[=:]\s*[\"']?([^\s\"'<>]+)[\"']?", r"\1=***REDACTED***"),
    (r"(?i)(api[_-]?key|api[_-]?token)\s*[=:]\s*[\"']?([^\s\"'<>]+)[\"']?", r"\1=***REDACTED***"),
    (r"(?i)(secret|密钥)\s*[=:]\s*[\"']?([^\s\"'<>]+)[\"']?", r"\1=***REDACTED***"),
    (r"(?i)(bearer|token)\s+[^\s]+", r"\1 ***REDACTED***"),
    (r"([A-Za-z0-9+/]{40,}={0,2})", r"***REDACTED_KEY***"),
]

ALLOWED_ENV_VARS = {
    "PATH", "HOME", "USER", "SHELL", "PWD", "TERM",
    "LANG", "LC_ALL", "EDITOR", "VISUAL", "PAGER",
}

BLOCKED_PATHS = {
    "/dev/zero", "/dev/random", "/dev/stdin", "/dev/stdout", "/dev/stderr",
    "/etc/passwd", "/etc/shadow", "/boot/", "/System/", "/Windows/System32/",
    "/proc/self/", "/proc/root/", "/sys/kernel",
}

SENSITIVE_PREFIXES = ("/etc/", "/root/", "/var/log/", "/var/run/", "/.ssh/")

MAX_CONSECUTIVE_READS = 3

_read_history = {}
_read_timestamps = {}


def is_path_allowed(path: str) -> bool:
    if not path:
        return False
    path = os.path.abspath(path)
    for blocked in BLOCKED_PATHS:
        if path.startswith(blocked):
            return False
    for prefix in SENSITIVE_PREFIXES:
        if path.startswith(prefix):
            return False
    return True


def check_consecutive_reads(path: str) -> bool:
    global _read_history
    path = os.path.abspath(path)
    count = _read_history.get(path, 0) + 1
    _read_history[path] = count
    return count <= MAX_CONSECUTIVE_READS


def reset_read_history(path: str = None):
    global _read_history, _read_timestamps
    if path:
        path = os.path.abspath(path)
        _read_history.pop(path, None)
        _read_timestamps.pop(path, None)
    else:
        _read_history.clear()
        _read_timestamps.clear()


def is_dangerous_command(command: str) -> bool:
    command_lower = command.lower()
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command_lower, re.IGNORECASE):
            return True
    if re.search(r"\|", command):
        parts = command.split("|")
        for part in parts:
            if is_dangerous_command(part.strip()):
                return True
    if re.search(r";\s*\w+", command):
        parts = command.split(";")
        for part in parts:
            if is_dangerous_command(part.strip()):
                return True
    if re.search(r"&&\s*\w+", command):
        parts = re.split(r"&&", command)
        for part in parts:
            if is_dangerous_command(part.strip()):
                return True
    if re.search(r"\$\(.*\)", command):
        return True
    if re.search(r"`.*`", command):
        return True
    return False


def redact_sensitive_text(text: str) -> str:
    if not text:
        return text
    result = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        result = re.sub(pattern, replacement, result)
    env_vars = os.environ.keys()
    for env_var in env_vars:
        if env_var not in ALLOWED_ENV_VARS:
            pattern = rf"(?i){env_var}=([^\s\"'<>]+)"
            replacement = f"{env_var}=***REDACTED***"
            result = re.sub(pattern, replacement, result)
    return result
