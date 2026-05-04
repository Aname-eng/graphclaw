import sqlite3
from pathlib import Path
from typing import Any, Callable, Optional

from tools.registry import registry, tool_result, tool_error


_DB_PATH: Path = Path(__file__).parent.parent / "budget.db"


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接，初始化表结构。"""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            key TEXT PRIMARY KEY,
            limit REAL NOT NULL,
            usage REAL DEFAULT 0.0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def set_budget(
    key: str,
    limit: float,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """设置预算。

    Args:
        key: 预算键名（如 "api_tokens", "api_cost"）。
        limit: 预算限额。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含 success=True 的字典。
    """
    try:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO budgets (key, limit, usage, timestamp) VALUES (?, ?, COALESCE((SELECT usage FROM budgets WHERE key = ?), 0.0), CURRENT_TIMESTAMP)",
                (key, limit, key),
            )
            conn.commit()
            return tool_result({"key": key, "limit": limit, "set": True})
        finally:
            conn.close()
    except Exception as e:
        return tool_error("BUDGET_SET_ERROR", f"Failed to set budget: {e}")


def get_budget(
    key: str,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """获取预算信息。

    Args:
        key: 预算键名。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含预算信息的字典。
    """
    try:
        conn = _get_conn()
        try:
            cursor = conn.execute("SELECT limit, usage FROM budgets WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row is None:
                return tool_result({"key": key, "limit": None, "usage": None})
            limit, usage = row
            return tool_result({"key": key, "limit": limit, "usage": usage})
        finally:
            conn.close()
    except Exception as e:
        return tool_error("BUDGET_GET_ERROR", f"Failed to get budget: {e}")


def check_budget(
    key: str,
    usage: float,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """检查并更新预算使用量。

    Args:
        key: 预算键名。
        usage: 新增使用量。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含检查结果的字典。
    """
    try:
        conn = _get_conn()
        try:
            cursor = conn.execute("SELECT limit, usage FROM budgets WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row is None:
                return tool_result({
                    "key": key,
                    "within_budget": True,
                    "message": "No budget set for this key",
                })

            limit, current_usage = row
            new_usage = current_usage + usage
            within_budget = new_usage <= limit

            conn.execute(
                "UPDATE budgets SET usage = ? WHERE key = ?",
                (new_usage, key),
            )
            conn.commit()

            return tool_result({
                "key": key,
                "limit": limit,
                "usage": new_usage,
                "remaining": limit - new_usage,
                "within_budget": within_budget,
            })
        finally:
            conn.close()
    except Exception as e:
        return tool_error("BUDGET_CHECK_ERROR", f"Failed to check budget: {e}")


def reset_budget_usage(
    key: str,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """重置预算使用量。

    Args:
        key: 预算键名。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含 success=True 的字典。
    """
    try:
        conn = _get_conn()
        try:
            conn.execute("UPDATE budgets SET usage = 0.0 WHERE key = ?", (key,))
            conn.commit()
            return tool_result({"key": key, "reset": True})
        finally:
            conn.close()
    except Exception as e:
        return tool_error("BUDGET_RESET_ERROR", f"Failed to reset budget: {e}")


def list_budgets(
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """列出所有预算。

    Args:
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含预算列表的字典。
    """
    try:
        conn = _get_conn()
        try:
            cursor = conn.execute("SELECT key, limit, usage FROM budgets ORDER BY key")
            rows = cursor.fetchall()
            budgets = [
                {
                    "key": r[0],
                    "limit": r[1],
                    "usage": r[2],
                    "remaining": r[1] - r[2],
                }
                for r in rows
            ]
            return tool_result({"budgets": budgets})
        finally:
            conn.close()
    except Exception as e:
        return tool_error("BUDGET_LIST_ERROR", f"Failed to list budgets: {e}")


def register_all() -> None:
    """注册所有预算控制工具到全局 registry。"""
    registry.register(
        name="set_budget",
        handler=set_budget,
        description="设置预算限额",
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "预算键名（如 'api_tokens', 'api_cost'）"},
                "limit": {"type": "number", "description": "预算限额"},
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["key", "limit"],
        },
        toolset="budget",
        emoji="💰",
    )

    registry.register(
        name="get_budget",
        handler=get_budget,
        description="获取预算信息（限额和当前使用量）",
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "预算键名"},
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["key"],
        },
        toolset="budget",
        emoji="📊",
    )

    registry.register(
        name="check_budget",
        handler=check_budget,
        description="检查并更新预算使用量",
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "预算键名"},
                "usage": {"type": "number", "description": "新增使用量"},
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["key", "usage"],
        },
        toolset="budget",
        emoji="✅",
    )

    registry.register(
        name="reset_budget_usage",
        handler=reset_budget_usage,
        description="重置预算使用量为 0",
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "预算键名"},
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["key"],
        },
        toolset="budget",
        emoji="🔄",
    )

    registry.register(
        name="list_budgets",
        handler=list_budgets,
        description="列出所有预算",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID"},
            },
        },
        toolset="budget",
        emoji="📋",
    )
