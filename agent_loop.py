"""单智能体循环 — 类 Hermes 强度，支持多轮工具调用."""
import json
import time
import logging
from typing import List, Dict, Optional, Any

logger = logging.getLogger("agent_loop")


_tools_inited = False


def _ensure_tools():
    global _tools_inited
    if not _tools_inited:
        from tools import init_tools
        init_tools()
        _tools_inited = True


def get_tool_schemas() -> list:
    """从工具注册表获取所有工具的 Anthropic 格式 schema."""
    _ensure_tools()
    from tools import registry
    tool_names = registry.get_all_tool_names()
    schemas = []
    for name in tool_names:
        schema = registry.get_schema(name)
        if not schema:
            continue
        # 参数的 JSON Schema 格式可以直接复用
        params = schema.parameters
        input_schema = params if isinstance(params, dict) and "properties" in params else {
            "type": "object",
            "properties": {},
            "required": [],
        }
        schemas.append({
            "name": name,
            "description": schema.description,
            "input_schema": input_schema,
        })
    return schemas


def execute_tool(name: str, args: dict) -> str:
    """执行工具并返回结果字符串（含安全拦截)."""
    from tools import registry
    try:
        # 安全检查：拦截危险 shell 命令
        if name == "run_shell_command":
            command = args.get("command", "")
            from safety import is_dangerous_command, execute_with_approval
            is_dangerous, reason = is_dangerous_command(command)
            if is_dangerous:
                print(f"  🔒 拦截高危命令: {command[:80]}...")
                print(f"  🔒 原因: {reason}")
                # 在 CLI 中直接请求确认
                print(f"  ⚠️ 高危操作: {reason}")
                print(f"  命令: {command}")
                confirm = input("  确认执行? (y/N): ").strip().lower()
                if confirm not in ("y", "yes"):
                    return f"[已拒绝] 高危命令未获批准: {command[:80]}"

        handler = registry.get_handler(name)
        if not handler:
            return f"[工具 {name} 未注册]"
        result = handler(args)
        if isinstance(result, dict):
            if result.get("success") is False:
                return f"[工具 {name}] {result.get('error', '未知错误')}"
            return json.dumps(result.get("data", result), ensure_ascii=False, indent=2)
        return str(result)
    except Exception as e:
        return f"[工具 {name} 异常] {e}"


def _find_tool_use(content_blocks: list) -> Optional[tuple]:
    """在 Anthropic 响应中查找第一个 tool_use 块. 返回 (index, name, input, tool_use_id)."""
    for i, block in enumerate(content_blocks):
        if block.get("type") == "tool_use":
            return i, block.get("name", ""), block.get("input", {}), block.get("id", "")
    return None


def _get_text_content(content_blocks: list) -> str:
    """提取 Anthropic 响应中的文本内容."""
    texts = []
    for block in content_blocks:
        if block.get("type") == "text":
            texts.append(block.get("text", ""))
    return "\n".join(texts)


def run_agent(user_message: str,
              system_prompt: Optional[str] = None,
              max_turns: int = 30,
              personality: str = "") -> str:
    """完整的单智能体循环 — 支持多轮工具调用.

    Args:
        user_message: 用户输入
        system_prompt: 系统提示词
        max_turns: 最大工具调用轮数
        personality: 角色人格设定

    Returns:
        最终回复文本
    """
    from meta_system import get_llm_client, load_character_personality

    client = get_llm_client()
    personality_text = personality or load_character_personality("executor")

    if not system_prompt:
        system_prompt = (
            f"{personality_text}\n\n"
            "你是一个全能的 AI 助手，运行在用户的 Windows 电脑上。\n"
            "你可以直接访问用户的整个文件系统（C:、D: 等盘符）。\n\n"
            "【工具使用指南】\n"
            "- 查看目录内容 → 用 `list_dir`\n"
            "- 读取文件 → 用 `read_file`\n"
            "- 搜索文件 → 用 `search_files`\n"
            "- 执行系统命令 → 用 `run_shell_command`\n"
            "- 写入文件 → 用 `write_file`\n"
            "- ❌ 不要用 `execute_code` 来做文件操作（它在沙箱里运行，无权访问用户文件）\n"
            "- `execute_code` 只用于纯数据计算/算法任务\n"
            "- 打开网页/搜索 → 用 `browser_navigate`（自动使用 browser-use + MCP 降级）\n\n"
            "你可以直接访问 C:\\、D:\\ 等路径。用户就在本机，不是远程服务器。\n\n"
            "【飞书命令】\n"
            "用户可以通过以下命令控制系统。如果用户说 /task xxx，直接告诉他们这不是聊天功能，\n"
            "让他们直接在飞书输入 /task 即可。\n"
            "- /help → 显示帮助\n"
            "- /shutdown → 停止机器人（不是你电脑！）\n"
            "- /task <内容> → 用团队工作流处理任务\n"
            "- /cron list / add / rm → 管理定时任务\n\n"
            "【安全机制】\n"
            "系统内置了危险命令拦截。以下命令会被自动拦截并要求用户确认：\n"
            "- 关机/重启: shutdown, restart, stop-computer\n"
            "- 删除: del, format, rmdir, rm -rf\n"
            "- 系统修改: reg delete, bcdedit\n"
            "- 高危 PowerShell: remove-item, rm, disable-*\n"
            "如果用户说 /shutdown，那不是让你关电脑！那是系统退出命令。\n"
            "如果你需要执行高危操作，先向用户说明原因，等待用户确认。\n\n"
            "每次工具调用后，根据结果决定下一步行动。\n"
            "最终用中文给出完整的回复。"
        )

    messages = [{"role": "user", "content": user_message}]
    tool_schemas = get_tool_schemas()

    for turn in range(max_turns):
        result = client.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.5,
            max_tokens=4000,
            tools=tool_schemas if tool_schemas else None,
        )

        if "error" in result:
            return f"[错误] {result['error']}"

        content_blocks = result.get("content", [])
        if not content_blocks:
            continue

        # 提取文本
        text = _get_text_content(content_blocks)
        if text:
            print(f"  🤖 {text[:100]}...")

        # 检查是否有工具调用
        tool_use = _find_tool_use(content_blocks)
        if not tool_use:
            # 没有工具调用 — 这就是最终回复
            return text if text else "任务完成"

        # 执行工具
        idx, tool_name, tool_args, tool_id = tool_use
        print(f"  🔧 调用工具: {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:100]})")

        tool_result_str = execute_tool(tool_name, tool_args)

        # 构建回复消息（含 tool_use 块和 tool_result 块）
        assistant_content = []
        if text:
            assistant_content.append({"type": "text", "text": text})
        assistant_content.append({
            "type": "tool_use",
            "id": tool_id,
            "name": tool_name,
            "input": tool_args,
        })

        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": tool_result_str[:5000],
            }]
        })

    return "已达最大工具调用轮数，任务可能未完成。"


def run_chat_session():
    """交互式聊天会话 — 类似 Hermes CLI."""
    print("=" * 40)
    print("💬 聊天模式 (类 Hermes 单智能体)")
    print("   支持文件搜索、代码执行、命令运行等工具调用")
    print("   输入 /task 进入任务模式, /shutdown 退出")
    print("=" * 40)

    from meta_system import load_character_personality
    personality = load_character_personality("executor")

    conversation_history = []

    while True:
        try:
            user_input = input("💬 > ").strip()
            if not user_input:
                continue
            if user_input == "/task":
                return "task"
            if user_input == "/shutdown":
                return "shutdown"
            if user_input.startswith("/"):
                print(f"  未知命令: {user_input}")
                continue

            print(f"  🧠 思考中...")
            t0 = time.time()

            # 构建上下文（含历史）
            context = ""
            if conversation_history:
                context = "【历史对话摘要】\n" + "\n".join(
                    f"用户: {h['user'][:200]}\n助手: {h['assistant'][:200]}"
                    for h in conversation_history[-3:]  # 最近3轮
                ) + "\n\n"

            reply = run_agent(
                user_message=f"{context}用户当前消息: {user_input}",
                personality=personality,
            )

            elapsed = time.time() - t0
            print(f"  ✅ ({elapsed:.1f}s)")
            print(f"  {reply}")
            print()

            conversation_history.append({
                "user": user_input,
                "assistant": reply[:500],
            })

        except KeyboardInterrupt:
            print()
            return "shutdown"
        except EOFError:
            print()
            return "shutdown"
        except Exception as e:
            print(f"  ⚠️ 错误: {e}")
            import traceback
            traceback.print_exc()
