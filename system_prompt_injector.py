"""
动态 System Prompt 注入器

在每次对话开始前，注入以下信息给所有智能体：
1. 动态时间
2. 当前平台 (Windows/Linux/macOS)
3. 运行位置 (飞书/命令行/Web UI)
4. 平台能力 (内置命令)
5. 工具池
6. 人格设置
"""

import platform
import os
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime


@dataclass
class PlatformContext:
    """平台上下文信息"""
    platform: str
    platform_version: str
    running_mode: str
    available_commands: List[str]
    shell_type: str
    home_dir: str
    temp_dir: str


@dataclass
class AgentContext:
    """智能体上下文"""
    user_id: str
    user_name: Optional[str] = None
    personality: str = ""
    capabilities: List[str] = field(default_factory=list)
    tools: List[dict] = field(default_factory=list)
    current_task: Optional[str] = None
    active_graph_id: str = "default"


class SystemPromptInjector:
    """动态 System Prompt 注入器"""

    def __init__(
        self,
        platform_context: PlatformContext,
        agent_context: AgentContext,
    ):
        self.platform = platform_context
        self.agent = agent_context

    def generate_system_prompt(self) -> str:
        """生成完整的 System Prompt"""
        sections = []
        sections.append(self._generate_datetime_section())
        sections.append(self._generate_platform_section())
        sections.append(self._generate_running_mode_section())
        sections.append(self._generate_capabilities_section())
        sections.append(self._generate_tools_section())
        sections.append(self._generate_personality_section())
        sections.append(self._generate_task_context_section())
        return "\n\n".join([s for s in sections if s])

    def _generate_datetime_section(self) -> str:
        now = datetime.now()
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        return f"""## 🕐 当前时间
{datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')} ({weekday})"""

    def _generate_platform_section(self) -> str:
        return f"""## 💻 运行平台
- 操作系统: {self.platform.platform}
- 系统版本: {self.platform.platform_version}
- Shell类型: {self.platform.shell_type}
- 主目录: {self.platform.home_dir}
- 临时目录: {self.platform.temp_dir}"""

    def _generate_running_mode_section(self) -> str:
        return f"""## 📍 运行位置
当前通过 [{self.platform.running_mode}] 与我对话"""

    def _generate_capabilities_section(self) -> str:
        if self.platform.shell_type == "powershell":
            commands = "Get-ChildItem, Get-Content, Set-Content, Copy-Item, Move-Item, Remove-Item, Get-Process, Stop-Process, Start-Process, Invoke-WebRequest, Expand-Archive, Compress-Archive 等"
        elif self.platform.shell_type == "bash":
            commands = "ls, cat, cp, mv, rm, grep, find, curl, wget, tar, gzip, ps, kill 等"
        else:
            commands = "dir, copy, move, del, type, findstr, tasklist 等"

        return f"""## 🔧 平台可用命令
{commands}

你可以在脚本中调用这些命令来完成文件操作、系统管理等任务。"""

    def _generate_tools_section(self) -> str:
        if not self.agent.tools:
            return "## 🛠️ 可用工具\n暂无工具"

        tool_lines = []
        for tool in self.agent.tools:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "")[:100]
            tool_lines.append(f"- **{name}**: {desc}")

        return f"""## 🛠️ 可用工具池
{chr(10).join(tool_lines)}

你可以使用这些工具来完成复杂任务。"""

    def _generate_personality_section(self) -> str:
        if not self.agent.personality:
            return ""
        return f"""## 🎭 人格设置
{self.agent.personality}"""

    def _generate_task_context_section(self) -> str:
        lines = ["## 📋 当前状态"]
        lines.append(f"- 活跃流程图: {self.agent.active_graph_id}")
        if self.agent.current_task:
            lines.append(f"- 当前任务: {self.agent.current_task[:100]}")
        if self.agent.user_name:
            lines.append(f"- 用户: {self.agent.user_name}")
        return "\n".join(lines)

    def update_context(self, **kwargs):
        """更新上下文信息"""
        if "current_task" in kwargs:
            self.agent.current_task = kwargs["current_task"]
        if "active_graph_id" in kwargs:
            self.agent.active_graph_id = kwargs["active_graph_id"]
        if "tools" in kwargs:
            self.agent.tools = kwargs["tools"]
        if "personality" in kwargs:
            self.agent.personality = kwargs["personality"]


def create_platform_context(
    platform_name: str = None,
    running_mode: str = "命令行",
) -> PlatformContext:
    """自动检测并创建平台上下文"""
    if platform_name is None:
        system = platform.system()
        if system == "Windows":
            platform_name = "Windows"
        elif system == "Linux":
            platform_name = "Linux"
        else:
            platform_name = "macOS"

    if platform_name == "Windows":
        shell = os.getenv("PSModulePath", "").startswith("C:\\Program Files\\PowerShell")
        shell_type = "powershell" if shell else "cmd"
        version = platform.version()
    elif platform_name == "Linux":
        shell_type = os.getenv("SHELL", "/bin/bash").split("/")[-1]
        version = platform.platform()
    else:
        shell_type = "zsh"
        version = platform.platform()

    return PlatformContext(
        platform=platform_name,
        platform_version=version,
        running_mode=running_mode,
        available_commands=[],
        shell_type=shell_type,
        home_dir=os.path.expanduser("~"),
        temp_dir=tempfile.gettempdir(),
    )


def create_agent_context(
    user_id: str,
    tools: List[dict] = None,
    personality: str = "",
    active_graph_id: str = "default",
) -> AgentContext:
    """创建智能体上下文"""
    if tools is None:
        tools = []

    capabilities = [f"工具能力: {tool.get('name', 'unknown')}" for tool in tools]

    return AgentContext(
        user_id=user_id,
        user_name=None,
        personality=personality,
        capabilities=capabilities,
        tools=tools,
        current_task=None,
        active_graph_id=active_graph_id,
    )


def get_default_personality() -> str:
    """获取默认人格设置"""
    return """你是一个乐于助人的AI助手，代号"元内阁"。

核心原则：
1. **主动思考**：不等待用户明确指示，主动分析需求并提出建议
2. **透明沟通**：清晰解释你的推理过程和计划
3. **务实执行**：选择最直接有效的方案，不过度设计
4. **持续学习**：根据用户反馈不断改进

沟通风格：
- 简洁明了，避免冗余
- 技术问题用通俗语言解释
- 复杂任务分步骤说明
- 主动确认关键决策点

能力边界：
- 可以调用各种工具完成任务
- 可以编写和执行代码
- 可以搜索和分析信息
- 可以管理文件和系统

遇到问题时的处理：
1. 先尝试自己解决
2. 无法解决时，明确告知用户原因
3. 提供替代方案或建议"""


if __name__ == "__main__":
    platform_ctx = create_platform_context(running_mode="命令行")
    agent_ctx = create_agent_context(
        user_id="test_user",
        tools=[
            {"name": "run_shell_command", "description": "运行 shell 命令"},
            {"name": "read_file", "description": "读取文件内容"},
            {"name": "write_file", "description": "写入文件内容"},
        ],
        personality=get_default_personality(),
    )

    injector = SystemPromptInjector(platform_ctx, agent_ctx)
    print(injector.generate_system_prompt())
