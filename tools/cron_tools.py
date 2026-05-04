import sqlite3
import json
import uuid
from pathlib import Path
from typing import Any, Callable, Optional
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from tools.registry import registry, tool_result, tool_error


_DB_PATH: Path = Path(__file__).parent.parent / "memories.db"
_scheduler: Optional[BackgroundScheduler] = None


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接，初始化表结构。"""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cron_jobs (
            id TEXT PRIMARY KEY,
            cron_expr TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            tool_args_json TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            next_run_time TEXT
        )
    """)
    conn.commit()
    return conn


def _init_scheduler() -> None:
    """初始化调度器并从数据库恢复任务。"""
    global _scheduler
    if _scheduler is not None:
        return
    
    _scheduler = BackgroundScheduler()
    _scheduler.start()
    
    conn = _get_conn()
    try:
        cursor = conn.execute("SELECT id, cron_expr, tool_name, tool_args_json FROM cron_jobs")
        for row in cursor.fetchall():
            job_id, cron_expr, tool_name, tool_args_json = row
            tool_args = json.loads(tool_args_json)
            _add_job_to_scheduler(job_id, cron_expr, tool_name, tool_args)
    finally:
        conn.close()


def _add_job_to_scheduler(job_id: str, cron_expr: str, tool_name: str, tool_args: dict) -> None:
    """向调度器添加任务。"""
    if _scheduler is None:
        _init_scheduler()
    
    def job_func() -> None:
        handler = registry.get_handler(tool_name)
        if handler:
            try:
                handler(**tool_args)
            except Exception as e:
                pass
    
    parts = cron_expr.split()
    if len(parts) < 5:
        raise ValueError(f"Invalid cron expression: {cron_expr}")
    
    trigger = CronTrigger(
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=parts[4]
    )
    
    _scheduler.add_job(job_func, trigger=trigger, id=job_id, replace_existing=True)


def _update_next_run_time(job_id: str) -> None:
    """更新任务下次运行时间。"""
    if _scheduler is None:
        return
    
    job = _scheduler.get_job(job_id)
    if job:
        next_run = job.next_run_time.isoformat() if job.next_run_time else None
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE cron_jobs SET next_run_time = ? WHERE id = ?",
                (next_run, job_id)
            )
            conn.commit()
        finally:
            conn.close()


def schedule_task(
    cron_expr: str,
    tool_name: str,
    tool_args: dict,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """注册定时任务。

    Args:
        cron_expr: cron 表达式（分 时 日 月 周），例如 "0 9 * * *"。
        tool_name: 要调用的工具名称。
        tool_args: 工具参数字典。
        task_id: 任务 ID（暂未使用）。
        on_log: 日志回调函数。

    Returns:
        包含 job_id 的字典。
    """
    try:
        if registry.get_handler(tool_name) is None:
            return tool_error("INVALID_TOOL", f"Tool not found: {tool_name}")
        
        job_id = str(uuid.uuid4())
        tool_args_json = json.dumps(tool_args)
        
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT INTO cron_jobs (id, cron_expr, tool_name, tool_args_json) VALUES (?, ?, ?, ?)",
                (job_id, cron_expr, tool_name, tool_args_json)
            )
            conn.commit()
        finally:
            conn.close()
        
        _add_job_to_scheduler(job_id, cron_expr, tool_name, tool_args)
        _update_next_run_time(job_id)
        
        if on_log:
            on_log(f"⏰ 定时任务已注册: {job_id}")
        
        return tool_result({"job_id": job_id, "cron_expr": cron_expr, "tool_name": tool_name})
    except Exception as e:
        return tool_error("SCHEDULE_ERROR", f"Failed to schedule task: {e}")


def list_scheduled_tasks(
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """列出所有定时任务。

    Args:
        task_id: 任务 ID（暂未使用）。
        on_log: 日志回调函数。

    Returns:
        包含任务列表的字典。
    """
    try:
        conn = _get_conn()
        try:
            cursor = conn.execute(
                "SELECT id, cron_expr, tool_name, tool_args_json, created_at, next_run_time FROM cron_jobs ORDER BY created_at DESC"
            )
            jobs = []
            for row in cursor.fetchall():
                job_id, cron_expr, tool_name, tool_args_json, created_at, next_run_time = row
                jobs.append({
                    "id": job_id,
                    "cron_expr": cron_expr,
                    "tool_name": tool_name,
                    "tool_args": json.loads(tool_args_json),
                    "created_at": created_at,
                    "next_run_time": next_run_time
                })
            return tool_result({"jobs": jobs})
        finally:
            conn.close()
    except Exception as e:
        return tool_error("LIST_ERROR", f"Failed to list tasks: {e}")


def unschedule_task(
    job_id: str,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """删除定时任务。

    Args:
        job_id: 任务 ID。
        task_id: 任务 ID（暂未使用）。
        on_log: 日志回调函数。

    Returns:
        操作结果。
    """
    try:
        if _scheduler is not None:
            job = _scheduler.get_job(job_id)
            if job:
                _scheduler.remove_job(job_id)
        
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
            conn.commit()
        finally:
            conn.close()
        
        if on_log:
            on_log(f"❌ 定时任务已删除: {job_id}")
        
        return tool_result({"job_id": job_id, "removed": True})
    except Exception as e:
        return tool_error("UNSCHEDULE_ERROR", f"Failed to unschedule task: {e}")


def register_all() -> None:
    """注册所有定时任务工具。"""
    registry.register(
        name="schedule_task",
        handler=schedule_task,
        description="注册定时任务，使用 cron 表达式",
        parameters={
            "type": "object",
            "properties": {
                "cron_expr": {"type": "string", "description": "cron 表达式，例如 '0 9 * * *'"},
                "tool_name": {"type": "string", "description": "要调用的工具名称"},
                "tool_args": {"type": "object", "description": "工具参数字典"},
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["cron_expr", "tool_name", "tool_args"],
        },
        toolset="cron",
        emoji="⏰",
    )
    
    registry.register(
        name="list_scheduled_tasks",
        handler=list_scheduled_tasks,
        description="列出所有定时任务",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID"},
            },
        },
        toolset="cron",
        emoji="📋",
    )
    
    registry.register(
        name="unschedule_task",
        handler=unschedule_task,
        description="删除定时任务",
        parameters={
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "任务 ID"},
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["job_id"],
        },
        toolset="cron",
        emoji="❌",
    )


_init_scheduler()
register_all()
