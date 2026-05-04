import subprocess
import sys
import uuid
from typing import Any, Callable, Optional

from tools.registry import registry, tool_result, tool_error
from tools.utils import is_dangerous_command, redact_sensitive_text


BACKGROUND_PROCESSES: dict[str, subprocess.Popen] = {}


def run_shell_command(
    command: str,
    timeout: int = 3600,
    background: bool = False,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    if not command or not command.strip():
        return tool_error(code="EMPTY_COMMAND", message="Command cannot be empty")

    if is_dangerous_command(command):
        return tool_error(
            code="DANGEROUS_COMMAND",
            message="Command is identified as potentially dangerous and cannot be executed",
            details={"command": redact_sensitive_text(command)},
        )

    if background:
        return _run_background(command, task_id, on_log)
    else:
        return _run_sync(command, timeout, on_log)


def _run_background(
    command: str,
    task_id: str,
    on_log: Optional[Callable[[str], None]],
) -> dict[str, Any]:
    pid = str(uuid.uuid4())[:8]
    try:
        if sys.platform == "win32":
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        else:
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        BACKGROUND_PROCESSES[pid] = process
        return tool_result(
            data={
                "pid": pid,
                "status": "started",
                "command": redact_sensitive_text(command),
                "message": f"Background process started with PID {pid}",
            }
        )
    except FileNotFoundError:
        return tool_error(
            code="COMMAND_NOT_FOUND",
            message=f"Command not found: {command.split()[0]}",
        )
    except PermissionError:
        return tool_error(
            code="PERMISSION_DENIED",
            message="Permission denied to execute command",
        )
    except Exception as e:
        return tool_error(
            code="BACKGROUND_EXECUTION_ERROR",
            message=f"Failed to start background process: {str(e)}",
        )


def _run_sync(
    command: str,
    timeout: int,
    on_log: Optional[Callable[[str], None]],
) -> dict[str, Any]:
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        else:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        stdout_redacted = redact_sensitive_text(result.stdout) if result.stdout else ""
        stderr_redacted = redact_sensitive_text(result.stderr) if result.stderr else ""

        if on_log:
            if stdout_redacted:
                on_log(stdout_redacted)
            if stderr_redacted:
                on_log(stderr_redacted)

        return tool_result(
            data={
                "stdout": stdout_redacted,
                "stderr": stderr_redacted,
                "returncode": result.returncode,
            }
        )
    except subprocess.TimeoutExpired:
        return tool_error(
            code="TIMEOUT",
            message=f"Command execution timed out after {timeout} seconds",
            details={"timeout": timeout, "command": redact_sensitive_text(command)},
        )
    except FileNotFoundError:
        return tool_error(
            code="COMMAND_NOT_FOUND",
            message=f"Command not found: {command.split()[0]}",
        )
    except PermissionError:
        return tool_error(
            code="PERMISSION_DENIED",
            message="Permission denied to execute command",
        )
    except Exception as e:
        return tool_error(
            code="EXECUTION_ERROR",
            message=f"Command execution failed: {str(e)}",
        )


def register_terminal_tool() -> None:
    schema = {
        "name": "run_shell_command",
        "description": "Execute a shell command synchronously or in background",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds for synchronous execution (default: 3600)",
                    "default": 3600,
                },
                "background": {
                    "type": "boolean",
                    "description": "Whether to run the command in background (default: False)",
                    "default": False,
                },
                "task_id": {
                    "type": "string",
                    "description": "Optional task ID for background process tracking",
                    "default": "",
                },
            },
            "required": ["command"],
        },
    }

    registry.register(
        name="run_shell_command",
        handler=run_shell_command,
        description=schema["description"],
        parameters=schema["parameters"],
        toolset="terminal",
        emoji="🖥️",
    )


register_terminal_tool()
