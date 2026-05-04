import sqlite3
from pathlib import Path
from typing import Any, Callable, Optional

from tools.registry import registry, tool_result, tool_error


_DB_PATH: Path = Path(__file__).parent.parent / "sessions.db"


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接，初始化表结构。"""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            message_role TEXT NOT NULL,
            message_content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
            thread_id,
            message_role,
            message_content,
            content='sessions',
            content_rowid='rowid'
        )
    """)
    conn.commit()
    return conn


def session_add_message(
    thread_id: str,
    message_role: str,
    message_content: str,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """添加会话消息到数据库。

    Args:
        thread_id: 线程/会话 ID。
        message_role: 消息角色（user/assistant/system）。
        message_content: 消息内容。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含 success=True 和消息 ID 的字典。
    """
    try:
        conn = _get_conn()
        try:
            cursor = conn.execute(
                "INSERT INTO sessions (thread_id, message_role, message_content) VALUES (?, ?, ?)",
                (thread_id, message_role, message_content),
            )
            msg_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO sessions_fts (thread_id, message_role, message_content) VALUES (?, ?, ?)",
                (thread_id, message_role, message_content),
            )
            conn.commit()
            return tool_result({"success": True, "message_id": msg_id})
        finally:
            conn.close()
    except Exception as e:
        return tool_error("SESSION_ADD_ERROR", f"Failed to add session message: {e}")


def session_search(
    query: str,
    max_results: int = 20,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """使用 FTS5 全文检索搜索会话历史。

    Args:
        query: 搜索查询字符串。
        max_results: 最多返回结果数，默认 20。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含搜索结果列表的字典。
    """
    try:
        conn = _get_conn()
        try:
            cursor = conn.execute(
                """
                SELECT s.id, s.thread_id, s.message_role, s.message_content, s.timestamp
                FROM sessions s
                JOIN sessions_fts fts ON s.rowid = fts.rowid
                WHERE sessions_fts MATCH ?
                ORDER BY s.timestamp DESC
                LIMIT ?
                """,
                (query, max_results),
            )
            rows = cursor.fetchall()
            results = [
                {
                    "id": r[0],
                    "thread_id": r[1],
                    "role": r[2],
                    "content": r[3],
                    "timestamp": r[4],
                }
                for r in rows
            ]
            return tool_result({"results": results})
        finally:
            conn.close()
    except Exception as e:
        return tool_error("SESSION_SEARCH_ERROR", f"Failed to search sessions: {e}")


def session_list_threads(
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """列出所有会话线程。

    Args:
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含线程列表的字典。
    """
    try:
        conn = _get_conn()
        try:
            cursor = conn.execute(
                "SELECT DISTINCT thread_id FROM sessions ORDER BY timestamp DESC"
            )
            rows = cursor.fetchall()
            threads = [r[0] for r in rows]
            return tool_result({"threads": threads})
        finally:
            conn.close()
    except Exception as e:
        return tool_error("SESSION_LIST_ERROR", f"Failed to list threads: {e}")


def register_all() -> None:
    """注册所有会话搜索工具到全局 registry。"""
    registry.register(
        name="session_add_message",
        handler=session_add_message,
        description="添加消息到会话历史",
        parameters={
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "线程/会话 ID"},
                "message_role": {"type": "string", "description": "消息角色（user/assistant/system）"},
                "message_content": {"type": "string", "description": "消息内容"},
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["thread_id", "message_role", "message_content"],
        },
        toolset="session",
        emoji="💬",
    )

    registry.register(
        name="session_search",
        handler=session_search,
        description="使用全文检索搜索会话历史",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询字符串"},
                "max_results": {"type": "integer", "description": "最多返回结果数", "default": 20},
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["query"],
        },
        toolset="session",
        emoji="🔍",
    )

    registry.register(
        name="session_list_threads",
        handler=session_list_threads,
        description="列出所有会话线程",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID"},
            },
        },
        toolset="session",
        emoji="📋",
    )
