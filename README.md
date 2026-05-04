# GraphClaw 🏛️

多智能体工作流引擎 — 融合团队协作与单智能体自主执行。

## 架构

```
Chat 模式 (默认)          Task 模式 (/task)
单智能体 + 63 个工具       LangGraph 多智能体团队
类 Hermes 强度            房玄龄ABC + 杜如晦 + 执行者
多轮工具调用              人机交互审核节点
文件/代码/搜索/浏览器      会话记忆 / Cron 调度
```

## 快速开始

```bash
# 克隆后
cd meta_cabinet
python -m venv meta_venv
meta_venv\Scripts\pip install -r requirements.txt

# 配置 API Key
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY

# 启动 CLI（默认聊天模式）
meta_venv\Scripts\python start.py cli

# 图编辑器
meta_venv\Scripts\python start.py editor

# 飞书机器人（WebSocket 长连接）
meta_venv\Scripts\python start.py feishu
```

## 模式

| 命令 | 作用 |
|---|---|
| `/chat` | 聊天模式（单智能体，63 工具） |
| `/task` | 任务模式（团队协作工作流） |
| `task_exit` | 退出当前任务 |
| `/shutdown` | 保存状态并退出 |
| `/cron list/add/rm` | 管理定时任务 |

## 安全机制

- 危险命令拦截（shutdown/del/format 等需确认）
- 搜索频率限制（30~40s 间隔防封禁）
- 浏览器人机验证自动突破（curl_cffi → requests → Playwright → MCP 四级降级）

## 飞书集成

- WebSocket 长连接（无需公网 IP）
- 自动 LLM 回复 + 工具调用
- 文件收发
- Typing 反应

## 致谢

本项目的设计和实现大量参考了以下优秀开源项目：

- **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** — 工具系统架构、Cron 调度、飞书网关、审批机制
- **[LangGraph](https://github.com/langchain-ai/langgraph)** — 状态图工作流引擎
- **[OpenClaw](https://github.com/NousResearch/openclaw)** — 多智能体协作模式、浏览器自动化
- **[LangGraph Studio](https://github.com/langchain-ai/langgraph-studio)** — 可视化图编辑器设计参考
- **[browser-use](https://github.com/browser-use/browser-use)** — AI 浏览器自动化
- **[Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp)** — 浏览器降级方案

## License

MIT
