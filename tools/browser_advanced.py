from typing import Any, Callable, Optional

from tools.registry import registry, tool_result, tool_error
from tools.utils import redact_sensitive_text
from tools.web_tools import _get_browser

try:
    from playwright.sync_api import Page
except ImportError:
    Page = None


def _get_page() -> Page:
    context = _get_browser()
    if not context.pages:
        return context.new_page()
    return context.pages[0]


_cdp_sessions: dict = {}


def _get_cdp_session(page) -> Any:
    """获取或创建 CDP Session."""
    page_id = id(page)
    if page_id not in _cdp_sessions:
        _cdp_sessions[page_id] = page.context.new_cdp_session(page)
    return _cdp_sessions[page_id]


def browser_cdp_execute(
    method: str,
    params: Optional[dict[str, Any]] = None,
    headless: bool = False,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """直接发送 Chrome DevTools Protocol (CDP) 命令。

    Args:
        method: CDP 方法名，例如 "Network.enable", "Runtime.evaluate"。
        params: CDP 命令参数。
        headless: 是否在无头模式下运行浏览器。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含 CDP 执行结果的字典。
    """
    try:
        page = _get_page()
        if on_log:
            on_log(f"[{task_id}] Executing CDP command: {method}")

        cdp_session = _get_cdp_session(page)
        result = cdp_session.send(method, params or {})

        return tool_result(
            data={
                "method": method,
                "result": result,
            }
        )
    except Exception as e:
        return tool_error(
            code="CDP_EXECUTE_ERROR",
            message=f"Failed to execute CDP command {method}: {str(e)}",
        )


def browser_handle_dialog(
    action: str,
    prompt_text: Optional[str] = None,
    headless: bool = False,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """处理浏览器弹窗（alert/confirm/prompt）。

    Args:
        action: 处理动作，"accept" 接受或 "dismiss" 拒绝。
        prompt_text: 当处理 prompt 弹窗时需要输入的文本。
        headless: 是否在无头模式下运行浏览器。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含操作结果的字典。
    """
    try:
        page = _get_page()
        if on_log:
            on_log(f"[{task_id}] Preparing to handle dialog with action: {action}")

        def handle_dialog(dialog):
            if action == "accept":
                if prompt_text is not None:
                    dialog.accept(prompt_text)
                else:
                    dialog.accept()
            else:
                dialog.dismiss()
            if on_log:
                on_log(f"[{task_id}] Dialog {action}ed")

        page.once("dialog", handle_dialog)

        return tool_result(
            data={
                "action": action,
                "status": "listening",
                "message": "Dialog handler registered",
            }
        )
    except Exception as e:
        return tool_error(
            code="DIALOG_HANDLE_ERROR",
            message=f"Failed to handle dialog: {str(e)}",
        )


def browser_get_performance_metrics(
    headless: bool = False,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """获取页面性能指标（FCP, LCP, TTI 等）。

    Args:
        headless: 是否在无头模式下运行浏览器。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含性能指标的字典。
    """
    try:
        page = _get_page()
        if on_log:
            on_log(f"[{task_id}] Getting performance metrics")

        metrics = page.evaluate("""
            () => {
                const navigation = performance.getEntriesByType('navigation')[0];
                const paint = performance.getEntriesByType('paint');
                const fcp = paint.find(p => p.name === 'first-contentful-paint')?.startTime || null;
                const lcp = performance.getEntriesByType('largest-contentful-paint')[0]?.startTime || null;
                
                return {
                    domContentLoaded: navigation?.domContentLoadedEventEnd || null,
                    load: navigation?.loadEventEnd || null,
                    firstContentfulPaint: fcp,
                    largestContentfulPaint: lcp,
                    domInteractive: navigation?.domInteractive || null,
                };
            }
        """)

        return tool_result(
            data={
                "metrics": metrics,
            }
        )
    except Exception as e:
        return tool_error(
            code="PERFORMANCE_METRICS_ERROR",
            message=f"Failed to get performance metrics: {str(e)}",
        )


def register_all() -> None:
    """注册所有浏览器高级工具。"""
    registry.register(
        name="browser_cdp_execute",
        handler=browser_cdp_execute,
        description="直接发送 Chrome DevTools Protocol (CDP) 命令",
        parameters={
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "CDP 方法名，例如 'Network.enable', 'Runtime.evaluate'"},
                "params": {"type": "object", "description": "CDP 命令参数"},
                "headless": {"type": "boolean", "description": "Run browser in headless mode", "default": False},
                "task_id": {"type": "string", "description": "Optional task ID for logging", "default": ""},
            },
            "required": ["method"],
        },
        toolset="browser",
        emoji="🌐",
    )

    registry.register(
        name="browser_handle_dialog",
        handler=browser_handle_dialog,
        description="处理浏览器弹窗（alert/confirm/prompt）",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["accept", "dismiss"], "description": "处理动作：接受或拒绝"},
                "prompt_text": {"type": "string", "description": "prompt 弹窗需要输入的文本"},
                "headless": {"type": "boolean", "description": "Run browser in headless mode", "default": False},
                "task_id": {"type": "string", "description": "Optional task ID for logging", "default": ""},
            },
            "required": ["action"],
        },
        toolset="browser",
        emoji="🌐",
    )

    registry.register(
        name="browser_get_performance_metrics",
        handler=browser_get_performance_metrics,
        description="获取页面性能指标（FCP, LCP, TTI 等）",
        parameters={
            "type": "object",
            "properties": {
                "headless": {"type": "boolean", "description": "Run browser in headless mode", "default": False},
                "task_id": {"type": "string", "description": "Optional task ID for logging", "default": ""},
            },
        },
        toolset="browser",
        emoji="🌐",
    )


register_all()
