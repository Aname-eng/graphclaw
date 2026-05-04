# GraphClaw 🏛️

> 多智能体工作流引擎 — 让 AI 团队协作完成复杂任务

GraphClaw 是一个基于 **LangGraph** 的多智能体工作流平台，融合了**单智能体自主执行**（Chat 模式）与**多智能体团队协作**（Task 模式）。灵感来自 Hermes Agent、OpenClaw 和 LangGraph Studio。

---

## ✨ 核心特性

### 🤖 双模式运行

| 模式 | 说明 | 适用场景 |
|---|---|---|
| **💬 Chat 模式**（默认） | 单智能体 + 63 个内置工具，类 Hermes 强度 | 日常问答、文件操作、代码编写、网页搜索 |
| **📋 Task 模式**（`/task`） | LangGraph 多智能体团队：房玄龄ABC + 杜如晦 + 执行者 | 复杂任务需要方案设计→评审→执行→审核 |

### 🧠 Chat 模式 — 63 个工具随意调用

- 📁 文件操作（读/写/搜索/移动/补丁）
- 🔧 代码执行与 Shell 命令
- 🌐 浏览器导航与网页内容提取（自动突破人机验证）
- 🖥️ 桌面操控（鼠标/键盘/截图）
- 💾 记忆系统（键值存储 + FTS5 全文检索）
- 📅 定时任务（Cron 调度）
- 🎤 语音合成与识别
- 🐳 Docker 容器执行
- 🖼️ AI 图像生成
- 🔒 审批与安全机制

### 📋 Task 模式 — 团队协作工作流

```
用户输入 → 任务解析 → 能力搜索
                        ↓
              ┌─ 简单 → 直接执行
              └─ 复杂 → 房玄龄A(提案) → 房玄龄B(反对)
                          ↓
                         房玄龄C(总结) → 杜如晦(审核)
                                          ↓
                                  ┌─ 通过 → 😊 人工审核节点(30s超时)
                                  └─ 驳回 → 循环优化
```

- **人机交互审核节点**：设计方案和产物都经人工确认，30 秒无操作自动通过
- **会话记忆**：同一次会话内的任务历史自动累积
- **持续对话**：任务完成后可继续修改，输入 `task_exit` 退出

### 🎨 图编辑器（Web UI）

已实现基于 **Mermaid.js** 的可视化图编辑器：

```
python start.py editor
```

- 节点和连线以流程图形式实时预览
- 支持添加/删除节点、连线、条件分支
- 节点类型：system（系统节点）、character（角色节点，含 personality）
- 自动生成 LangGraph Python 代码
- 保存/加载图配置

> **后续规划**：支持完整拖拽交互、节点属性面板、实时图执行调试

### 📱 飞书机器人集成

- **WebSocket 长连接**（无需公网 IP，无需内网穿透）
- 收到消息立即发送 `Typing` 反应
- 自动 LLM 回复（支持工具调用）
- 文件自动接收与发送
- 支持命令：`/help` `/shutdown` `/task <内容>` `/cron list/add/rm`

### ⏰ Cron 定时任务

- 支持间隔调度（`30m`、`every 2h`）和 Cron 表达式（`0 9 * * *`）
- 到期自动调用 LLM 执行，结果保存到本地
- 管理命令：`/cron list` `/cron add <间隔> <提示词>` `/cron rm <id>`

### 🔒 安全机制

- **危险命令拦截**：`shutdown`、`del`、`format`、`rm -rf` 等需用户确认
- **搜索频率限制**：每次搜索间隔 `30 + random(0,10)` 秒
- **浏览器验证突破**：四级降级策略 — curl_cffi → requests → Playwright → MCP

---

## 🚀 快速开始

```bash
# 1. 克隆
git clone https://github.com/Aname-eng/graphclaw.git
cd graphclaw

# 2. 创建虚拟环境
python -m venv meta_venv
meta_venv\Scripts\pip install -r requirements.txt

# 3. 安装浏览器依赖（可选）
meta_venv\Scripts\playwright install chromium

# 4. 配置 API Key
cp .env.example .env
# 编辑 .env 填入你的 OPENAI_API_KEY

# 5. 启动
meta_venv\Scripts\python start.py cli      # CLI 聊天模式
meta_venv\Scripts\python start.py editor    # 图编辑器 Web UI
meta_venv\Scripts\python start.py feishu   # 飞书机器人
```

---

## 📖 命令参考

| 命令 | 模式 | 作用 |
|---|---|---|
| `/chat` | 全局 | 切换到聊天模式 |
| `/task` | 全局 | 切换到任务模式 |
| `task_exit` | 任务中 | 退出当前任务 |
| `/shutdown` | 全局 | 保存状态并退出 |
| `/cron list` | 全局 | 查看定时任务 |
| `/cron add <间隔> <提示词>` | 全局 | 添加定时任务 |
| `/cron rm <id>` | 全局 | 删除定时任务 |
| `/help` | 飞书 | 显示飞书命令帮助 |

---

## 🗺️ 路线图

### ✅ 已实现

- [x] Chat 模式（单智能体 + 63 工具）
- [x] Task 模式（LangGraph 多智能体团队）
- [x] 可视化图编辑器（Mermaid.js 渲染）
- [x] 图配置 → Python 代码自动生成
- [x] 飞书 WebSocket 长连接机器人
- [x] Cron 定时任务调度器
- [x] 会话记忆跨任务持久化
- [x] 人机交互审核节点（30s 超时自动通过）
- [x] 危险命令安全拦截
- [x] 浏览器人机验证自动突破
- [x] 飞书文件收发

### 🔜 开发中

- [ ] **拖拽式图编辑器 Web UI** — 基于 React Flow / 类似 LangGraph Studio 的完整拖拽编辑体验，当前已实现基础的节点/连线管理 + 代码生成
- [ ] **更多消息渠道支持**
  - [ ] 微信公众号
  - [ ] 钉钉机器人
  - [ ] Telegram Bot
  - [ ] Discord Bot
  - [ ] Slack Bot
  - [ ] WhatsApp
- [ ] **更多工具**
  - [ ] 网页搜索 API（Bing Search API / SerpAPI）
  - [ ] 数据库查询（SQLite / PostgreSQL）
  - [ ] 电子邮件收发
  - [ ] RSS 订阅监控
  - [ ] 股票/加密货币行情
  - [ ] 地图/天气查询
- [ ] **Task 模式增强**
  - [ ] 更多团队模板（产品团队、创意团队、研究团队）
  - [ ] 子图嵌套与复用
  - [ ] 图执行可视化追踪
- [ ] **部署**
  - [ ] Docker 容器化
  - [ ] 一键部署脚本
  - [ ] Web UI 托管版本

---

## 🙏 致谢

本项目的设计和实现大量参考了以下优秀开源项目：

| 项目 | 参考内容 |
|---|---|
| **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** | 工具系统架构、Cron 调度器、飞书 WebSocket 网关、审批机制、Agent 循环 |
| **[LangGraph](https://github.com/langchain-ai/langgraph)** | 状态图工作流引擎、节点/边路由、Checkpointer |
| **[OpenClaw](https://github.com/NousResearch/openclaw)** | 多智能体协作模式、浏览器自动化策略 |
| **[LangGraph Studio](https://github.com/langchain-ai/langgraph-studio)** | 可视化图编辑器 UI/UX 设计参考 |
| **[browser-use](https://github.com/browser-use/browser-use)** | AI 浏览器自动化能力 |
| **[Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp)** | 浏览器降级方案 |

## License

MIT
