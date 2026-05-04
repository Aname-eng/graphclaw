import os
import time
from pathlib import Path
from typing import Optional, Callable

from tools.registry import registry, tool_result, tool_error
from tools.utils import redact_sensitive_text

# 路径安全控制 - 扩展允许访问的路径范围
_ALLOWED_ROOTS = [os.getcwd(), os.path.expanduser("~"), os.path.expanduser("~/Downloads"), "C:\\", "D:\\"]
_READ_HISTORY: dict[str, list[float]] = {}
_FILE_LOCKS: set[str] = set()


def is_path_allowed(path: str) -> bool:
    """Check if a path is within allowed directories."""
    try:
        resolved = Path(path).resolve()
        for root in _ALLOWED_ROOTS:
            if str(resolved).startswith(str(Path(root).resolve())):
                return True
        return False
    except Exception:
        return False


def check_consecutive_reads(path: str, max_reads: int = 3, window: float = 60.0) -> bool:
    """Check if a file has been read too many times consecutively."""
    now = time.time()
    history = _READ_HISTORY.get(path, [])
    history = [t for t in history if now - t < window]
    _READ_HISTORY[path] = history
    return len(history) >= max_reads


def reset_read_history(path: str) -> None:
    """Reset read history for a path."""
    if path in _READ_HISTORY:
        del _READ_HISTORY[path]


def _lock_path(path: str) -> None:
    """Lock a file path to prevent concurrent writes."""
    _FILE_LOCKS.add(path)


def _unlock_path(path: str) -> None:
    """Unlock a file path."""
    _FILE_LOCKS.discard(path)


def read_file(path: str, max_chars: int = 100000, task_id: str = "", on_log: Optional[Callable] = None) -> dict:
    if not is_path_allowed(path):
        return tool_error(code="ACCESS_DENIED", message="Access denied: path is not allowed")

    if check_consecutive_reads(path):
        return tool_error(code="RATE_LIMIT", message="Too many consecutive reads detected")

    try:
        file_path = Path(path).expanduser()
        if not file_path.exists():
            return tool_error(code="FILE_NOT_FOUND", message=f"File not found: {path}")

        content = file_path.read_text(encoding="utf-8")
        if len(content) > max_chars:
            content = content[:max_chars]

        content = redact_sensitive_text(content)
        reset_read_history(path)

        return tool_result(data={"content": content, "path": path, "size": len(content)})
    except Exception as e:
        return tool_error(code="READ_ERROR", message=f"Error reading file: {str(e)}")


def write_file(path: str, content: str, task_id: str = "", on_log: Optional[Callable] = None) -> dict:
    if not is_path_allowed(path):
        return tool_error(code="ACCESS_DENIED", message="Access denied: path is not allowed")

    _lock_path(path)

    try:
        file_path = Path(path).expanduser()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return tool_result(data={"path": path, "size": len(content)})
    except Exception as e:
        return tool_error(code="WRITE_ERROR", message=f"Error writing file: {str(e)}")
    finally:
        _unlock_path(path)


def patch_file(path: str, old_string: str, new_string: str, task_id: str = "", on_log: Optional[Callable] = None) -> dict:
    if not is_path_allowed(path):
        return tool_error(code="ACCESS_DENIED", message="Access denied: path is not allowed")

    try:
        file_path = Path(path).expanduser()
        if not file_path.exists():
            return tool_error(code="FILE_NOT_FOUND", message=f"File not found: {path}")

        content = file_path.read_text(encoding="utf-8")

        if old_string not in content:
            return tool_error(code="STRING_NOT_FOUND", message="old_string not found in file")

        new_content = content.replace(old_string, new_string, 1)
        file_path.write_text(new_content, encoding="utf-8")

        return tool_result(data={
            "path": path,
            "replaced": True,
            "old_length": len(old_string),
            "new_length": len(new_string)
        })
    except Exception as e:
        return tool_error(code="PATCH_ERROR", message=f"Error patching file: {str(e)}")


def search_files(root_dir: str, pattern: str, content_search: bool = False, task_id: str = "", on_log: Optional[Callable] = None) -> dict:
    if not is_path_allowed(root_dir):
        return tool_error(code="ACCESS_DENIED", message="Access denied: path is not allowed")

    try:
        results = []
        root_path = Path(root_dir).expanduser()

        if not root_path.exists():
            return tool_error(code="DIR_NOT_FOUND", message=f"Directory not found: {root_dir}")

        if content_search:
            for file_path in root_path.rglob("*"):
                if file_path.is_file() and file_path.stat().st_size < 10_000_000:
                    try:
                        content = file_path.read_text(encoding="utf-8", errors="ignore")
                        if pattern in content:
                            results.append(str(file_path))
                            if len(results) >= 100:
                                break
                    except Exception:
                        continue
        else:
            for file_path in root_path.rglob(pattern):
                if file_path.is_file():
                    results.append(str(file_path))
                    if len(results) >= 100:
                        break

        return tool_result(data={"results": results, "count": len(results)})
    except Exception as e:
        return tool_error(code="SEARCH_ERROR", message=f"Error searching files: {str(e)}")


def list_dir(path: str, task_id: str = "", on_log: Optional[Callable] = None) -> dict:
    """List files and directories in a given path."""
    if not is_path_allowed(path):
        return tool_error(code="ACCESS_DENIED", message="Access denied: path is not allowed")

    try:
        dir_path = Path(path).expanduser()
        if not dir_path.exists():
            return tool_error(code="DIR_NOT_FOUND", message=f"Directory not found: {path}")

        items = []
        for item in dir_path.iterdir():
            item_type = "directory" if item.is_dir() else "file"
            item_info = {
                "name": item.name,
                "type": item_type,
                "path": str(item),
            }
            if item.is_file():
                item_info["size"] = item.stat().st_size
                item_info["ext"] = item.suffix.lower()
            items.append(item_info)

        return tool_result(data={"items": items, "count": len(items), "path": str(dir_path)})
    except Exception as e:
        return tool_error(code="LIST_ERROR", message=f"Error listing directory: {str(e)}")


def move_file(source: str, destination: str, task_id: str = "", on_log: Optional[Callable] = None) -> dict:
    """Move a file from source to destination."""
    if not is_path_allowed(source):
        return tool_error(code="ACCESS_DENIED", message="Access denied: source path is not allowed")
    if not is_path_allowed(destination):
        return tool_error(code="ACCESS_DENIED", message="Access denied: destination path is not allowed")

    try:
        src_path = Path(source).expanduser()
        dst_path = Path(destination).expanduser()

        if not src_path.exists():
            return tool_error(code="FILE_NOT_FOUND", message=f"Source file not found: {source}")

        # Create destination directory if needed
        dst_path.parent.mkdir(parents=True, exist_ok=True)

        import shutil
        shutil.move(str(src_path), str(dst_path))

        return tool_result(data={"source": str(src_path), "destination": str(dst_path)})
    except Exception as e:
        return tool_error(code="MOVE_ERROR", message=f"Error moving file: {str(e)}")


FILE_TOOLS_SCHEMAS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file with optional character limit.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
                "max_chars": {"type": "integer", "description": "Maximum characters to read", "default": 100000},
                "task_id": {"type": "string", "description": "Task identifier for tracking", "default": ""},
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write content to a file, creating directories if needed.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "Content to write"},
                "task_id": {"type": "string", "description": "Task identifier for tracking", "default": ""},
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "patch_file",
        "description": "Replace a string in a file with another string.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to patch"},
                "old_string": {"type": "string", "description": "String to replace"},
                "new_string": {"type": "string", "description": "Replacement string"},
                "task_id": {"type": "string", "description": "Task identifier for tracking", "default": ""},
            },
            "required": ["path", "old_string", "new_string"]
        }
    },
    {
        "name": "search_files",
        "description": "Search for files matching a pattern in a directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "root_dir": {"type": "string", "description": "Root directory to search"},
                "pattern": {"type": "string", "description": "File pattern to match"},
                "content_search": {"type": "boolean", "description": "Search in file contents", "default": False},
                "task_id": {"type": "string", "description": "Task identifier for tracking", "default": ""},
            },
            "required": ["root_dir", "pattern"]
        }
    },
    {
        "name": "list_dir",
        "description": "List files and directories in a given path with file info (name, type, size, extension).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list"},
                "task_id": {"type": "string", "description": "Task identifier for tracking", "default": ""},
            },
            "required": ["path"]
        }
    },
    {
        "name": "move_file",
        "description": "Move a file from source path to destination path. Creates destination directory if needed.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source file path"},
                "destination": {"type": "string", "description": "Destination file path"},
                "task_id": {"type": "string", "description": "Task identifier for tracking", "default": ""},
            },
            "required": ["source", "destination"]
        }
    }
]


for schema in FILE_TOOLS_SCHEMAS:
    handler = globals()[schema["name"]]
    registry.register(
        name=schema["name"],
        handler=handler,
        description=schema["description"],
        parameters=schema["parameters"],
        toolset="file",
        emoji="📁"
    )
