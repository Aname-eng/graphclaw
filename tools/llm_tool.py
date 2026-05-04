"""LLM 调用工具 - 支持 minimax Anthropic 兼容 API."""

import os
import json
import time
from typing import Optional, Dict, Any, List
import requests

from .registry import registry, tool_result, tool_error


class LLMClient:
    """通用 LLM 客户端，支持 MiniMax Anthropic 兼容 API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = 60
    ):
        # 确保 dotenv 已加载
        from dotenv import load_dotenv
        load_dotenv()

        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = (base_url or os.getenv("OPENAI_API_BASE", "https://api.minimaxi.com/anthropic")).rstrip("/")
        self.model = model or os.getenv("LLM_MODEL", "MiniMax-M2.1")
        self.timeout = timeout

    def chat(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """发送聊天请求 (Anthropic 兼容格式)，支持工具调用."""
        if not self.api_key:
            return {"error": "未配置 API Key，请设置 OPENAI_API_KEY 环境变量"}

        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }

        # 转换消息格式
        system_content = system_prompt or ""
        anthropic_messages = []
        for msg in messages:
            role = msg["role"]
            if role == "system":
                system_content = msg["content"] if isinstance(msg["content"], str) else str(msg["content"])
            else:
                content = msg["content"]
                # content 可以是字符串或 list（tool_use/tool_result 块）
                if isinstance(content, str):
                    anthropic_messages.append({"role": role, "content": content})
                else:
                    anthropic_messages.append({"role": role, "content": content})

        payload = {
            "model": self.model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or 4000,
        }
        if system_content:
            payload["system"] = system_content
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            return {"error": f"请求超时（{self.timeout}秒）"}
        except requests.exceptions.RequestException as e:
            return {"error": f"请求失败: {str(e)}"}
        except json.JSONDecodeError:
            return {"error": f"响应解析失败: {response.text[:200]}"}

    def generate_code(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3
    ) -> str:
        """生成代码的便捷方法."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        result = self.chat(messages, temperature=temperature, max_tokens=8000)

        if "error" in result:
            return f"# 生成失败: {result['error']}"

        try:
            # Anthropic 格式: content 数组中找 type=text 的块
            if "content" in result and len(result["content"]) > 0:
                text_content = None
                for block in result["content"]:
                    if block.get("type") == "text":
                        text_content = block["text"]
                        break

                if text_content is None:
                    text_content = result["content"][0].get("text", str(result["content"][0]))

                # 清理 minimax 的 thinking 块（如果存在）
                # minimax 有时会在 text 块中包含 thinking 内容
                if text_content.startswith("{'thinking': ") or text_content.startswith('{"thinking": '):
                    import json
                    try:
                        # 尝试解析 JSON 格式的 thinking 块
                        end_idx = text_content.find("', 'text': ")
                        if end_idx == -1:
                            end_idx = text_content.find('", "text": ')
                        if end_idx > 0:
                            # 提取 text 部分
                            text_start = text_content.find("'text': '")
                            if text_start == -1:
                                text_start = text_content.find('"text": "')
                            if text_start > 0:
                                text_content = text_content[text_start + 9:].rstrip("'}")
                    except Exception:
                        pass

                return text_content

            # 兼容 OpenAI 格式
            return result["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            return f"# 解析响应失败: {str(e)}\n# 原始响应: {json.dumps(result, ensure_ascii=False)[:500]}"


# 全局客户端实例
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取全局 LLM 客户端实例."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def reset_llm_client():
    """重置 LLM 客户端（用于重新加载配置）."""
    global _llm_client
    _llm_client = None


# ===== 工具函数 =====

def llm_chat(messages: List[Dict[str, str]], temperature: float = 0.7, max_tokens: Optional[int] = None) -> Dict[str, Any]:
    """LLM 对话工具."""
    client = get_llm_client()
    result = client.chat(messages, temperature=temperature, max_tokens=max_tokens)
    if "error" in result:
        return tool_error(result["error"])
    return tool_result(result)


def llm_generate_code(prompt: str, system_prompt: Optional[str] = None, temperature: float = 0.3) -> str:
    """LLM 代码生成工具."""
    client = get_llm_client()
    code = client.generate_code(prompt, system_prompt, temperature)
    if code.startswith("# 生成失败") or code.startswith("# 解析响应失败"):
        return tool_error(code)
    return tool_result({"code": code})


def llm_generate_design(task: str, requirements: Optional[str] = None) -> str:
    """使用 LLM 生成架构设计文档."""
    system_prompt = """你是一位资深系统架构师（房玄龄）。
请根据用户任务生成详细的 LangGraph 子系统架构设计文档。
设计文档必须包含：
1. 状态定义（TypedDict）
2. 核心节点列表及职责
3. 路由规则
4. 安全阀设置（最大循环次数、超时时间）
5. 流式输出机制
请用中文回答，格式清晰。"""

    prompt = f"任务: {task}"
    if requirements:
        prompt += f"\n额外要求: {requirements}"

    client = get_llm_client()
    design = client.generate_code(prompt, system_prompt, temperature=0.5)
    return design


def llm_generate_code_from_design(design: str, task: str) -> str:
    """使用 LLM 根据设计文档生成代码."""
    system_prompt = """你是一位资深 Python 工程师（理星）。
请根据提供的架构设计文档，生成完整、可运行的 Python 代码。
要求：
1. 代码必须完整，可直接运行
2. 包含所有必要的 import
3. 包含错误处理
4. 包含 if __name__ == '__main__' 测试入口
5. 代码风格规范，注释清晰
6. 字符串中不要包含未转义的换行符
请直接输出代码，不要输出 markdown 代码块标记（```）。"""

    prompt = f"任务: {task}\n\n架构设计文档:\n{design}\n\n请生成完整可运行的 Python 代码。"

    client = get_llm_client()
    code = client.generate_code(prompt, system_prompt, temperature=0.3)
    return code


def llm_audit_code(code: str, design: str) -> Dict[str, Any]:
    """使用 LLM 审核代码质量."""
    system_prompt = """你是一位严格的代码审核专家（杜如晦）。
请审核提供的代码是否符合设计文档要求。
审核标准：
1. 代码语法是否正确（是否有未闭合的字符串、括号等）
2. 是否实现了设计文档中的所有功能
3. 是否有错误处理机制
4. 代码风格是否规范
5. 是否包含测试入口
请输出 JSON 格式：{"passed": true/false, "issues": ["问题1", "问题2"], "suggestions": ["建议1"]}
只输出 JSON，不要其他内容。"""

    prompt = f"设计文档:\n{design[:2000]}\n\n代码:\n{code[:3000]}\n\n请审核。"

    client = get_llm_client()
    result = client.chat(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=2000
    )

    if "error" in result:
        return {"passed": False, "issues": [f"审核请求失败: {result['error']}"], "suggestions": []}

    try:
        # Anthropic 格式: 找 type=text 的块
        if "content" in result and len(result["content"]) > 0:
            content = None
            for block in result["content"]:
                if block.get("type") == "text":
                    content = block["text"]
                    break
            if content is None:
                content = result["content"][0].get("text", str(result["content"][0]))
        else:
            content = result["choices"][0]["message"]["content"]

        # 尝试提取 JSON
        import re
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            audit_result = json.loads(json_match.group())
            return audit_result
        else:
            return {"passed": False, "issues": ["审核响应格式错误，无法解析 JSON"], "suggestions": [content[:200]]}
    except Exception as e:
        return {"passed": False, "issues": [f"解析审核结果失败: {str(e)}"], "suggestions": []}


# 注册工具
registry.register(
    "llm_chat", llm_chat,
    description="与 LLM 进行对话",
    parameters={
        "messages": {"type": "array", "description": "消息列表 [{role, content}]"},
        "temperature": {"type": "number", "description": "温度参数", "default": 0.7},
        "max_tokens": {"type": "integer", "description": "最大 token 数", "default": None}
    }
)

registry.register(
    "llm_generate_code", llm_generate_code,
    description="使用 LLM 生成代码",
    parameters={
        "prompt": {"type": "string", "description": "代码生成提示"},
        "system_prompt": {"type": "string", "description": "系统提示", "default": None},
        "temperature": {"type": "number", "description": "温度参数", "default": 0.3}
    }
)

registry.register(
    "llm_generate_design", llm_generate_design,
    description="使用 LLM 生成架构设计",
    parameters={
        "task": {"type": "string", "description": "任务描述"},
        "requirements": {"type": "string", "description": "额外要求", "default": None}
    }
)

registry.register(
    "llm_generate_code_from_design", llm_generate_code_from_design,
    description="根据设计文档生成代码",
    parameters={
        "design": {"type": "string", "description": "架构设计文档"},
        "task": {"type": "string", "description": "任务描述"}
    }
)

registry.register(
    "llm_audit_code", llm_audit_code,
    description="使用 LLM 审核代码",
    parameters={
        "code": {"type": "string", "description": "待审核代码"},
        "design": {"type": "string", "description": "设计文档"}
    }
)
