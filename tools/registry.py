from typing import Any, Callable, Optional
from dataclasses import dataclass, field


@dataclass
class ToolResult:
    success: bool
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result = {"success": self.success}
        if self.data is not None:
            result["data"] = self.data
        if self.error is not None:
            result["error"] = self.error
        return result


@dataclass
class ToolError:
    code: str
    message: str
    details: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        result = {"code": self.code, "message": self.message}
        if self.details is not None:
            result["details"] = self.details
        return result


def tool_result(data: Optional[dict[str, Any]] = None, error: Optional[str] = None) -> dict[str, Any]:
    if error is not None:
        return ToolResult(success=False, error=error).to_dict()
    return ToolResult(success=True, data=data).to_dict()


def tool_error(code: str, message: str, details: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return ToolError(code=code, message=message, details=details).to_dict()


@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any]
    toolset: str = "default"
    emoji: str = "🔧"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "toolset": self.toolset,
            "emoji": self.emoji,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSchema] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}

    def register(
        self,
        name: str,
        handler: Callable[..., Any],
        description: str,
        parameters: dict[str, Any],
        toolset: str = "default",
        emoji: str = "🔧",
    ) -> None:
        schema = ToolSchema(
            name=name,
            description=description,
            parameters=parameters,
            toolset=toolset,
            emoji=emoji,
        )
        self._tools[name] = schema
        self._handlers[name] = handler

    def get_schema(self, name: str) -> Optional[ToolSchema]:
        return self._tools.get(name)

    def get_handler(self, name: str) -> Optional[Callable[..., Any]]:
        return self._handlers.get(name)

    def list_tools(self, toolset: Optional[str] = None) -> list[dict[str, Any]]:
        tools = list(self._tools.values())
        if toolset is not None:
            tools = [t for t in tools if t.toolset == toolset]
        return [t.to_dict() for t in tools]

    def get_all_handlers(self) -> dict[str, Callable[..., Any]]:
        return self._handlers.copy()

    def get_all_tool_names(self) -> list[str]:
        """返回所有已注册工具的名称列表。"""
        return list(self._tools.keys())


registry = ToolRegistry()
