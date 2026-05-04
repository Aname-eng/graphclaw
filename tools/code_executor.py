import subprocess
import sys
import time
import os
import re
from typing import Any, Callable, Optional, Dict

from tools.registry import registry, tool_result, tool_error
from tools.utils import redact_sensitive_text, is_dangerous_command
from tools.approval_tool import request_approval, is_command_approved


def _sanitize_python_code(code: str) -> tuple[bool, str]:
    """检查Python代码是否安全"""
    dangerous_patterns = [
        r"__import__\s*\(\s*['\"]os['\"]",
        r"import\s+os\s*[;\n]",
        r"from\s+os\s+import",
        r"__import__\s*\(\s*['\"]subprocess['\"]",
        r"import\s+subprocess\s*[;\n]",
        r"from\s+subprocess\s+import",
        r"eval\s*\(",
        r"exec\s*\(",
        r"compile\s*\(",
        r"__import__\s*\(",
        r"globals\(\)",
        r"locals\(\)",
        r"vars\(\)",
        r"getattr\(",
        r"setattr\(",
        r"delattr\(",
        r"open\s*\(",
        r"builtins\.",
        r"__builtins__",
    ]
    code_lower = code.lower()
    for pattern in dangerous_patterns:
        if re.search(pattern, code_lower, re.IGNORECASE):
            return False, f"Code contains dangerous pattern: {pattern}"
    return True, ""


def _sanitize_javascript_code(code: str) -> tuple[bool, str]:
    """检查JavaScript代码是否安全"""
    dangerous_patterns = [
        r"child_process",
        r"require\(['\"]fs['\"]",
        r"fs\.",
        r"eval\s*\(",
        r"Function\s*\(",
        r"window\.",
        r"document\.",
        r"fetch\s*\(",
        r"XMLHttpRequest",
    ]
    code_lower = code.lower()
    for pattern in dangerous_patterns:
        if re.search(pattern, code_lower, re.IGNORECASE):
            return False, f"Code contains dangerous pattern: {pattern}"
    return True, ""


def execute_code(
    language: str,
    code: str,
    timeout: int = 30,
    memory_limit_mb: int = 256,
    env_vars: Optional[Dict[str, str]] = None,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
    auto_approve: bool = False,
) -> dict[str, Any]:
    """
    Execute Python/JavaScript/Shell code snippet in a sandbox environment.

    Args:
        language: Programming language - "python" | "javascript" | "shell".
        code: Code to execute.
        timeout: Timeout in seconds (default: 30).
        memory_limit_mb: Memory limit in MB (default: 256).
        env_vars: Optional environment variables to set.
        task_id: Task ID for tracking.
        on_log: Optional callback for logging.
        auto_approve: If True, automatically approve dangerous commands.

    Returns:
        Dictionary with execution results.
    """
    start_time = time.time()
    supported_languages = ["python", "javascript", "shell"]

    if language not in supported_languages:
        execution_time = time.time() - start_time
        return {
            "success": False,
            "output": "",
            "error": f"Unsupported language: {language}. Supported languages: {', '.join(supported_languages)}",
            "execution_time": execution_time
        }

    if not code or not code.strip():
        execution_time = time.time() - start_time
        return {
            "success": False,
            "output": "",
            "error": "Code cannot be empty",
            "execution_time": execution_time
        }

    try:
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)

        # 检查代码安全性
        is_safe = True
        danger_reason = ""
        if language == "python":
            is_safe, danger_reason = _sanitize_python_code(code)
        elif language == "javascript":
            is_safe, danger_reason = _sanitize_javascript_code(code)
        elif language == "shell":
            if is_dangerous_command(code):
                is_safe = False
                danger_reason = "Command is identified as potentially dangerous"

        # 如果代码被识别为不安全，请求批准
        if not is_safe and not auto_approve:
            operation = f"执行 {language} 代码"
            approval_result = request_approval(
                operation=operation,
                details=code[:500],  # 只显示前500字符
                risk_level="high",
                task_id=task_id,
                on_log=on_log
            )
            
            if approval_result.get("success") and approval_result.get("data", {}).get("status") == "pending":
                # 需要用户批准，返回等待状态
                return {
                    "success": False,
                    "output": "",
                    "error": "Waiting for user approval",
                    "needs_approval": True,
                    "approval_id": approval_result.get("data", {}).get("approval_id"),
                    "approval_options": approval_result.get("data", {}).get("options", []),
                    "execution_time": time.time() - start_time
                }
            elif approval_result.get("success") and approval_result.get("data", {}).get("status") != "already_approved":
                # 未批准，返回错误
                return {
                    "success": False,
                    "output": "",
                    "error": danger_reason,
                    "execution_time": time.time() - start_time
                }

        # 执行代码
        if language == "python":
            cmd = [sys.executable, "-c", code]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env
            )
        elif language == "javascript":
            cmd = ["node", "-e", code]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env
            )
        elif language == "shell":
            if sys.platform == "win32":
                result = subprocess.run(
                    code,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    env=env
                )
            else:
                result = subprocess.run(
                    code,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=env
                )

        stdout = redact_sensitive_text(result.stdout) if result.stdout else ""
        stderr = redact_sensitive_text(result.stderr) if result.stderr else ""
        output = stdout
        if stderr:
            if output:
                output += "\n"
            output += stderr

        if on_log:
            if stdout:
                on_log(stdout)
            if stderr:
                on_log(stderr)

        execution_time = time.time() - start_time
        success = result.returncode == 0

        return {
            "success": success,
            "output": output[:10240],
            "error": "" if success else f"Command exited with code {result.returncode}",
            "execution_time": execution_time
        }

    except subprocess.TimeoutExpired:
        execution_time = time.time() - start_time
        return {
            "success": False,
            "output": "",
            "error": f"Execution timed out after {timeout} seconds",
            "execution_time": execution_time
        }
    except FileNotFoundError as e:
        execution_time = time.time() - start_time
        return {
            "success": False,
            "output": "",
            "error": f"Required executable not found: {str(e)}",
            "execution_time": execution_time
        }
    except PermissionError:
        execution_time = time.time() - start_time
        return {
            "success": False,
            "output": "",
            "error": "Permission denied to execute code",
            "execution_time": execution_time
        }
    except Exception as e:
        execution_time = time.time() - start_time
        return {
            "success": False,
            "output": "",
            "error": f"Code execution failed: {str(e)}",
            "execution_time": execution_time
        }


EXECUTE_CODE_SCHEMA = {
    "name": "execute_code",
    "description": "Execute Python/JavaScript/Shell code snippet in a sandbox.",
    "parameters": {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "enum": ["python", "javascript", "shell"],
                "description": "Programming language to execute"
            },
            "code": {
                "type": "string",
                "description": "Code snippet to execute"
            },
            "timeout": {
                "type": "integer",
                "default": 30,
                "description": "Timeout in seconds (default: 30)"
            },
            "memory_limit_mb": {
                "type": "integer",
                "default": 256,
                "description": "Memory limit in MB (default: 256)"
            },
            "env_vars": {
                "type": "object",
                "description": "Optional environment variables to set",
                "default": None
            },
            "task_id": {
                "type": "string",
                "description": "Task ID for tracking",
                "default": ""
            },
            "auto_approve": {
                "type": "boolean",
                "description": "If True, automatically approve dangerous commands",
                "default": False
            }
        },
        "required": ["language", "code"]
    }
}


def register_all() -> None:
    """Register all code execution tools to the registry."""
    registry.register(
        name=EXECUTE_CODE_SCHEMA["name"],
        handler=execute_code,
        description=EXECUTE_CODE_SCHEMA["description"],
        parameters=EXECUTE_CODE_SCHEMA["parameters"],
        toolset="code",
        emoji="💻"
    )


register_all()
