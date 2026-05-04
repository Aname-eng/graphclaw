"""Tools package - Hermès-style tool system for LangGraph meta-cabinet."""

from .registry import registry, tool_result, tool_error

def init_tools(on_log=None):
    """Initialize all tools and register them to the global registry.

    调用此函数会注册所有内置工具到 registry。
    """
    from . import file_tools
    from . import terminal_tool
    from . import memory_tool
    from . import cron_tools
    from . import interrupt_tool
    from . import code_executor
    from . import execute_tool
    from . import session_search
    from . import fuzzy_match
    from . import budget_config
    
    session_search.register_all()
    fuzzy_match.register_all()
    budget_config.register_all()
    
    if on_log:
        on_log("✅ 会话搜索工具已加载")
        on_log("✅ 模糊匹配工具已加载")
        on_log("✅ 预算控制工具已加载")

    try:
        from . import voice_tools
        if on_log:
            on_log("✅ 语音工具已加载")
    except ImportError as e:
        if on_log:
            on_log(f"⚠️ 语音工具未加载: {e}")

    try:
        from .environments import docker
        if on_log:
            on_log("✅ Docker 执行环境工具已加载")
    except ImportError as e:
        if on_log:
            on_log(f"⚠️ Docker 执行环境工具未加载: {e}")
    
    try:
        from . import feishu_tools
        if on_log:
            on_log("✅ 飞书工具已加载")
    except ImportError as e:
        if on_log:
            on_log(f"⚠️ 飞书工具未加载: {e}")

    try:
        from . import web_tools
        if on_log:
            on_log("✅ 浏览器工具已加载 (playwright)")
    except ImportError as e:
        if on_log:
            on_log(f"⚠️ 浏览器工具未加载: {e}")

    try:
        from . import browser_use_tool
        if on_log:
            on_log("✅ AI 浏览器工具已加载 (browser-use + MCP DevTools)")
    except ImportError as e:
        if on_log:
            on_log(f"⚠️ AI 浏览器工具未加载: {e}")

    try:
        from . import browser_advanced
        if on_log:
            on_log("✅ 浏览器高级工具已加载")
    except ImportError as e:
        if on_log:
            on_log(f"⚠️ 浏览器高级工具未加载: {e}")

    try:
        from . import desktop_tools
        if on_log:
            on_log("✅ 桌面操控工具已加载")
    except ImportError as e:
        if on_log:
            on_log(f"⚠️ 桌面操控工具未加载: {e}")

    try:
        from . import cli_tools
        cli_manager = cli_tools.CLIToolManager()
        cli_manager.discover_and_register(on_log)
        if on_log:
            on_log("✅ CLI 工具已加载")
    except ImportError as e:
        if on_log:
            on_log(f"⚠️ CLI 工具未加载: {e}")
    
    try:
        from . import image_gen_tool
        if on_log:
            on_log("✅ 图像生成工具已加载")
    except ImportError as e:
        if on_log:
            on_log(f"⚠️ 图像生成工具未加载: {e}")

    try:
        from . import checkpoint_manager
        if on_log:
            on_log("✅ 检查点管理工具已加载")
    except ImportError as e:
        if on_log:
            on_log(f"⚠️ 检查点管理工具未加载: {e}")

    try:
        from . import llm_tool
        if on_log:
            on_log("✅ LLM 工具已加载 (minimax)")
    except ImportError as e:
        if on_log:
            on_log(f"⚠️ LLM 工具未加载: {e}")

    if on_log:
        tool_count = len(registry.get_all_tool_names())
        on_log(f"📦 共注册 {tool_count} 个工具")

__all__ = ["registry", "tool_result", "tool_error", "init_tools"]
