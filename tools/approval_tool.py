import time
import os
import json
from typing import Any, Callable, Optional

from tools.registry import registry, tool_result, tool_error


_pending_approvals: dict[str, dict[str, Any]] = {}
_approved_commands_session: set[str] = set()  # 本次对话内批准的命令
_approved_commands_permanent: set[str] = set()  # 永久批准的命令
APPROVAL_CONFIG_FILE = "approved_commands.json"


def _load_permanent_approvals() -> None:
    """加载永久批准的命令列表"""
    try:
        if os.path.exists(APPROVAL_CONFIG_FILE):
            with open(APPROVAL_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                _approved_commands_permanent.update(data.get("permanent", []))
    except Exception:
        pass


_load_permanent_approvals()


def _save_permanent_approvals() -> None:
    """保存永久批准的命令列表"""
    try:
        with open(APPROVAL_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"permanent": list(_approved_commands_permanent)}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _get_command_hash(operation: str, details: str) -> str:
    """生成命令的哈希值，用于比较"""
    import hashlib
    content = f"{operation}|{details}"
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def is_command_approved(operation: str, details: str) -> bool:
    """检查命令是否已经被批准"""
    cmd_hash = _get_command_hash(operation, details)
    return cmd_hash in _approved_commands_session or cmd_hash in _approved_commands_permanent


def request_approval(
    operation: str,
    details: str,
    risk_level: str = "medium",
    timeout_seconds: int = 120,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """请求用户审批。

    Args:
        operation: 要执行的操作描述。
        details: 操作的详细信息。
        risk_level: 风险等级（"low" | "medium" | "high"）。
        timeout_seconds: 等待审批的超时时间（秒）。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含审批结果的字典。
    """
    try:
        # 先检查是否已经批准
        if is_command_approved(operation, details):
            return tool_result({
                "approval_id": None,
                "status": "already_approved",
                "message": "Command already approved"
            })

        approval_id = task_id or str(int(time.time() * 1000))
        _pending_approvals[approval_id] = {
            "operation": operation,
            "details": details,
            "risk_level": risk_level,
            "created_at": time.time(),
            "timeout_seconds": timeout_seconds,
            "cmd_hash": _get_command_hash(operation, details)
        }

        if on_log:
            on_log(
                f"🔒 审批请求 [{approval_id}]\n"
                f"操作: {operation}\n"
                f"详情: {details}\n"
                f"风险等级: {risk_level}\n"
                f"请选择一个选项响应:\n"
                f"1. /approve {approval_id} - 仅本次批准\n"
                f"2. /approve-session {approval_id} - 本次对话内批准\n"
                f"3. /approve-permanent {approval_id} - 永久批准\n"
                f"4. /reject {approval_id} - 拒绝执行"
            )

        return tool_result({
            "approval_id": approval_id,
            "status": "pending",
            "message": "Approval request submitted",
            "options": ["本次批准", "本次对话内批准", "永久批准", "拒绝"]
        })
    except Exception as e:
        return tool_error("APPROVAL_REQUEST_ERROR", f"Failed to submit approval request: {e}")


def approve_task(
    task_id: str,
    reason: str = "Approved by user",
    approval_type: str = "once",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """批准待审批的任务。

    Args:
        task_id: 任务 ID。
        reason: 批准原因。
        approval_type: 批准类型 ("once" | "session" | "permanent")。
        on_log: 日志回调函数。

    Returns:
        包含操作结果的字典。
    """
    try:
        if task_id not in _pending_approvals:
            return tool_error("APPROVAL_NOT_FOUND", f"No pending approval found for task_id: {task_id}")

        approval = _pending_approvals.pop(task_id)
        cmd_hash = approval.get("cmd_hash")

        # 根据批准类型记录
        approval_type_desc = "本次批准"
        if approval_type == "once":
            # 本次批准也加入到本次对话批准列表，但标记为一次性
            if cmd_hash:
                _approved_commands_session.add(cmd_hash)
            approval_type_desc = "本次批准"
        elif approval_type == "session":
            if cmd_hash:
                _approved_commands_session.add(cmd_hash)
            approval_type_desc = "本次对话内批准"
        elif approval_type == "permanent":
            if cmd_hash:
                _approved_commands_permanent.add(cmd_hash)
                _save_permanent_approvals()
            approval_type_desc = "永久批准"

        if on_log:
            on_log(f"✅ 任务 [{task_id}] 已批准 ({approval_type_desc}): {reason}")

        return tool_result({
            "approval_id": task_id,
            "approved": True,
            "reason": reason,
            "operation": approval["operation"],
            "approval_type": approval_type,
            "approval_type_desc": approval_type_desc
        })
    except Exception as e:
        return tool_error("APPROVAL_APPROVE_ERROR", f"Failed to approve task: {e}")


def reject_task(
    task_id: str,
    reason: str = "Rejected by user",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """拒绝待审批的任务。

    Args:
        task_id: 任务 ID。
        reason: 拒绝原因。
        on_log: 日志回调函数。

    Returns:
        包含操作结果的字典。
    """
    try:
        if task_id not in _pending_approvals:
            return tool_error("APPROVAL_NOT_FOUND", f"No pending approval found for task_id: {task_id}")

        approval = _pending_approvals.pop(task_id)

        if on_log:
            on_log(f"❌ 任务 [{task_id}] 已拒绝: {reason}")

        return tool_result({
            "approval_id": task_id,
            "approved": False,
            "reason": reason,
            "operation": approval["operation"]
        })
    except Exception as e:
        return tool_error("APPROVAL_REJECT_ERROR", f"Failed to reject task: {e}")


def get_pending_approvals(
    task_id: Optional[str] = None,
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """获取待审批的任务列表。

    Args:
        task_id: 可选的任务 ID，只返回指定任务的审批信息。
        on_log: 日志回调函数。

    Returns:
        包含待审批任务的字典。
    """
    try:
        if task_id:
            if task_id not in _pending_approvals:
                return tool_error("APPROVAL_NOT_FOUND", f"No pending approval found for task_id: {task_id}")
            return tool_result({"approvals": {task_id: _pending_approvals[task_id]}})

        return tool_result({"approvals": _pending_approvals.copy()})
    except Exception as e:
        return tool_error("APPROVAL_LIST_ERROR", f"Failed to list pending approvals: {e}")


def register_all() -> None:
    """注册所有审批工具。"""
    registry.register(
        name="request_approval",
        handler=request_approval,
        description="请求用户审批高风险操作",
        parameters={
            "type": "object",
            "properties": {
                "operation": {"type": "string", "description": "要执行的操作描述"},
                "details": {"type": "string", "description": "操作的详细信息"},
                "risk_level": {"type": "string", "enum": ["low", "medium", "high"], "description": "风险等级"},
                "timeout_seconds": {"type": "integer", "default": 120, "description": "超时时间（秒）"},
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["operation", "details"],
        },
        toolset="approval",
        emoji="🔒",
    )

    registry.register(
        name="approve_task",
        handler=approve_task,
        description="批准待审批的任务",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID"},
                "reason": {"type": "string", "description": "批准原因"},
                "approval_type": {
                    "type": "string", 
                    "enum": ["once", "session", "permanent"], 
                    "description": "批准类型：once(仅本次)|session(本次对话内)|permanent(永久)", 
                    "default": "once"
                },
            },
            "required": ["task_id"],
        },
        toolset="approval",
        emoji="✅",
    )

    registry.register(
        name="reject_task",
        handler=reject_task,
        description="拒绝待审批的任务",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID"},
                "reason": {"type": "string", "description": "拒绝原因"},
            },
            "required": ["task_id"],
        },
        toolset="approval",
        emoji="❌",
    )

    registry.register(
        name="get_pending_approvals",
        handler=get_pending_approvals,
        description="获取待审批的任务列表",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "可选的任务 ID"},
            },
        },
        toolset="approval",
        emoji="📋",
    )


register_all()
