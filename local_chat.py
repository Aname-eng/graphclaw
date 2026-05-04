#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地对话模块 - Hermes 风格 CLI 交互
提供命令行交互界面，支持多轮对话和命令
"""

import os
import sys
import time
import json
from typing import Dict, List, Optional, Callable

from tools.approval_tool import (
    approve_task, reject_task, get_pending_approvals
)

# 颜色定义
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

    @classmethod
    def disable(cls):
        """禁用颜色（Windows兼容）"""
        cls.HEADER = ''
        cls.BLUE = ''
        cls.CYAN = ''
        cls.GREEN = ''
        cls.YELLOW = ''
        cls.RED = ''
        cls.ENDC = ''
        cls.BOLD = ''
        cls.UNDERLINE = ''


# Windows 颜色支持
if sys.platform == 'win32':
    try:
        import colorama
        colorama.init()
    except ImportError:
        Colors.disable()


class LocalChat:
    """本地对话管理器"""

    def __init__(self, on_message: Callable, on_command: Optional[Callable] = None):
        """
        初始化本地对话
        
        Args:
            on_message: 消息处理回调函数 (user_id, text) -> state
            on_command: 命令处理回调函数 (command, args) -> None
        """
        self.on_message = on_message
        self.on_command = on_command or self._default_command_handler
        self.user_id = "local_user"
        self.history: List[Dict] = []
        self.running = False
        self.active_session = {}
        self.pending_approvals: Dict = {}
        self._last_message_result = None

    def _default_command_handler(self, command: str, args: List[str]):
        """默认命令处理器"""
        if command == "/help":
            self._print_help()
        elif command == "/exit":
            self.running = False
            print(f"\n{Colors.GREEN}👋 再见！{Colors.ENDC}")
        elif command == "/clear":
            os.system('cls' if sys.platform == 'win32' else 'clear')
            self._print_banner()
        elif command == "/history":
            self._print_history()
        elif command == "/status":
            self._print_status()
        elif command == "/approve":
            self._handle_approve(args, "once")
        elif command == "/approve-session":
            self._handle_approve(args, "session")
        elif command == "/approve-permanent":
            self._handle_approve(args, "permanent")
        elif command == "/reject":
            self._handle_reject(args)
        elif command == "/pending":
            self._print_pending_approvals()
        else:
            print(f"{Colors.YELLOW}⚠️ 未知命令: {command}，输入 /help 查看帮助{Colors.ENDC}")

    def _print_banner(self):
        """打印欢迎横幅"""
        banner = f"""
{Colors.CYAN}{'='*60}{Colors.ENDC}
{Colors.BOLD}{Colors.HEADER}  🏛️ 元·内阁元系统 - 本地对话模式{Colors.ENDC}
{Colors.CYAN}{'='*60}{Colors.ENDC}

{Colors.GREEN}💡 提示:{Colors.ENDC}
  • 直接输入文字开始对话
  • 输入 /help 查看所有命令
  • 输入 /exit 退出系统

{Colors.CYAN}{'='*60}{Colors.ENDC}
"""
        print(banner)

    def _print_help(self):
        """打印帮助信息"""
        help_text = f"""
{Colors.BOLD}📚 可用命令:{Colors.ENDC}

{Colors.CYAN}系统命令:{Colors.ENDC}
  /help      - 显示此帮助
  /exit      - 退出系统
  /clear     - 清屏
  /history   - 查看对话历史
  /status    - 查看当前会话状态

{Colors.CYAN}任务命令:{Colors.ENDC}
  /interrupt - 中断当前任务
  /rollback  - 时间回溯

{Colors.CYAN}审批命令:{Colors.ENDC}
  /pending             - 查看待审批操作
  /approve <id>        - 仅本次批准操作
  /approve-session <id> - 本次对话内批准
  /approve-permanent <id> - 永久批准
  /reject <id>         - 拒绝操作

{Colors.CYAN}对话技巧:{Colors.ENDC}
  • 描述任务需求，内阁会自动分配专家
  • 可以要求特定专家（如"让文星写文档"）
  • 支持多轮对话优化方案
"""
        print(help_text)

    def _print_history(self):
        """打印对话历史"""
        if not self.history:
            print(f"{Colors.YELLOW}📭 暂无对话历史{Colors.ENDC}")
            return

        print(f"\n{Colors.BOLD}📜 对话历史 ({len(self.history)} 条):{Colors.ENDC}\n")
        for i, entry in enumerate(self.history[-20:], 1):  # 只显示最近20条
            role = entry.get("role", "unknown")
            content = entry.get("content", "")
            timestamp = entry.get("time", "")

            if role == "user":
                print(f"{Colors.BLUE}[{timestamp}] 👤 您:{Colors.ENDC}")
                print(f"  {content}")
            else:
                print(f"{Colors.GREEN}[{timestamp}] 🤖 系统:{Colors.ENDC}")
                # 截断过长的输出
                if len(content) > 500:
                    print(f"  {content[:500]}...")
                else:
                    print(f"  {content}")
            print()

    def _print_status(self):
        """打印当前状态"""
        status = f"""
{Colors.BOLD}📊 当前会话状态:{Colors.ENDC}

{Colors.CYAN}用户信息:{Colors.ENDC}
  用户ID: {self.user_id}
  对话轮数: {len(self.history)}

{Colors.CYAN}会话状态:{Colors.ENDC}
  活跃会话: {'✅ 是' if self.active_session else '❌ 否'}
  当前阶段: {self.active_session.get('phase', 'IDLE')}
"""
        print(status)

    def _print_pending_approvals(self):
        """打印待审批操作"""
        result = get_pending_approvals()
        if not result.get("success"):
            print(f"{Colors.RED}❌ 获取待审批列表失败: {result.get('error', '')}{Colors.ENDC}")
            return
        
        pending = result.get("data", {}).get("approvals", {})
        if not pending:
            print(f"{Colors.CYAN}✅ 当前没有待审批的操作{Colors.ENDC}")
            return
        
        print(f"\n{Colors.BOLD}📋 待审批操作 ({len(pending)}):{Colors.ENDC}\n")
        for approval_id, info in pending.items():
            print(f"{Colors.YELLOW}🔒 [{approval_id}]{Colors.ENDC}")
            print(f"  操作: {info.get('operation', '')}")
            print(f"  风险: {info.get('risk_level', 'medium')}")
            print(f"  详情: {info.get('details', '')[:200]}...")
            print()

    def _handle_approve(self, args: List[str], approval_type: str):
        """处理批准命令"""
        if not args:
            print(f"{Colors.YELLOW}⚠️ 请提供审批ID，格式: /approve <id>{Colors.ENDC}")
            self._print_pending_approvals()
            return
        
        approval_id = args[0]
        reason = " ".join(args[1:]) if len(args) > 1 else "Approved by user"
        
        result = approve_task(approval_id, reason, approval_type)
        if result.get("success"):
            type_desc = result.get("data", {}).get("approval_type_desc", "批准")
            print(f"{Colors.GREEN}✅ {type_desc}成功！{Colors.ENDC}")
            # 如果有保存的最后结果，尝试重新执行
            if self._last_message_result and hasattr(self, "on_message"):
                print(f"\n{Colors.YELLOW}⏳ 重新执行之前的任务...{Colors.ENDC}\n")
                # 这里可以重新执行之前的任务，但需要保存完整的状态
                pass
        else:
            print(f"{Colors.RED}❌ 批准失败: {result.get('error', '')}{Colors.ENDC}")

    def _handle_reject(self, args: List[str]):
        """处理拒绝命令"""
        if not args:
            print(f"{Colors.YELLOW}⚠️ 请提供审批ID，格式: /reject <id>{Colors.ENDC}")
            self._print_pending_approvals()
            return
        
        approval_id = args[0]
        reason = " ".join(args[1:]) if len(args) > 1 else "Rejected by user"
        
        result = reject_task(approval_id, reason)
        if result.get("success"):
            print(f"{Colors.GREEN}✅ 拒绝成功！{Colors.ENDC}")
        else:
            print(f"{Colors.RED}❌ 拒绝失败: {result.get('error', '')}{Colors.ENDC}")

    def _format_message(self, message: str, max_width: int = 80) -> str:
        """格式化消息，自动换行"""
        lines = []
        current_line = ""

        for word in message.split():
            if len(current_line) + len(word) + 1 <= max_width:
                current_line += " " + word if current_line else word
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word

        if current_line:
            lines.append(current_line)

        return "\n".join(lines)

    def _print_stream_message(self, message: str):
        """打印流式消息"""
        formatted = self._format_message(message)
        print(f"{Colors.GREEN}  🤖 {formatted}{Colors.ENDC}")

    def run(self):
        """运行本地对话循环"""
        self.running = True
        self._print_banner()

        while self.running:
            try:
                # 显示输入提示
                prompt = f"\n{Colors.CYAN}👤 您:{Colors.ENDC} "
                user_input = input(prompt).strip()

                if not user_input:
                    continue

                # 记录时间
                timestamp = time.strftime("%H:%M:%S")

                # 处理命令
                if user_input.startswith("/"):
                    parts = user_input.split()
                    command = parts[0]
                    args = parts[1:]

                    self.history.append({
                        "role": "user",
                        "content": user_input,
                        "time": timestamp
                    })

                    self.on_command(command, args)
                    continue

                # 处理普通消息
                self.history.append({
                    "role": "user",
                    "content": user_input,
                    "time": timestamp
                })

                print(f"\n{Colors.YELLOW}⏳ 内阁会议进行中...{Colors.ENDC}\n")

                # 调用消息处理器
                result = self.on_message(self.user_id, user_input)
                self._last_message_result = result

                # 显示结果
                if result and isinstance(result, dict):
                    phase = result.get("current_phase", "UNKNOWN")
                    print(f"\n{Colors.CYAN}📋 当前阶段: {phase}{Colors.ENDC}")

                    # 检查是否需要审批
                    if result.get("needs_approval"):
                        approval_id = result.get("approval_id")
                        options = result.get("approval_options", [])
                        print(f"\n{Colors.RED}🔒 需要用户审批操作！{Colors.ENDC}")
                        print(f"{Colors.YELLOW}审批ID: {approval_id}{Colors.ENDC}")
                        print(f"\n{Colors.CYAN}请选择一个选项:{Colors.ENDC}")
                        print(f"  1. /approve {approval_id} - 仅本次批准")
                        print(f"  2. /approve-session {approval_id} - 本次对话内批准")
                        print(f"  3. /approve-permanent {approval_id} - 永久批准")
                        print(f"  4. /reject {approval_id} - 拒绝执行")

                    # 显示流式消息
                    if result.get("stream_buffer"):
                        print(f"\n{Colors.BOLD}📤 处理过程:{Colors.ENDC}")
                        for msg in result.get("stream_buffer", []):
                            print(f"  {msg}")

                    # 显示生成的文件
                    if result.get("subsystem_code"):
                        print(f"\n{Colors.GREEN}✅ 代码已生成{Colors.ENDC}")
                    if result.get("tech_doc"):
                        print(f"{Colors.GREEN}✅ 文档已生成{Colors.ENDC}")

                # 记录响应
                self.history.append({
                    "role": "assistant",
                    "content": f"处理完成，阶段: {result.get('current_phase', 'UNKNOWN') if result else 'ERROR'}",
                    "time": time.strftime("%H:%M:%S")
                })

            except KeyboardInterrupt:
                print(f"\n\n{Colors.YELLOW}⚠️ 收到中断信号{Colors.ENDC}")
                self.running = False
            except Exception as e:
                print(f"\n{Colors.RED}❌ 错误: {e}{Colors.ENDC}")
                import traceback
                traceback.print_exc()

        print(f"\n{Colors.GREEN}👋 感谢使用元·内阁元系统！{Colors.ENDC}\n")


def create_local_chat(on_message: Callable, on_command: Optional[Callable] = None) -> LocalChat:
    """
    创建本地对话实例
    
    Args:
        on_message: 消息处理回调
        on_command: 命令处理回调（可选）
    
    Returns:
        LocalChat 实例
    """
    return LocalChat(on_message, on_command)


if __name__ == "__main__":
    # 测试本地对话
    def test_message_handler(user_id: str, text: str):
        print(f"收到消息: {text}")
        return {"current_phase": "TESTING"}

    chat = create_local_chat(test_message_handler)
    chat.run()
