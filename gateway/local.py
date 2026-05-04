#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地网关 - 命令行交互界面
提供 Hermes 风格的本地对话体验
"""

import os
import sys
import time
from typing import Dict, List, Optional, Callable


class Colors:
    """终端颜色"""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

    @classmethod
    def disable(cls):
        cls.HEADER = ''
        cls.BLUE = ''
        cls.CYAN = ''
        cls.GREEN = ''
        cls.YELLOW = ''
        cls.RED = ''
        cls.ENDC = ''
        cls.BOLD = ''


# Windows 颜色支持
if sys.platform == 'win32':
    try:
        import colorama
        colorama.init()
    except ImportError:
        Colors.disable()


class LocalGateway:
    """本地网关 - 命令行交互"""

    def __init__(self, on_message: Callable, on_command: Optional[Callable] = None):
        """
        初始化本地网关
        
        Args:
            on_message: 消息处理回调 (user_id, text) -> state
            on_command: 命令处理回调 (command, args) -> None
        """
        self.on_message = on_message
        self.on_command = on_command
        self.user_id = "local_user"
        self.history: List[Dict] = []
        self.running = False
        self.active_session = {}

    def send_message(self, user_id: str, content: str) -> bool:
        """模拟发送消息（实际直接打印到控制台）"""
        print(f"\n{Colors.GREEN}🤖 系统:{Colors.ENDC}")
        # 格式化长消息
        lines = content.split('\n')
        for line in lines:
            if len(line) > 80:
                # 自动换行
                while len(line) > 80:
                    print(f"  {line[:80]}")
                    line = line[80:]
                if line:
                    print(f"  {line}")
            else:
                print(f"  {line}")
        return True

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
        """打印帮助"""
        help_text = f"""
{Colors.BOLD}📚 可用命令:{Colors.ENDC}

{Colors.CYAN}系统命令:{Colors.ENDC}
  /help      - 显示此帮助
  /exit      - 退出系统
  /clear     - 清屏
  /history   - 查看对话历史
  /status    - 查看当前会话状态

{Colors.CYAN}任务命令:{Colors.ENDC}
  /tools     - 列出所有可用工具
  /interrupt - 中断当前任务
  /approve   - 批准待审批操作
  /reject    - 拒绝待审批操作
  /rollback  - 时间回溯

{Colors.CYAN}对话技巧:{Colors.ENDC}
  • 描述任务需求，内阁会自动分配专家
  • 可以要求特定专家（如"让文星写文档"）
  • 支持多轮对话优化方案
"""
        print(help_text)

    def _print_history(self):
        """打印历史"""
        if not self.history:
            print(f"{Colors.YELLOW}📭 暂无对话历史{Colors.ENDC}")
            return

        print(f"\n{Colors.BOLD}📜 对话历史 ({len(self.history)} 条):{Colors.ENDC}\n")
        for entry in self.history[-20:]:
            role = entry.get("role", "unknown")
            content = entry.get("content", "")
            timestamp = entry.get("time", "")

            if role == "user":
                print(f"{Colors.BLUE}[{timestamp}] 👤 您:{Colors.ENDC}")
                print(f"  {content[:200]}{'...' if len(content) > 200 else ''}")
            else:
                print(f"{Colors.GREEN}[{timestamp}] 🤖 系统:{Colors.ENDC}")
                print(f"  {content[:200]}{'...' if len(content) > 200 else ''}")
            print()

    def _print_status(self):
        """打印状态"""
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

    def _handle_command(self, command: str, args: List[str]):
        """处理命令"""
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
        elif self.on_command:
            self.on_command(command, args)
        else:
            print(f"{Colors.YELLOW}⚠️ 未知命令: {command}，输入 /help 查看帮助{Colors.ENDC}")

    def run(self):
        """运行本地对话循环"""
        self.running = True
        self._print_banner()

        while self.running:
            try:
                # 输入提示
                prompt = f"\n{Colors.CYAN}👤 您:{Colors.ENDC} "
                user_input = input(prompt).strip()

                if not user_input:
                    continue

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

                    self._handle_command(command, args)
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

                # 显示结果摘要
                if result and isinstance(result, dict):
                    phase = result.get("current_phase", "UNKNOWN")
                    print(f"\n{Colors.CYAN}📋 当前阶段: {phase}{Colors.ENDC}")

                    if result.get("subsystem_code"):
                        print(f"{Colors.GREEN}✅ 代码已生成{Colors.ENDC}")
                    if result.get("tech_doc"):
                        print(f"{Colors.GREEN}✅ 文档已生成{Colors.ENDC}")

                # 记录响应
                self.history.append({
                    "role": "assistant",
                    "content": f"处理完成，阶段: {result.get('current_phase', 'UNKNOWN')}",
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


def create_local_gateway(on_message: Callable, on_command: Optional[Callable] = None) -> LocalGateway:
    """创建本地网关实例"""
    return LocalGateway(on_message, on_command)
