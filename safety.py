"""安全机制 — 危险命令拦截与审批 (类 Hermes approval 系统)."""
import re
import json
import time
from typing import Optional

# Windows 危险命令模式
DANGEROUS_COMMANDS = [
    # 关机/重启
    r"\bshutdown\b", r"\brestart\b", r"\bstop-computer\b", r"\brestart-computer\b",
    r"\blogoff\b", r"\bpoweroff\b",
    # 删除
    r"\bdel\b", r"\brmdir\b", r"\brm\s+-rf\b", r"\brm\s+-r\b", r"\brm\b",
    r"\bformat\b", r"\bdiskpart\b",
    # 系统修改
    r"\breboot\b", r"\binit\s+6\b", r"\binit\s+0\b",
    r"\breg\s+delete\b", r"\breg\s+add\b",
    r"\bbcdedit\b", r"\bbootrec\b",
    # 危险网络
    r"\bnet\s+user\b", r"\bnet\s+localgroup\b",
    r"\bwinrm\b", r"\bwmic\s+process\s+delete\b",
    r"\btaskkill\s+/f\b", r"\bkill\s+-9\b",
    # 高危 PowerShell
    r"\bremove-item\b", r"\brm\b", r"\bri\s+-", r"\bdel\s+-",
    r"\bclear-content\b", r"\bremove-variable\b",
    r"\bdisable\s+", r"\buninstall\b",
    # 清空/覆写
    r">\s+[A-Za-z]:\\[^\\]+\.[^\\]+$",  # 重定向到文件
]

COMPILED_DANGEROUS = [re.compile(p, re.IGNORECASE) for p in DANGEROUS_COMMANDS]

# 安全例外 — 这些看似危险但实际无害
SAFE_EXCEPTIONS = [
    r"\becho\s+shutdown\b",
    r"\bdel\s+/?/?$",  # del 不带参数
    r"\brm\s+/?/?$",
    r"\bdir\b", r"\bcd\b", r"\bcls\b", r"\bhelp\b",
]


def is_dangerous_command(command: str) -> tuple[bool, str]:
    """检查命令是否危险. 返回 (is_dangerous, reason)."""
    cmd_lower = command.strip().lower()

    # 安全检查例外
    for pattern in SAFE_EXCEPTIONS:
        if re.search(pattern, cmd_lower):
            return False, ""

    for pattern, compiled in zip(DANGEROUS_COMMANDS, COMPILED_DANGEROUS):
        if compiled.search(cmd_lower):
            # 二次验证：排除注释、echo 等
            if re.match(r'^(rem\s|echo\s|::|#)', cmd_lower):
                continue
            return True, f"高危命令: {pattern}"

    return False, ""


def execute_with_approval(command: str,
                          tool_executor,
                          approval_callback=None) -> str:
    """执行危险命令前请求审批.

    Args:
        command: shell 命令
        tool_executor: 执行工具的函数
        approval_callback: 审批回调，接收 (operation, details) 返回是否批准

    Returns:
        执行结果字符串
    """
    is_dangerous, reason = is_dangerous_command(command)
    if not is_dangerous:
        # 安全命令，直接执行
        return tool_executor(command)

    # 危险命令：请求审批
    print(f"  🔒 检测到高危命令: {command[:80]}...")
    print(f"  🔒 原因: {reason}")

    if approval_callback:
        approved = approval_callback(
            operation=f"执行 Shell 命令",
            details=f"命令: {command[:200]}",
        )
        if approved:
            print(f"  ✅ 已批准，执行中...")
            return tool_executor(command)
        else:
            return f"[已拒绝] 高危命令未获批准: {command[:80]}..."
    else:
        # 无审批回调，默认拒绝
        return (f"[安全拦截] 高危命令已被自动拦截: {command[:80]}...\n"
                f"如需执行，请在提示词中明确要求绕过安全检查。")
