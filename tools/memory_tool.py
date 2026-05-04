import sqlite3
import json
from pathlib import Path
from typing import Any, Callable, Optional

from tools.registry import registry, tool_result, tool_error


_DB_PATH: Path = Path(__file__).parent.parent / "memories.db"


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接，初始化表结构。"""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            key TEXT PRIMARY KEY,
            value TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            key,
            value,
            content='memories',
            content_rowid='rowid'
        )
    """)
    conn.commit()
    return conn


def memory_save(key: str, value: str, task_id: str = "", on_log: Optional[Callable[..., Any]] = None) -> dict[str, Any]:
    """保存键值对到数据库，同时更新 FTS 表。

    Args:
        key: 记忆的键名。
        value: 记忆的值。
        task_id: 任务 ID（暂未使用）。
        on_log: 日志回调函数。

    Returns:
        包含 success=True 的字典。
    """
    try:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO memories (key, value, timestamp) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (key, value),
            )
            conn.execute("DELETE FROM memories_fts WHERE key = ?", (key,))
            conn.execute(
                "INSERT INTO memories_fts (key, value) VALUES (?, ?)",
                (key, value),
            )
            conn.commit()
            return tool_result({"key": key, "saved": True})
        finally:
            conn.close()
    except Exception as e:
        return tool_error("MEMORY_SAVE_ERROR", f"Failed to save memory: {e}")


def memory_load(key: str, task_id: str = "", on_log: Optional[Callable[..., Any]] = None) -> dict[str, Any]:
    """按 key 加载值，如果值是 JSON 格式则解析。

    Args:
        key: 记忆的键名。
        task_id: 任务 ID（暂未使用）。
        on_log: 日志回调函数。

    Returns:
        包含 {key: value} 或 {key: None} 的字典。
    """
    try:
        conn = _get_conn()
        try:
            cursor = conn.execute("SELECT value FROM memories WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row is None:
                return tool_result({key: None})
            raw_value = row[0]
            try:
                parsed_value = json.loads(raw_value)
            except (json.JSONDecodeError, TypeError):
                parsed_value = raw_value
            return tool_result({key: parsed_value})
        finally:
            conn.close()
    except Exception as e:
        return tool_error("MEMORY_LOAD_ERROR", f"Failed to load memory: {e}")


def memory_search(query: str, task_id: str = "", on_log: Optional[Callable[..., Any]] = None) -> dict[str, Any]:
    """使用 FTS5 全文检索搜索记忆。

    Args:
        query: 搜索查询字符串。
        task_id: 任务 ID（暂未使用）。
        on_log: 日志回调函数。

    Returns:
        包含搜索结果列表的字典，每项为 {"key": k, "value": v}。
    """
    try:
        conn = _get_conn()
        try:
            cursor = conn.execute(
                """
                SELECT m.key, m.value
                FROM memories m
                JOIN memories_fts fts ON m.key = fts.key
                WHERE memories_fts MATCH ?
                LIMIT 20
                """,
                (query,),
            )
            rows = cursor.fetchall()
            results = [{"key": r[0], "value": r[1]} for r in rows]
            return tool_result({"results": results})
        finally:
            conn.close()
    except Exception as e:
        return tool_error("MEMORY_SEARCH_ERROR", f"Failed to search memories: {e}")


registry.register(
    name="memory_save",
    handler=memory_save,
    description="保存键值对到记忆数据库",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "记忆的键名"},
            "value": {"type": "string", "description": "记忆的值"},
            "task_id": {"type": "string", "description": "任务 ID"},
        },
        "required": ["key", "value"],
    },
    toolset="memory",
    emoji="🧠",
)

registry.register(
    name="memory_load",
    handler=memory_load,
    description="按 key 加载记忆值",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "记忆的键名"},
            "task_id": {"type": "string", "description": "任务 ID"},
        },
        "required": ["key"],
    },
    toolset="memory",
    emoji="🧠",
)

registry.register(
    name="memory_search",
    handler=memory_search,
    description="使用 FTS5 全文检索搜索记忆",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询字符串"},
            "task_id": {"type": "string", "description": "任务 ID"},
        },
        "required": ["query"],
    },
    toolset="memory",
    emoji="🔎",
)
