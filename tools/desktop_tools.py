from typing import Any, Callable, Optional

import time
from pathlib import Path
import tempfile

from tools.registry import registry, tool_result, tool_error

# 延迟导入 pyautogui 和 numpy，避免模块加载时耗时
_pyautogui = None
_numpy = None

def _get_pyautogui():
    global _pyautogui
    if _pyautogui is None:
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.1
        _pyautogui = pyautogui
    return _pyautogui

def _get_numpy():
    global _numpy
    if _numpy is None:
        import numpy as np
        _numpy = np
    return _numpy


def desktop_screenshot(
    region: Optional[tuple[int, int, int, int]] = None,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """
    Capture a screenshot of the full screen or a specified region.

    Args:
        region: Optional tuple (left, top, width, height) to capture a specific region.
        task_id: Optional task identifier for logging.
        on_log: Optional callback function for logging messages.

    Returns:
        A dictionary containing success status, screenshot path, and dimensions.
    """
    try:
        if on_log:
            on_log(f"[{task_id}] Capturing screenshot, region={region}")

        pyautogui = _get_pyautogui()
        screenshot = pyautogui.screenshot()

        if region is not None:
            left, top, width, height = region
            screenshot = screenshot.crop((left, top, left + width, top + height))

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            temp_path = Path(tmp.name)
            screenshot.save(temp_path, format="PNG")

        width, height = screenshot.size

        return tool_result(
            data={
                "success": True,
                "path": str(temp_path),
                "width": width,
                "height": height,
                "region": region,
            }
        )
    except Exception as e:
        return tool_error(
            code="SCREENSHOT_ERROR",
            message=f"Failed to capture screenshot: {str(e)}",
        )


def desktop_click(
    x: int,
    y: int,
    clicks: int = 1,
    interval: float = 0.0,
    button: str = "left",
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """
    Perform a mouse click at the specified coordinates.

    Args:
        x: X coordinate.
        y: Y coordinate.
        clicks: Number of clicks.
        interval: Interval between clicks.
        button: Mouse button ('left', 'right', 'middle').
        task_id: Optional task identifier.
        on_log: Optional callback function.

    Returns:
        A dictionary containing success status.
    """
    try:
        if on_log:
            on_log(f"[{task_id}] Clicking at ({x}, {y}), button={button}, clicks={clicks}")

        pyautogui = _get_pyautogui()
        pyautogui.click(x=x, y=y, clicks=clicks, interval=interval, button=button)

        return tool_result(
            data={
                "success": True,
                "x": x,
                "y": y,
                "button": button,
                "clicks": clicks,
            }
        )
    except Exception as e:
        return tool_error(
            code="CLICK_ERROR",
            message=f"Failed to click at ({x}, {y}): {str(e)}",
        )


def desktop_type(
    text: str,
    interval: float = 0.01,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """
    Type text using the keyboard.

    Args:
        text: Text to type.
        interval: Interval between key presses.
        task_id: Optional task identifier.
        on_log: Optional callback function.

    Returns:
        A dictionary containing success status.
    """
    try:
        if on_log:
            on_log(f"[{task_id}] Typing text: {text[:50]}...")

        pyautogui = _get_pyautogui()
        pyautogui.typewrite(text, interval=interval)

        return tool_result(
            data={
                "success": True,
                "text": text,
                "interval": interval,
            }
        )
    except Exception as e:
        return tool_error(
            code="TYPE_ERROR",
            message=f"Failed to type text: {str(e)}",
        )


def desktop_press(
    keys: list[str],
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """
    Press a sequence of keys.

    Args:
        keys: List of keys to press (e.g., ['ctrl', 'c']).
        task_id: Optional task identifier.
        on_log: Optional callback function.

    Returns:
        A dictionary containing success status.
    """
    try:
        if on_log:
            on_log(f"[{task_id}] Pressing keys: {keys}")

        pyautogui = _get_pyautogui()
        pyautogui.hotkey(*keys)

        return tool_result(
            data={
                "success": True,
                "keys": keys,
            }
        )
    except Exception as e:
        return tool_error(
            code="PRESS_ERROR",
            message=f"Failed to press keys {keys}: {str(e)}",
        )


def desktop_scroll(
    clicks: int,
    x: Optional[int] = None,
    y: Optional[int] = None,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """
    Scroll the mouse wheel.

    Args:
        clicks: Number of clicks (positive for up, negative for down).
        x: Optional X coordinate to move to before scrolling.
        y: Optional Y coordinate to move to before scrolling.
        task_id: Optional task identifier.
        on_log: Optional callback function.

    Returns:
        A dictionary containing success status.
    """
    try:
        if on_log:
            on_log(f"[{task_id}] Scrolling {clicks} clicks at ({x}, {y})")

        pyautogui = _get_pyautogui()
        if x is not None and y is not None:
            pyautogui.scroll(clicks, x=x, y=y)
        else:
            pyautogui.scroll(clicks)

        return tool_result(
            data={
                "success": True,
                "clicks": clicks,
                "x": x,
                "y": y,
            }
        )
    except Exception as e:
        return tool_error(
            code="SCROLL_ERROR",
            message=f"Failed to scroll: {str(e)}",
        )


def desktop_move_to(
    x: int,
    y: int,
    duration: float = 0.25,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """
    Move the mouse to the specified coordinates.

    Args:
        x: X coordinate.
        y: Y coordinate.
        duration: Duration of the movement.
        task_id: Optional task identifier.
        on_log: Optional callback function.

    Returns:
        A dictionary containing success status.
    """
    try:
        if on_log:
            on_log(f"[{task_id}] Moving mouse to ({x}, {y}), duration={duration}")

        pyautogui = _get_pyautogui()
        pyautogui.moveTo(x, y, duration=duration)

        return tool_result(
            data={
                "success": True,
                "x": x,
                "y": y,
                "duration": duration,
            }
        )
    except Exception as e:
        return tool_error(
            code="MOVE_ERROR",
            message=f"Failed to move mouse to ({x}, {y}): {str(e)}",
        )


def desktop_drag_to(
    x: int,
    y: int,
    duration: float = 0.5,
    button: str = "left",
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """
    Drag the mouse to the specified coordinates.

    Args:
        x: X coordinate.
        y: Y coordinate.
        duration: Duration of the drag.
        button: Mouse button to drag with.
        task_id: Optional task identifier.
        on_log: Optional callback function.

    Returns:
        A dictionary containing success status.
    """
    try:
        if on_log:
            on_log(f"[{task_id}] Dragging mouse to ({x}, {y}), button={button}, duration={duration}")

        pyautogui = _get_pyautogui()
        pyautogui.dragTo(x, y, duration=duration, button=button)

        return tool_result(
            data={
                "success": True,
                "x": x,
                "y": y,
                "duration": duration,
                "button": button,
            }
        )
    except Exception as e:
        return tool_error(
            code="DRAG_ERROR",
            message=f"Failed to drag mouse to ({x}, {y}): {str(e)}",
        )


def desktop_get_position(
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """
    Get the current mouse position.

    Args:
        task_id: Optional task identifier.
        on_log: Optional callback function.

    Returns:
        A dictionary containing success status and position.
    """
    try:
        pyautogui = _get_pyautogui()
        x, y = pyautogui.position()

        if on_log:
            on_log(f"[{task_id}] Current mouse position: ({x}, {y})")

        return tool_result(
            data={
                "success": True,
                "x": x,
                "y": y,
            }
        )
    except Exception as e:
        return tool_error(
            code="POSITION_ERROR",
            message=f"Failed to get mouse position: {str(e)}",
        )


def register_desktop_tools() -> None:
    registry.register(
        name="desktop_screenshot",
        handler=desktop_screenshot,
        description="Capture a screenshot of the full screen or a specified region.",
        parameters={
            "type": "object",
            "properties": {
                "region": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional tuple (left, top, width, height) to capture a specific region.",
                },
                "task_id": {"type": "string", "description": "Optional task identifier for logging.", "default": ""},
            },
        },
        toolset="desktop",
        emoji="🖥️",
    )

    registry.register(
        name="desktop_click",
        handler=desktop_click,
        description="Perform a mouse click at the specified coordinates.",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate."},
                "y": {"type": "integer", "description": "Y coordinate."},
                "clicks": {"type": "integer", "description": "Number of clicks.", "default": 1},
                "interval": {"type": "number", "description": "Interval between clicks.", "default": 0.0},
                "button": {"type": "string", "description": "Mouse button ('left', 'right', 'middle').", "default": "left"},
                "task_id": {"type": "string", "description": "Optional task identifier.", "default": ""},
            },
            "required": ["x", "y"],
        },
        toolset="desktop",
        emoji="🖱️",
    )

    registry.register(
        name="desktop_type",
        handler=desktop_type,
        description="Type text using the keyboard.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type."},
                "interval": {"type": "number", "description": "Interval between key presses.", "default": 0.01},
                "task_id": {"type": "string", "description": "Optional task identifier.", "default": ""},
            },
            "required": ["text"],
        },
        toolset="desktop",
        emoji="⌨️",
    )

    registry.register(
        name="desktop_press",
        handler=desktop_press,
        description="Press a sequence of keys.",
        parameters={
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of keys to press (e.g., ['ctrl', 'c']).",
                },
                "task_id": {"type": "string", "description": "Optional task identifier.", "default": ""},
            },
            "required": ["keys"],
        },
        toolset="desktop",
        emoji="🔘",
    )

    registry.register(
        name="desktop_scroll",
        handler=desktop_scroll,
        description="Scroll the mouse wheel.",
        parameters={
            "type": "object",
            "properties": {
                "clicks": {"type": "integer", "description": "Number of clicks (positive for up, negative for down)."},
                "x": {"type": "integer", "description": "Optional X coordinate to move to before scrolling.", "default": None},
                "y": {"type": "integer", "description": "Optional Y coordinate to move to before scrolling.", "default": None},
                "task_id": {"type": "string", "description": "Optional task identifier.", "default": ""},
            },
            "required": ["clicks"],
        },
        toolset="desktop",
        emoji="🔄",
    )

    registry.register(
        name="desktop_move_to",
        handler=desktop_move_to,
        description="Move the mouse to the specified coordinates.",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate."},
                "y": {"type": "integer", "description": "Y coordinate."},
                "duration": {"type": "number", "description": "Duration of the movement.", "default": 0.25},
                "task_id": {"type": "string", "description": "Optional task identifier.", "default": ""},
            },
            "required": ["x", "y"],
        },
        toolset="desktop",
        emoji="🎯",
    )

    registry.register(
        name="desktop_drag_to",
        handler=desktop_drag_to,
        description="Drag the mouse to the specified coordinates.",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate."},
                "y": {"type": "integer", "description": "Y coordinate."},
                "duration": {"type": "number", "description": "Duration of the drag.", "default": 0.5},
                "button": {"type": "string", "description": "Mouse button to drag with.", "default": "left"},
                "task_id": {"type": "string", "description": "Optional task identifier.", "default": ""},
            },
            "required": ["x", "y"],
        },
        toolset="desktop",
        emoji="✋",
    )

    registry.register(
        name="desktop_get_position",
        handler=desktop_get_position,
        description="Get the current mouse position.",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Optional task identifier.", "default": ""},
            },
        },
        toolset="desktop",
        emoji="📍",
    )


register_desktop_tools()
