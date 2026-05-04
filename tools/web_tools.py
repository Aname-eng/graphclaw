import tempfile
import time
import base64
from pathlib import Path
from typing import Any, Callable, Optional

from tools.registry import registry, tool_result, tool_error
from tools.utils import redact_sensitive_text

try:
    from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeout
except ImportError:
    sync_playwright = None
    PlaywrightTimeout = Exception

_browser_context: Optional[BrowserContext] = None
_playwright_instance = None

# 搜索频率限制 — 每次搜索间隔至少 30 + random(0,10) 秒
_last_search_time: float = 0
import random as _random


def _enforce_search_delay():
    """强制执行搜索间隔，防止被搜索引擎封禁."""
    global _last_search_time
    now = time.time()
    elapsed = now - _last_search_time
    min_delay = 30 + _random.randint(0, 10)

    if elapsed < min_delay:
        wait = min_delay - elapsed
        print(f"  ⏳ 搜索频率限制: 等待 {wait:.1f} 秒 (需间隔{min_delay}s)...")
        time.sleep(wait)

    _last_search_time = time.time()


def _get_browser(headless: bool = False) -> BrowserContext:
    global _browser_context, _playwright_instance

    if _browser_context is None:
        if sync_playwright is None:
            raise ImportError("Playwright is not installed. Run: pip install playwright && playwright install")
        _playwright_instance = sync_playwright().start()
        browser = _playwright_instance.chromium.launch(headless=headless)
        _browser_context = browser.new_context()
    return _browser_context


def _get_page() -> Page:
    context = _get_browser()
    if not context.pages:
        return context.new_page()
    return context.pages[0]


def browser_navigate(
    url: str,
    wait_for: Optional[str] = None,
    headless: bool = False,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Navigate to a URL and optionally wait for a selector."""
    try:
        # 搜索引擎请求强制执行频率限制
        search_domains = ("bing.com/search", "google.com/search", "baidu.com/s", "duckduckgo.com/?q")
        if any(d in url.lower() for d in search_domains):
            if on_log:
                on_log(f"[{task_id}] ⏳ 搜索引擎请求，执行频率限制...")
            _enforce_search_delay()

        page = _get_page()
        if on_log:
            on_log(f"[{task_id}] Navigating to {url}")

        page.goto(url, wait_until="domcontentloaded")

        if wait_for:
            try:
                page.wait_for_selector(wait_for, timeout=30000)
            except PlaywrightTimeout:
                if on_log:
                    on_log(f"[{task_id}] Wait for selector '{wait_for}' timed out")

        content_preview = page.content()[:500] if page.content() else ""

        return tool_result(
            data={
                "title": page.title(),
                "url": page.url,
                "content_preview": redact_sensitive_text(content_preview),
            }
        )
    except PlaywrightTimeout as e:
        return tool_error(
            code="NAVIGATE_TIMEOUT",
            message=f"Navigation to {url} timed out: {str(e)}",
        )
    except Exception as e:
        return tool_error(
            code="NAVIGATE_ERROR",
            message=f"Failed to navigate to {url}: {str(e)}",
        )


def browser_click(
    selector: Optional[str] = None,
    text: Optional[str] = None,
    headless: bool = False,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Click an element by selector or text."""
    try:
        page = _get_page()

        if text:
            if on_log:
                on_log(f"[{task_id}] Clicking text: {text}")
            page.click(f"text={text}")
        elif selector:
            if on_log:
                on_log(f"[{task_id}] Clicking selector: {selector}")
            page.click(selector)
        else:
            return tool_error(
                code="CLICK_ERROR",
                message="Either selector or text must be provided",
            )

        time.sleep(0.3)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            screenshot_path = f.name

        page.screenshot(path=screenshot_path)

        return tool_result(
            data={
                "clicked": text or selector,
                "screenshot_path": screenshot_path,
            }
        )
    except PlaywrightTimeout as e:
        return tool_error(
            code="CLICK_TIMEOUT",
            message=f"Click action timed out: {str(e)}",
        )
    except Exception as e:
        return tool_error(
            code="CLICK_ERROR",
            message=f"Failed to click element: {str(e)}",
        )


def browser_type(
    selector: str,
    text: str,
    headless: bool = False,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Type text into an input field."""
    try:
        page = _get_page()
        if on_log:
            on_log(f"[{task_id}] Typing into selector: {selector}")

        page.fill(selector, text)

        return tool_result(
            data={
                "selector": selector,
                "text_length": len(text),
            }
        )
    except PlaywrightTimeout as e:
        return tool_error(
            code="TYPE_TIMEOUT",
            message=f"Type action timed out: {str(e)}",
        )
    except Exception as e:
        return tool_error(
            code="TYPE_ERROR",
            message=f"Failed to type into {selector}: {str(e)}",
        )


def browser_execute_js(
    script: str,
    headless: bool = False,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Execute JavaScript in the page context."""
    try:
        page = _get_page()
        if on_log:
            on_log(f"[{task_id}] Executing JavaScript")

        result = page.evaluate(script)

        # 不再截断结果，让调用方决定如何处理
        # 但防止极端情况（超过100KB）
        if isinstance(result, str) and len(result) > 100000:
            result = result[:100000] + "...[truncated]"

        return tool_result(
            data={
                "result": redact_sensitive_text(str(result)) if result else None,
            }
        )
    except Exception as e:
        return tool_error(
            code="JS_EXECUTION_ERROR",
            message=f"Failed to execute JavaScript: {str(e)}",
        )


def browser_screenshot(
    full_page: bool = False,
    headless: bool = False,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Take a screenshot of the current page."""
    try:
        page = _get_page()
        if on_log:
            on_log(f"[{task_id}] Taking screenshot (full_page={full_page})")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            screenshot_path = f.name

        page.screenshot(path=screenshot_path, full_page=full_page)

        return tool_result(
            data={
                "screenshot_path": screenshot_path,
            }
        )
    except Exception as e:
        return tool_error(
            code="SCREENSHOT_ERROR",
            message=f"Failed to take screenshot: {str(e)}",
        )


def browser_close(
    headless: bool = False,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Close the browser session."""
    global _browser_context, _playwright_instance

    try:
        if on_log:
            on_log(f"[{task_id}] Closing browser")

        if _browser_context:
            _browser_context.close()
            _browser_context = None

        if _playwright_instance:
            _playwright_instance.stop()
            _playwright_instance = None

        return tool_result(
            data={
                "status": "closed",
            }
        )
    except Exception as e:
        _browser_context = None
        _playwright_instance = None
        return tool_error(
            code="CLOSE_ERROR",
            message=f"Failed to close browser: {str(e)}",
        )


def register_web_tools() -> None:
    registry.register(
        name="browser_navigate",
        handler=browser_navigate,
        description="Navigate to a URL and optionally wait for a selector",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"},
                "wait_for": {"type": "string", "description": "Optional CSS selector to wait for"},
                "headless": {"type": "boolean", "description": "Run browser in headless mode", "default": False},
                "task_id": {"type": "string", "description": "Optional task ID for logging", "default": ""},
            },
            "required": ["url"],
        },
        toolset="web",
        emoji="🌐",
    )

    registry.register(
        name="browser_click",
        handler=browser_click,
        description="Click an element by selector or text",
        parameters={
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector to click"},
                "text": {"type": "string", "description": "Text to click (alternative to selector)"},
                "headless": {"type": "boolean", "description": "Run browser in headless mode", "default": False},
                "task_id": {"type": "string", "description": "Optional task ID for logging", "default": ""},
            },
        },
        toolset="web",
        emoji="🌐",
    )

    registry.register(
        name="browser_type",
        handler=browser_type,
        description="Type text into an input field",
        parameters={
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of input field"},
                "text": {"type": "string", "description": "Text to type"},
                "headless": {"type": "boolean", "description": "Run browser in headless mode", "default": False},
                "task_id": {"type": "string", "description": "Optional task ID for logging", "default": ""},
            },
            "required": ["selector", "text"],
        },
        toolset="web",
        emoji="🌐",
    )

    registry.register(
        name="browser_execute_js",
        handler=browser_execute_js,
        description="Execute JavaScript in the page context",
        parameters={
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "JavaScript code to execute"},
                "headless": {"type": "boolean", "description": "Run browser in headless mode", "default": False},
                "task_id": {"type": "string", "description": "Optional task ID for logging", "default": ""},
            },
            "required": ["script"],
        },
        toolset="web",
        emoji="🌐",
    )

    registry.register(
        name="browser_screenshot",
        handler=browser_screenshot,
        description="Take a screenshot of the current page",
        parameters={
            "type": "object",
            "properties": {
                "full_page": {"type": "boolean", "description": "Capture full page or just viewport", "default": False},
                "headless": {"type": "boolean", "description": "Run browser in headless mode", "default": False},
                "task_id": {"type": "string", "description": "Optional task ID for logging", "default": ""},
            },
        },
        toolset="web",
        emoji="🌐",
    )

    registry.register(
        name="browser_close",
        handler=browser_close,
        description="Close the browser session",
        parameters={
            "type": "object",
            "properties": {
                "headless": {"type": "boolean", "description": "Run browser in headless mode", "default": False},
                "task_id": {"type": "string", "description": "Optional task ID for logging", "default": ""},
            },
        },
        toolset="web",
        emoji="🌐",
    )


register_web_tools()
