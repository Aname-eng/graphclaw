from typing import Any, Callable, Optional

from tools.registry import registry, tool_result, tool_error


_interrupt_flags: dict[str, bool] = {}


def set_interrupt_flag(task_id: str, value: bool = True) -> None:
    """设置指定任务的中断标志。

    Args:
        task_id: 任务 ID。
        value: 中断标志值，默认为 True。
    """
    _interrupt_flags[task_id] = value


def is_interrupted(task_id: str) -> bool:
    """检查指定任务是否被中断。

    Args:
        task_id: 任务 ID。

    Returns:
        如果任务被中断返回 True，否则返回 False。
    """
    return _interrupt_flags.get(task_id, False)


def clear_interrupt(task_id: str) -> None:
    """清除指定任务的中断标志。

    Args:
        task_id: 任务 ID。
    """
    if task_id in _interrupt_flags:
        del _interrupt_flags[task_id]


def interrupt_current_task(task_id: str = "", on_log: Optional[Callable[..., Any]] = None) -> dict[str, Any]:
    """中断当前正在执行的任务。

    Args:
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含 success=True 的字典。
    """
    try:
        if not task_id:
            return tool_error("MISSING_TASK_ID", "task_id is required to interrupt a task")
        
        set_interrupt_flag(task_id)
        if on_log:
            on_log(f"⏸️ 任务 {task_id} 已标记为中断")
        
        return tool_result({"task_id": task_id, "interrupted": True})
    except Exception as e:
        return tool_error("INTERRUPT_ERROR", f"Failed to interrupt task: {e}")


def register_all() -> None:
    """注册所有中断工具到全局 registry。"""
    registry.register(
        name="interrupt_current_task",
        handler=interrupt_current_task,
        description="中断当前正在执行的任务",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": [],
        },
        toolset="system",
        emoji="⏸️",
    )


register_all()
