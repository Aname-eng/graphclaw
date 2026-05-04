from typing import Any, Callable, Optional, List, Dict

from tools.registry import registry, tool_result, tool_error


def list_checkpoints(
    thread_id: str,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """列出指定线程的所有检查点。

    Args:
        thread_id: 线程 ID。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含检查点列表的字典。
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        import os

        checkpointer = None
        try:
            checkpoint_db_path = os.getenv("CHECKPOINT_DB_PATH", "./checkpoints.db")
            os.makedirs(os.path.dirname(checkpoint_db_path) or ".", exist_ok=True)
            checkpointer = SqliteSaver.from_conn_string(checkpoint_db_path)
        except Exception:
            if on_log:
                on_log("⚠️ 无法连接到 checkpointer，返回空列表")
            return tool_result({"checkpoints": [], "count": 0})

        checkpoints = []
        try:
            if hasattr(checkpointer, "list"):
                for config in checkpointer.list({"configurable": {"thread_id": thread_id}}):
                    checkpoint_info = {
                        "checkpoint_id": config.get("configurable", {}).get("checkpoint_id", ""),
                        "thread_id": thread_id,
                        "created_at": config.get("metadata", {}).get("created_at", ""),
                        "step": config.get("metadata", {}).get("step", 0),
                        "parent_checkpoint_id": config.get("configurable", {}).get("parent_checkpoint_id", None),
                    }
                    checkpoints.append(checkpoint_info)
        except Exception as e:
            if on_log:
                on_log(f"⚠️ 列出检查点时出错: {e}")

        if on_log:
            on_log(f"📋 找到 {len(checkpoints)} 个检查点")

        return tool_result({
            "checkpoints": checkpoints,
            "count": len(checkpoints),
            "thread_id": thread_id
        })
    except ImportError:
        if on_log:
            on_log("⚠️ LangGraph 检查点功能不可用")
        return tool_error("LANGGRAPH_NOT_AVAILABLE", "LangGraph checkpoint API is not available")
    except Exception as e:
        return tool_error("LIST_CHECKPOINTS_ERROR", f"Failed to list checkpoints: {e}")


def restore_checkpoint(
    thread_id: str,
    checkpoint_id: str,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """恢复到指定的检查点。

    Args:
        thread_id: 线程 ID。
        checkpoint_id: 检查点 ID。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含恢复结果的字典。
    """
    try:
        if on_log:
            on_log(f"⏪ 准备恢复到检查点: {checkpoint_id}")

        return tool_result({
            "thread_id": thread_id,
            "checkpoint_id": checkpoint_id,
            "status": "pending",
            "message": "Checkpoint restore requested - please restart the graph with this checkpoint",
            "next_action": "Use this checkpoint_id in your next graph invocation config"
        })
    except Exception as e:
        return tool_error("RESTORE_CHECKPOINT_ERROR", f"Failed to restore checkpoint: {e}")


def prune_checkpoints(
    thread_id: str,
    keep_last: int = 10,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """清理旧检查点，只保留最近的 N 个。

    Args:
        thread_id: 线程 ID。
        keep_last: 保留的检查点数量，默认为 10。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含清理结果的字典。
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        import os

        checkpointer = None
        try:
            checkpoint_db_path = os.getenv("CHECKPOINT_DB_PATH", "./checkpoints.db")
            os.makedirs(os.path.dirname(checkpoint_db_path) or ".", exist_ok=True)
            checkpointer = SqliteSaver.from_conn_string(checkpoint_db_path)
        except Exception:
            if on_log:
                on_log("⚠️ 无法连接到 checkpointer，无法清理")
            return tool_result({"pruned": 0, "kept": 0, "thread_id": thread_id})

        checkpoints = []
        try:
            if hasattr(checkpointer, "list"):
                for config in checkpointer.list({"configurable": {"thread_id": thread_id}}):
                    checkpoint_info = {
                        "checkpoint_id": config.get("configurable", {}).get("checkpoint_id", ""),
                        "step": config.get("metadata", {}).get("step", 0),
                        "created_at": config.get("metadata", {}).get("created_at", ""),
                    }
                    checkpoints.append(checkpoint_info)
        except Exception as e:
            if on_log:
                on_log(f"⚠️ 列出检查点时出错: {e}")
            return tool_result({"pruned": 0, "kept": len(checkpoints), "thread_id": thread_id})

        checkpoints.sort(key=lambda x: x.get("step", 0), reverse=True)
        to_keep = checkpoints[:keep_last]
        to_prune = checkpoints[keep_last:]

        pruned_count = 0
        for cp in to_prune:
            try:
                cp_id = cp.get("checkpoint_id")
                if cp_id and hasattr(checkpointer, "delete"):
                    checkpointer.delete({"configurable": {"thread_id": thread_id, "checkpoint_id": cp_id}})
                    pruned_count += 1
            except Exception:
                continue

        if on_log:
            on_log(f"✂️ 已清理 {pruned_count} 个旧检查点，保留 {len(to_keep)} 个")

        return tool_result({
            "pruned": pruned_count,
            "kept": len(to_keep),
            "total_before": len(checkpoints),
            "thread_id": thread_id
        })
    except ImportError:
        if on_log:
            on_log("⚠️ LangGraph 检查点功能不可用")
        return tool_error("LANGGRAPH_NOT_AVAILABLE", "LangGraph checkpoint API is not available")
    except Exception as e:
        return tool_error("PRUNE_CHECKPOINTS_ERROR", f"Failed to prune checkpoints: {e}")


def register_all() -> None:
    """注册所有检查点管理工具。"""
    registry.register(
        name="list_checkpoints",
        handler=list_checkpoints,
        description="列出指定线程的所有检查点",
        parameters={
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "线程 ID"},
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["thread_id"],
        },
        toolset="checkpoint",
        emoji="📋",
    )

    registry.register(
        name="restore_checkpoint",
        handler=restore_checkpoint,
        description="恢复到指定的检查点",
        parameters={
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "线程 ID"},
                "checkpoint_id": {"type": "string", "description": "检查点 ID"},
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["thread_id", "checkpoint_id"],
        },
        toolset="checkpoint",
        emoji="⏪",
    )

    registry.register(
        name="prune_checkpoints",
        handler=prune_checkpoints,
        description="清理旧检查点，只保留最近的 N 个",
        parameters={
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "线程 ID"},
                "keep_last": {"type": "integer", "default": 10, "description": "保留的检查点数量"},
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["thread_id"],
        },
        toolset="checkpoint",
        emoji="✂️",
    )


register_all()
