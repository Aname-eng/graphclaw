"""
Execute Tool -- Programmatic Tool Calling (PTC)

模仿 Hermes 的 execute_code 工具实现，让 LLM 可以编写 Python 脚本
通过 RPC 调用系统中的其他工具，将多步工具链压缩到单次推理中。

架构（Windows 兼容，使用 TCP 本地回环代替 UDS）：
  1. 父进程生成 `meta_tools.py` stub 模块，包含 TCP RPC 函数
  2. 父进程打开一个本地 TCP 服务器并启动 RPC 监听线程
  3. 父进程生成子进程运行 LLM 的脚本
  4. 工具调用通过 TCP 回到父进程进行分发

安全特性：
  - 工具白名单：只有 SANDBOX_ALLOWED_TOOLS 中的工具可以被调用
  - 环境变量过滤：阻止 API keys、tokens 等敏感信息泄露
  - 资源限制：超时、最大工具调用次数、输出大小限制
  - 终端参数过滤：禁止 background、pty 等参数
"""

import json
import logging
import os
import platform
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from typing import Any, Dict, List, Optional

from tools.registry import registry, tool_result, tool_error
from tools.utils import redact_sensitive_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置与常量
# ---------------------------------------------------------------------------

# 默认沙箱工具池（当调用方未指定 enabled_tools 时使用）
# 注意：web_search 和浏览器工具有独立实现，不在默认池中
# 调用方可通过 enabled_tools 参数完全自定义可用工具
DEFAULT_SANDBOX_TOOLS = frozenset([
    "read_file",
    "write_file",
    "search_files",
    "patch_file",
    "run_shell_command",
    "list_dir",
    "move_file",
    "copy_file",
    "delete_file",
    "memory_recall",
    "memory_save",
])

# 资源限制默认值
DEFAULT_TIMEOUT = 300          # 5 分钟
DEFAULT_MAX_TOOL_CALLS = 50
MAX_STDOUT_BYTES = 50_000      # 50 KB
MAX_STDERR_BYTES = 10_000      # 10 KB

# 终端参数黑名单（沙箱内禁止使用的参数）
_TERMINAL_BLOCKED_PARAMS = {"background", "pty", "notify_on_complete", "watch_patterns"}

# 安全环境变量前缀
_SAFE_ENV_PREFIXES = (
    "PATH", "HOME", "USER", "LANG", "LC_", "TERM",
    "TMPDIR", "TMP", "TEMP", "SHELL", "LOGNAME",
    "XDG_", "PYTHONPATH", "VIRTUAL_ENV", "CONDA",
    "META_", "SYSTEMROOT", "PROGRAMFILES", "APPDATA",
    "LOCALAPPDATA", "USERPROFILE", "OS", "NUMBER_OF_PROCESSORS",
    "COMPUTERNAME", "HOMEDRIVE", "HOMEPATH",
)

# 敏感信息关键词
_SECRET_SUBSTRINGS = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL",
    "PASSWD", "AUTH", "API_KEY", "PRIVATE_KEY", "ACCESS_KEY",
)


# ---------------------------------------------------------------------------
# meta_tools.py 代码生成器
# ---------------------------------------------------------------------------

# 每个工具的 stub 模板：(函数名, 签名, 文档字符串, args_dict_expr)
# 注意：web_search 和浏览器工具不在默认池中，但 stub 保留在此
# 如需在 execute_code 中使用，调用方需通过 enabled_tools 显式传入
_TOOL_STUBS = {
    "web_search": (
        "web_search",
        "query: str, limit: int = 5",
        '"""搜索网页。返回包含 results 列表的字典。"""',
        '{"query": query, "limit": limit}',
    ),
    "browser_navigate": (
        "browser_navigate",
        "url: str, wait_for: str = None",
        '"""导航到 URL 并等待指定选择器。返回包含 title, url, content 的字典。"""',
        '{"url": url, "wait_for": wait_for}',
    ),
    "browser_extract": (
        "browser_extract",
        "url: str, selector: str = None",
        '"""从 URL 提取内容。返回包含 content 的字典。"""',
        '{"url": url, "selector": selector}',
    ),
    "read_file": (
        "read_file",
        "path: str, max_chars: int = 100000",
        '"""读取文件内容。返回包含 content, size 的字典。"""',
        '{"path": path, "max_chars": max_chars}',
    ),
    "write_file": (
        "write_file",
        "path: str, content: str",
        '"""写入内容到文件（总是覆盖）。返回包含 status 的字典。"""',
        '{"path": path, "content": content}',
    ),
    "search_files": (
        "search_files",
        'root_dir: str, pattern: str, content_search: bool = False',
        '"""搜索文件。content_search=True 时搜索文件内容，否则按名称匹配。返回包含 results 的字典。"""',
        '{"root_dir": root_dir, "pattern": pattern, "content_search": content_search}',
    ),
    "patch_file": (
        "patch_file",
        'path: str, old_string: str, new_string: str',
        '"""在文件中进行查找替换。返回包含 status 的字典。"""',
        '{"path": path, "old_string": old_string, "new_string": new_string}',
    ),
    "run_shell_command": (
        "run_shell_command",
        "command: str, timeout: int = None",
        '"""运行 shell 命令（仅前台）。返回包含 stdout, stderr, returncode 的字典。"""',
        '{"command": command, "timeout": timeout}',
    ),
    "list_dir": (
        "list_dir",
        "path: str = '.'",
        '"""列出目录内容。返回包含 files, directories 的字典。"""',
        '{"path": path}',
    ),
    "move_file": (
        "move_file",
        "source: str, destination: str",
        '"""移动文件。返回包含 status 的字典。"""',
        '{"source": source, "destination": destination}',
    ),
    "copy_file": (
        "copy_file",
        "source: str, destination: str",
        '"""复制文件。返回包含 status 的字典。"""',
        '{"source": source, "destination": destination}',
    ),
    "delete_file": (
        "delete_file",
        "path: str",
        '"""删除文件。返回包含 status 的字典。"""',
        '{"path": path}',
    ),
    "memory_recall": (
        "memory_recall",
        "query: str, limit: int = 5",
        '"""回忆记忆。返回包含 memories 的字典。"""',
        '{"query": query, "limit": limit}',
    ),
    "memory_save": (
        "memory_save",
        "content: str, tags: list = None",
        '"""保存记忆。返回包含 status 的字典。"""',
        '{"content": content, "tags": tags}',
    ),
}


_COMMON_HELPERS = '''\

# ---------------------------------------------------------------------------
# 便利函数（避免常见脚本陷阱）
# ---------------------------------------------------------------------------

def json_parse(text: str):
    """解析 JSON，容忍控制字符 (strict=False)。
    当解析 terminal() 或 web_extract() 的输出中包含原始换行符/制表符时使用。"""
    return json.loads(text, strict=False)


def shell_quote(s: str) -> str:
    """对字符串进行 shell 转义，安全地插入到命令中。
    当将动态内容插入到 terminal() 命令时使用：
        terminal(f"echo {shell_quote(user_input)}")
    """
    return shlex.quote(s)


def retry(fn, max_attempts=3, delay=2):
    """使用指数退避重试函数。
    用于处理临时性失败（网络错误、API 限流）：
        result = retry(lambda: terminal("gh issue list ..."))
    """
    last_err = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if attempt < max_attempts - 1:
                time.sleep(delay * (2 ** attempt))
    raise last_err

'''

# TCP 传输头（Windows 兼容）
_TCP_TRANSPORT_HEADER = '''\
"""Auto-generated Meta tools RPC stubs (TCP transport)."""
import json, os, socket, shlex, threading, time

_HOST = os.environ.get("META_RPC_HOST", "127.0.0.1")
_PORT = int(os.environ.get("META_RPC_PORT", "0"))
_sock = None
# RPC 服务器串行处理请求，所以并发调用需要加锁
_call_lock = threading.Lock()
''' + _COMMON_HELPERS + '''\

def _connect():
    global _sock
    if _sock is None:
        _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _sock.connect((_HOST, _PORT))
        _sock.settimeout(300)
    return _sock

def _call(tool_name, args):
    """发送工具调用到父进程并返回解析后的结果。"""
    request = json.dumps({"tool": tool_name, "args": args}) + "\\n"
    with _call_lock:
        conn = _connect()
        conn.sendall(request.encode("utf-8"))
        buf = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                raise RuntimeError("Agent process disconnected")
            buf += chunk
            if buf.endswith(b"\\n"):
                break
    raw = buf.decode("utf-8").strip()
    result = json.loads(raw)
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return result
    return result

'''


def generate_meta_tools_module(enabled_tools: List[str]) -> str:
    """
    构建 meta_tools.py stub 模块的源代码。

    为 enabled_tools 列表中的每个工具生成 stub（前提是 _TOOL_STUBS 中有定义）。
    调用方已通过 execute_code 的 enabled_tools 参数控制可用工具范围。
    """
    tools_to_generate = sorted(set(enabled_tools))

    stub_functions = []
    export_names = []
    for tool_name in tools_to_generate:
        if tool_name not in _TOOL_STUBS:
            continue
        func_name, sig, doc, args_expr = _TOOL_STUBS[tool_name]
        stub_functions.append(
            f"def {func_name}({sig}):\n"
            f"    {doc}\n"
            f"    return _call({func_name!r}, {args_expr})\n"
        )
        export_names.append(func_name)

    return _TCP_TRANSPORT_HEADER + "\n".join(stub_functions)


# ---------------------------------------------------------------------------
# RPC 服务器（在父进程的线程中运行）
# ---------------------------------------------------------------------------

def _rpc_server_loop(
    server_sock: socket.socket,
    task_id: str,
    tool_call_log: list,
    tool_call_counter: list,
    max_tool_calls: int,
    allowed_tools: frozenset,
):
    """
    接受一个客户端连接，然后分发工具调用请求直到客户端断开或达到调用限制。
    """
    conn = None
    try:
        server_sock.settimeout(5)
        conn, addr = server_sock.accept()
        conn.settimeout(300)
        logger.debug("RPC client connected from %s", addr)

        buf = b""
        while True:
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk

            # 处理缓冲区中所有完整的换行分隔消息
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue

                call_start = time.monotonic()
                try:
                    request = json.loads(line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    resp = json.dumps({"error": f"Invalid RPC request: {exc}"})
                    conn.sendall((resp + "\n").encode("utf-8"))
                    continue

                tool_name = request.get("tool", "")
                tool_args = request.get("args", {})

                # 强制执行白名单
                if tool_name not in allowed_tools:
                    available = ", ".join(sorted(allowed_tools))
                    resp = json.dumps({
                        "error": (
                            f"Tool '{tool_name}' is not available in execute_code. "
                            f"Available: {available}"
                        )
                    })
                    conn.sendall((resp + "\n").encode("utf-8"))
                    continue

                # 强制执行工具调用限制
                if tool_call_counter[0] >= max_tool_calls:
                    resp = json.dumps({
                        "error": (
                            f"Tool call limit reached ({max_tool_calls}). "
                            "No more tool calls allowed in this execution."
                        )
                    })
                    conn.sendall((resp + "\n").encode("utf-8"))
                    continue

                # 过滤终端参数
                if tool_name == "run_shell_command" and isinstance(tool_args, dict):
                    for param in _TERMINAL_BLOCKED_PARAMS:
                        tool_args.pop(param, None)

                # 通过注册表分发工具调用
                try:
                    handler = registry.get_handler(tool_name)
                    if handler is None:
                        result = json.dumps({"error": f"Tool '{tool_name}' handler not found"})
                    else:
                        # 抑制内部工具处理程序的 stdout/stderr
                        _real_stdout, _real_stderr = sys.stdout, sys.stderr
                        devnull = open(os.devnull, "w")
                        try:
                            sys.stdout = devnull
                            sys.stderr = devnull
                            raw_result = handler(**tool_args)
                        finally:
                            sys.stdout, sys.stderr = _real_stdout, _real_stderr
                            devnull.close()
                        result = json.dumps(raw_result) if not isinstance(raw_result, str) else raw_result
                except Exception as exc:
                    logger.error("Tool call failed in sandbox: %s", exc, exc_info=True)
                    result = json.dumps({"error": str(exc)})

                tool_call_counter[0] += 1
                call_duration = time.monotonic() - call_start

                # 记录日志以便观察
                args_preview = str(tool_args)[:80]
                tool_call_log.append({
                    "tool": tool_name,
                    "args_preview": args_preview,
                    "duration": round(call_duration, 2),
                })

                conn.sendall((result + "\n").encode("utf-8"))

    except socket.timeout:
        logger.debug("RPC listener socket timeout")
    except OSError as e:
        logger.debug("RPC listener socket error: %s", e, exc_info=True)
    finally:
        if conn:
            try:
                conn.close()
            except OSError as e:
                logger.debug("RPC conn close error: %s", e)


# ---------------------------------------------------------------------------
# 子进程管理
# ---------------------------------------------------------------------------

def _kill_process_group(proc, escalate: bool = False):
    """终止子进程及其整个进程组。"""
    _IS_WINDOWS = platform.system() == "Windows"

    def _try_kill():
        """尝试终止进程。"""
        try:
            if _IS_WINDOWS:
                # 直接使用 Windows API 终止进程
                try:
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    PROCESS_TERMINATE = 0x0001
                    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, proc.pid)
                    if handle:
                        kernel32.TerminateProcess(handle, 1)
                        kernel32.CloseHandle(handle)
                        return True
                except Exception:
                    pass
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                return True
        except Exception:
            pass

        # 最后的回退
        try:
            proc.kill()
            return True
        except Exception:
            return False

    # 第一次尝试
    _try_kill()

    if escalate:
        # 给进程一点时间退出
        for _ in range(25):  # 5 秒 = 25 * 0.2
            if proc.poll() is not None:
                return
            time.sleep(0.2)
        # 如果还在运行，再次强制终止
        if proc.poll() is None:
            _try_kill()


# ---------------------------------------------------------------------------
# 主入口点
# ---------------------------------------------------------------------------

def execute_code(
    code: str,
    task_id: Optional[str] = None,
    enabled_tools: Optional[List[str]] = None,
    timeout: Optional[int] = None,
    max_tool_calls: Optional[int] = None,
) -> str:
    """
    在沙箱子进程中运行 Python 脚本，脚本可以通过 RPC 调用系统中的工具子集。

    Args:
        code: 要执行的 Python 源代码。
        task_id: 会话任务 ID，用于工具隔离。
        enabled_tools: 当前会话启用的工具名称列表。沙箱将获得与 SANDBOX_ALLOWED_TOOLS 的交集。
        timeout: 执行超时（秒），默认 300。
        max_tool_calls: 最大工具调用次数，默认 50。

    Returns:
        JSON 字符串，包含执行结果。
    """
    if not code or not code.strip():
        return json.dumps({"error": "No code provided."}, ensure_ascii=False)

    effective_timeout = timeout or DEFAULT_TIMEOUT
    effective_max_tool_calls = max_tool_calls or DEFAULT_MAX_TOOL_CALLS

    # 确定沙箱可以调用哪些工具
    # 如果调用方传入了 enabled_tools，完全使用调用方指定的工具池
    # 否则使用默认工具池
    if enabled_tools:
        sandbox_tools = frozenset(enabled_tools)
    else:
        sandbox_tools = DEFAULT_SANDBOX_TOOLS

    # --- 设置临时目录 ---
    tmpdir = tempfile.mkdtemp(prefix="meta_sandbox_")

    # 使用 TCP 本地回环（Windows 兼容）
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))  # 绑定到任意可用端口
    server_sock.listen(1)
    host, port = server_sock.getsockname()

    tool_call_log: list = []
    tool_call_counter = [0]
    exec_start = time.monotonic()

    try:
        # 写入自动生成的 meta_tools 模块
        tools_src = generate_meta_tools_module(list(sandbox_tools))
        with open(os.path.join(tmpdir, "meta_tools.py"), "w", encoding="utf-8") as f:
            f.write(tools_src)

        # 写入用户脚本
        with open(os.path.join(tmpdir, "script.py"), "w", encoding="utf-8") as f:
            f.write(code)

        # --- 启动 TCP RPC 服务器 ---
        rpc_thread = threading.Thread(
            target=_rpc_server_loop,
            args=(
                server_sock, task_id, tool_call_log,
                tool_call_counter, effective_max_tool_calls, sandbox_tools,
            ),
            daemon=True,
        )
        rpc_thread.start()

        # --- 生成子进程环境变量 ---
        # 故意排除 API keys 和 tokens，防止 LLM 生成的脚本泄露凭证
        child_env = {}
        for k, v in os.environ.items():
            # 阻止包含敏感关键词的变量
            if any(s in k.upper() for s in _SECRET_SUBSTRINGS):
                continue
            # 允许已知安全前缀的变量
            if any(k.startswith(p) for p in _SAFE_ENV_PREFIXES):
                child_env[k] = v

        child_env["META_RPC_HOST"] = host
        child_env["META_RPC_PORT"] = str(port)
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"

        # 确保 meta_cabinet 根目录可导入，同时临时目录优先
        _meta_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _existing_pp = child_env.get("PYTHONPATH", "")
        _pp_parts = [tmpdir, _meta_root]
        if _existing_pp:
            _pp_parts.append(_existing_pp)
        child_env["PYTHONPATH"] = os.pathsep.join(_pp_parts)

        # --- 生成子进程 ---
        _IS_WINDOWS = platform.system() == "Windows"
        script_path = os.path.join(tmpdir, "script.py")

        proc = subprocess.Popen(
            [sys.executable, script_path],
            cwd=tmpdir,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if _IS_WINDOWS else 0,
        )

        # --- 轮询循环：监控退出、超时和中断 ---
        deadline = time.monotonic() + effective_timeout
        stderr_chunks: list = []

        # stdout 使用 head+tail 策略
        _STDOUT_HEAD_BYTES = int(MAX_STDOUT_BYTES * 0.4)
        _STDOUT_TAIL_BYTES = MAX_STDOUT_BYTES - _STDOUT_HEAD_BYTES

        def _drain(pipe, chunks, max_bytes):
            """简单的仅头部读取（用于 stderr）。"""
            total = 0
            try:
                while True:
                    data = pipe.read(4096)
                    if not data:
                        break
                    if total < max_bytes:
                        keep = max_bytes - total
                        chunks.append(data[:keep])
                    total += len(data)
            except (ValueError, OSError) as e:
                logger.debug("Error reading process output: %s", e, exc_info=True)

        stdout_total_bytes = [0]

        def _drain_head_tail(pipe, head_chunks, tail_chunks, head_bytes, tail_bytes, total_ref):
            """读取 stdout，同时保留头部和尾部数据。"""
            head_collected = 0
            tail_buf = deque()
            tail_collected = 0
            try:
                while True:
                    data = pipe.read(4096)
                    if not data:
                        break
                    total_ref[0] += len(data)
                    # 先填充头部缓冲区
                    if head_collected < head_bytes:
                        keep = min(len(data), head_bytes - head_collected)
                        head_chunks.append(data[:keep])
                        head_collected += keep
                        data = data[keep:]
                        if not data:
                            continue
                    # 超过头部的部分放入滚动尾部缓冲区
                    tail_buf.append(data)
                    tail_collected += len(data)
                    # 淘汰旧的尾部数据以保持在预算内
                    while tail_collected > tail_bytes and tail_buf:
                        oldest = tail_buf.popleft()
                        tail_collected -= len(oldest)
            except (ValueError, OSError):
                pass
            tail_chunks.extend(tail_buf)

        stdout_head_chunks: list = []
        stdout_tail_chunks: list = []

        stdout_reader = threading.Thread(
            target=_drain_head_tail,
            args=(proc.stdout, stdout_head_chunks, stdout_tail_chunks,
                  _STDOUT_HEAD_BYTES, _STDOUT_TAIL_BYTES, stdout_total_bytes),
            daemon=True
        )
        stderr_reader = threading.Thread(
            target=_drain, args=(proc.stderr, stderr_chunks, MAX_STDERR_BYTES), daemon=True
        )
        stdout_reader.start()
        stderr_reader.start()

        status = "success"
        while proc.poll() is None:
            if time.monotonic() > deadline:
                _kill_process_group(proc, escalate=True)
                # 等待进程真正退出
                try:
                    proc.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    pass
                status = "timeout"
                break
            time.sleep(0.2)

        # 等待读取器完成排空
        stdout_reader.join(timeout=3)
        stderr_reader.join(timeout=3)

        stdout_head = b"".join(stdout_head_chunks).decode("utf-8", errors="replace")
        stdout_tail = b"".join(stdout_tail_chunks).decode("utf-8", errors="replace")
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")

        # 组装带 head+tail 截断的 stdout
        total_stdout = stdout_total_bytes[0]
        if total_stdout > MAX_STDOUT_BYTES and stdout_tail:
            omitted = total_stdout - len(stdout_head) - len(stdout_tail)
            truncated_notice = (
                f"\n\n... [OUTPUT TRUNCATED - {omitted:,} chars omitted "
                f"out of {total_stdout:,} total] ...\n\n"
            )
            stdout_text = stdout_head + truncated_notice + stdout_tail
        else:
            stdout_text = stdout_head + stdout_tail

        exit_code = proc.returncode if proc.returncode is not None else -1
        duration = round(time.monotonic() - exec_start, 2)

        # 等待 RPC 线程完成
        server_sock.close()
        rpc_thread.join(timeout=3)

        # 去除 ANSI 转义序列
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        stdout_text = ansi_escape.sub('', stdout_text)
        stderr_text = ansi_escape.sub('', stderr_text)

        # 从输出中脱敏
        stdout_text = redact_sensitive_text(stdout_text)
        stderr_text = redact_sensitive_text(stderr_text)

        # 构建响应
        result: Dict[str, Any] = {
            "status": status,
            "output": stdout_text,
            "tool_calls_made": tool_call_counter[0],
            "duration_seconds": duration,
        }

        if status == "timeout":
            timeout_msg = f"Script timed out after {effective_timeout}s and was killed."
            result["error"] = timeout_msg
            if stdout_text:
                result["output"] = stdout_text + f"\n\n⏰ {timeout_msg}"
            else:
                result["output"] = f"⏰ {timeout_msg}"
            logger.warning(
                "execute_code timed out after %ss (limit %ss) with %d tool calls",
                duration, effective_timeout, tool_call_counter[0],
            )
        elif exit_code != 0:
            result["status"] = "error"
            result["error"] = stderr_text or f"Script exited with code {exit_code}"
            if stderr_text:
                result["output"] = stdout_text + "\n--- stderr ---\n" + stderr_text

        return json.dumps(result, ensure_ascii=False)

    except Exception as exc:
        duration = round(time.monotonic() - exec_start, 2)
        logger.error(
            "execute_code failed after %ss with %d tool calls: %s: %s",
            duration, tool_call_counter[0], type(exc).__name__, exc,
            exc_info=True,
        )
        return json.dumps({
            "status": "error",
            "error": str(exc),
            "tool_calls_made": tool_call_counter[0],
            "duration_seconds": duration,
        }, ensure_ascii=False)

    finally:
        # 清理临时目录
        if server_sock is not None:
            try:
                server_sock.close()
            except OSError:
                pass
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Schema 构建
# ---------------------------------------------------------------------------

_TOOL_DOC_LINES = [
    ("web_search",
     "  web_search(query: str, limit: int = 5) -> dict\n"
     "    返回 {\"results\": [{\"url\", \"title\", \"snippet\"}, ...]}"),
    ("browser_navigate",
     "  browser_navigate(url: str, wait_for: str = None) -> dict\n"
     "    返回 {\"title\", \"url\", \"content_preview\"}"),
    ("browser_extract",
     "  browser_extract(url: str, selector: str = None) -> dict\n"
     "    返回 {\"content\"}"),
    ("read_file",
     "  read_file(path: str, max_chars: int = 100000) -> dict\n"
     "    返回 {\"content\", \"size\"}"),
    ("write_file",
     "  write_file(path: str, content: str) -> dict\n"
     "    总是覆盖整个文件。"),
    ("search_files",
     "  search_files(root_dir: str, pattern: str, content_search=False) -> dict\n"
     "    content_search=True 时搜索文件内容。返回 {\"results\": [...]}"),
    ("patch_file",
     "  patch_file(path: str, old_string: str, new_string: str) -> dict\n"
     "    在文件中将 old_string 替换为 new_string。"),
    ("run_shell_command",
     "  run_shell_command(command: str, timeout=None) -> dict\n"
     "    仅前台执行。返回 {\"stdout\", \"stderr\", \"returncode\"}"),
    ("list_dir",
     "  list_dir(path: str = \".\") -> dict\n"
     "    返回 {\"files\": [...], \"directories\": [...]}"),
    ("move_file",
     "  move_file(source: str, destination: str) -> dict\n"
     "    移动文件。"),
    ("copy_file",
     "  copy_file(source: str, destination: str) -> dict\n"
     "    复制文件。"),
    ("delete_file",
     "  delete_file(path: str) -> dict\n"
     "    删除文件。"),
    ("memory_recall",
     "  memory_recall(query: str, limit: int = 5) -> dict\n"
     "    回忆记忆。"),
    ("memory_save",
     "  memory_save(content: str, tags: list = None) -> dict\n"
     "    保存记忆。"),
]


def build_execute_code_schema(enabled_sandbox_tools: set = None) -> dict:
    """构建 execute_code 的 schema，只列出启用的工具。"""
    if enabled_sandbox_tools is None:
        enabled_sandbox_tools = DEFAULT_SANDBOX_TOOLS

    tool_lines = "\n".join(
        doc for name, doc in _TOOL_DOC_LINES if name in enabled_sandbox_tools
    )

    import_examples = [n for n in ("run_shell_command", "read_file") if n in enabled_sandbox_tools]
    if not import_examples:
        import_examples = sorted(enabled_sandbox_tools)[:2]
    import_str = ", ".join(import_examples) + ", ..." if import_examples else "..."

    description = (
        "运行一个可以程序化调用系统工具的 Python 脚本。"
        "当你需要 3+ 次工具调用并在中间进行数据处理、需要过滤/减少大量工具输出、"
        "需要条件分支或循环时，使用此工具。\n\n"
        "对于单次工具调用或需要看到完整结果并应用复杂推理的任务，使用普通工具调用。\n\n"
        f"通过 `from meta_tools import ...` 可用:\n\n"
        f"{tool_lines}\n\n"
        "限制: 5分钟超时, 50KB stdout 上限, 每次脚本最多 50 次工具调用。"
        "run_shell_command() 仅支持前台执行。\n\n"
        "脚本在独立的临时目录中运行，使用绝对路径或通过工具访问用户文件。\n\n"
        "将最终结果打印到 stdout。使用 Python 标准库 (json, re, math, csv, "
        "datetime, collections 等) 在工具调用之间进行数据处理。\n\n"
        "还可用（无需导入，内置于 meta_tools）:\n"
        "  json_parse(text: str) — 带 strict=False 的 json.loads\n"
        "  shell_quote(s: str) — shlex.quote()\n"
        "  retry(fn, max_attempts=3, delay=2) — 指数退避重试"
    )

    return {
        "name": "execute_code",
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "要执行的 Python 代码。使用 "
                        f"`from meta_tools import {import_str}` "
                        "导入工具，并将最终结果打印到 stdout。"
                    ),
                },
            },
            "required": ["code"],
        },
    }


EXECUTE_CODE_SCHEMA = build_execute_code_schema()


# ---------------------------------------------------------------------------
# 注册到工具注册表
# ---------------------------------------------------------------------------

def _handle_execute_code(args, **kw):
    # enabled_tools 可以从 args 传入（FXL 规划时指定）或从 kw 传入（系统级配置）
    enabled_tools = args.get("enabled_tools") or kw.get("enabled_tools")
    return execute_code(
        code=args.get("code", ""),
        task_id=kw.get("task_id"),
        enabled_tools=enabled_tools,
        timeout=args.get("timeout"),
        max_tool_calls=args.get("max_tool_calls"),
    )


registry.register(
    name="execute_code",
    handler=_handle_execute_code,
    description=EXECUTE_CODE_SCHEMA["description"],
    parameters=EXECUTE_CODE_SCHEMA["parameters"],
    toolset="code_execution",
    emoji="🐍",
)
