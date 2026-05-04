import difflib
from typing import Any, Callable, Optional

from tools.registry import registry, tool_result, tool_error


def fuzzy_match(
    text: str,
    candidates: list[str],
    threshold: float = 0.6,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """使用 difflib 进行模糊字符串匹配。

    Args:
        text: 要匹配的输入文本。
        candidates: 候选字符串列表。
        threshold: 相似度阈值（0-1），默认 0.6。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含匹配结果列表的字典，按相似度降序排列，每项为 {"text": candidate, "score": similarity}。
    """
    try:
        if not candidates:
            return tool_result({"matches": []})

        matches: list[dict[str, Any]] = []
        for candidate in candidates:
            similarity = difflib.SequenceMatcher(None, text, candidate).ratio()
            if similarity >= threshold:
                matches.append({"text": candidate, "score": similarity})

        matches.sort(key=lambda x: x["score"], reverse=True)
        return tool_result({"matches": matches})
    except Exception as e:
        return tool_error("FUZZY_MATCH_ERROR", f"Failed to perform fuzzy match: {e}")


def fuzzy_find_best(
    text: str,
    candidates: list[str],
    threshold: float = 0.0,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> dict[str, Any]:
    """找到最匹配的单个候选字符串。

    Args:
        text: 要匹配的输入文本。
        candidates: 候选字符串列表。
        threshold: 最小相似度阈值（0-1），默认 0（不限制）。
        task_id: 任务 ID。
        on_log: 日志回调函数。

    Returns:
        包含最佳匹配或 None 的字典。
    """
    try:
        if not candidates:
            return tool_result({"best_match": None, "score": 0.0})

        best_match: Optional[str] = None
        best_score: float = 0.0

        for candidate in candidates:
            similarity = difflib.SequenceMatcher(None, text, candidate).ratio()
            if similarity > best_score:
                best_score = similarity
                best_match = candidate

        if best_score < threshold:
            best_match = None

        return tool_result({"best_match": best_match, "score": best_score})
    except Exception as e:
        return tool_error("FUZZY_FIND_ERROR", f"Failed to find best match: {e}")


def register_all() -> None:
    """注册所有模糊匹配工具到全局 registry。"""
    registry.register(
        name="fuzzy_match",
        handler=fuzzy_match,
        description="使用 difflib 进行模糊字符串匹配，返回符合阈值的所有匹配项",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要匹配的输入文本"},
                "candidates": {"type": "array", "items": {"type": "string"}, "description": "候选字符串列表"},
                "threshold": {"type": "number", "description": "相似度阈值（0-1），默认 0.6", "default": 0.6},
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["text", "candidates"],
        },
        toolset="fuzzy",
        emoji="🎯",
    )

    registry.register(
        name="fuzzy_find_best",
        handler=fuzzy_find_best,
        description="找到最匹配的单个候选字符串",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要匹配的输入文本"},
                "candidates": {"type": "array", "items": {"type": "string"}, "description": "候选字符串列表"},
                "threshold": {"type": "number", "description": "最小相似度阈值（0-1），默认 0", "default": 0.0},
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["text", "candidates"],
        },
        toolset="fuzzy",
        emoji="🏆",
    )
