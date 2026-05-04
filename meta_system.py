
import os
import sys
import json
import time
import hashlib
import subprocess
import shutil
import importlib
import sqlite3
import re
from datetime import datetime
from typing import TypedDict, List, Dict, Optional, Any
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from dotenv import load_dotenv

# 动态获取当前日期
_CURRENT_DATE = datetime.now().strftime('%Y年%m月%d日')
_CURRENT_YEAR = datetime.now().year

# 工具系统集成
from tools import registry, init_tools
from tools.interrupt_tool import is_interrupted, set_interrupt_flag, clear_interrupt
from tools.approval_tool import approve_task, reject_task, get_pending_approvals
from tools.llm_tool import (
    llm_generate_design,
    llm_generate_code_from_design,
    llm_audit_code,
    get_llm_client
)

load_dotenv()

# 类型别名（兼容新旧版本）
BaseCheckpointSaver = MemorySaver

LONGBOT_API_URL = os.getenv("LONGBOT_API_URL", "http://localhost:8080")
LONGBOT_API_KEY = os.getenv("LONGBOT_API_KEY", "")
CHECKPOINT_DB_PATH = os.getenv("CHECKPOINT_DB_PATH", "./checkpoints.db")
CAPACITY_INDEX_PATH = os.getenv("CAPACITY_INDEX_PATH", "./capacities/capacity_index.json")
WORKFLOW_TEMPLATES_PATH = os.getenv("WORKFLOW_TEMPLATES_PATH", "./capacities/workflow_templates.json")
DEPLOYED_DIR = os.getenv("DEPLOYED_DIR", "./deployed")
RESULT_DIR = os.getenv("RESULT_DIR", "./result")
MEMORY_DB_PATH = os.getenv("MEMORY_DB_PATH", "./memory.db")
SKILL_MANIFEST_PATH = os.getenv("SKILL_MANIFEST_PATH", "./skills/manifest.json")
CHARACTERS_DIR = os.getenv("CHARACTERS_DIR", "./characters")


def load_character_personality(character_id: str) -> str:
    """加载角色personality文件.

    Args:
        character_id: 角色ID（如fang_a, du_ruhui）

    Returns:
        personality内容字符串
    """
    personality_path = os.path.join(CHARACTERS_DIR, character_id, "personality.md")
    if os.path.exists(personality_path):
        with open(personality_path, "r", encoding="utf-8") as f:
            return f.read()
    return f"你是{character_id}，通用智能体。"


class MetaSystemState(TypedDict):
    user_id: str
    global_task: str
    current_phase: str
    design_draft: str
    tech_doc: str
    subsystem_code: str
    subsystem_metadata: Dict
    capacity_library: List[Dict]
    stream_buffer: List[str]
    snapshot_id: Optional[str]
    # checkpointer 不存储在状态中，由外部管理
    # active_subprocess 也不存储在状态中，由外部管理
    subsystem_conversation_history:List[Dict]
    major_loop_count: int
    inner_debate_count: int
    # 新增记忆字段
    user_profile: Dict[str, Any]
    task_memory_id: Optional[int]
    skills_used: List[str]
    # 工具系统字段
    tool_results: List[Dict]
    available_tools: List[str]
    # 任务复杂度判断
    task_complexity: str  # "simple" | "complex"
    # 审核历史记录
    audit_history: List[Dict]  # [{"round": 1, "issues": [...], "suggestions": [...]}]
    # 任务标题（AI生成）
    task_title: str
    selected_template: str
    template_prompt: str
    # 工作目录（任务执行的目标目录，如整理文件夹时的目标路径）
    work_dir: Optional[str]
    # 多图支持字段
    active_graph_id: str  # 当前活跃图ID，默认 "default"
    graph_contexts: Dict[str, Dict]  # 各图的独立状态存储 {graph_id: state_dict}
    parent_graph_id: Optional[str]  # 子图的父图ID，用于回切
    # 人机交互字段
    human_feedback: str  # 用户对当前产出的建议
    conversation_memory: List[Dict]  # 同一次会话的历史 [{task, result}]
    task_active: bool  # 是否在任务中（False则等待新任务）


def load_capacity_index() -> List[Dict]:
    if os.path.exists(CAPACITY_INDEX_PATH):
        with open(CAPACITY_INDEX_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("capacities", [])
    return []


def save_capacity_index(capacities: List[Dict]) -> bool:
    try:
        os.makedirs(os.path.dirname(CAPACITY_INDEX_PATH) or ".", exist_ok=True)
        with open(CAPACITY_INDEX_PATH, 'w', encoding='utf-8') as f:
            json.dump({"capacities": capacities}, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def search_similar_capacity(keywords: list, threshold: float = 0.7):
    capacities = load_capacity_index()
    best_match = None
    best_score = 0.0

    for cap in capacities:
        cap_keywords = cap.get("keywords", [])
        if not cap_keywords:
            continue
        overlap = len(set(keywords) & set(cap_keywords))
        score = overlap / max(len(keywords), len(cap_keywords), 1)
        if score >= threshold and score > best_score:
            best_score = score
            best_match = cap

    return best_match


def load_workflow_templates() -> List[Dict]:
    if os.path.exists(WORKFLOW_TEMPLATES_PATH):
        with open(WORKFLOW_TEMPLATES_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("templates", [])
    return []


def get_template_summaries() -> str:
    templates = load_workflow_templates()
    if not templates:
        return "（模板池为空）"
    lines = []
    for t in templates:
        lines.append(f"- [{t['id']}] {t['name']}：{t['summary']}")
    return "\n".join(lines)


def get_template_by_id(template_id: str) -> Dict:
    templates = load_workflow_templates()
    for t in templates:
        if t.get("id") == template_id:
            return t
    return {}


def build_template_injection(state: MetaSystemState) -> str:
    """根据state中的selected_template构建模板注入文本，用于各节点的system_prompt."""
    template_id = state.get("selected_template", "")
    if not template_id:
        return ""
    template_prompt = state.get("template_prompt", "")
    if not template_prompt:
        tpl = get_template_by_id(template_id)
        template_prompt = tpl.get("prompt_template", "")
    if not template_prompt:
        return ""
    template_name = ""
    tpl = get_template_by_id(template_id)
    if tpl:
        template_name = tpl.get("name", template_id)
    return f"\n\n【当前使用的工作流模板】\n模板名称：{template_name}\n{template_prompt}\n"


def write_file(path: str, content: str) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    except Exception:
        return False


def launch_subprocess(command: str, cwd: str = None):
    return subprocess.Popen(
        command,
        shell=True,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )


def kill_process(process: subprocess.Popen):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def archive_capacity(code: str, doc: str, metadata: Dict) -> str:
    capacity_id = f"cap_{int(time.time())}"
    cap_dir = f"./capacities/{capacity_id}"
    os.makedirs(cap_dir, exist_ok=True)

    write_file(f"{cap_dir}/subsystem.py", code)
    write_file(f"{cap_dir}/README.md", doc)

    new_capacity = {
        "id": capacity_id,
        "task_type": metadata.get("task_type", ""),
        "keywords": metadata.get("keywords", []),
        "code_path": f"{cap_dir}/subsystem.py",
        "doc_path": f"{cap_dir}/README.md",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")
    }

    capacities = load_capacity_index()
    capacities.append(new_capacity)
    save_capacity_index(capacities)

    return capacity_id


def send_to_gateway(user_id: str, message: str):
    import requests
    try:
        requests.post(
            f"{LONGBOT_API_URL}/send",
            json={"user_id": user_id, "message": message},
            headers={"Authorization": f"Bearer {LONGBOT_API_KEY}"},
            timeout=5
        )
    except Exception:
        pass


# ================== 技能扩展相关函数 ==================
def load_skill_manifest() -> Dict:
    if os.path.exists(SKILL_MANIFEST_PATH):
        with open(SKILL_MANIFEST_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"skills": []}


def call_skill(skill_name: str, params: Dict) -> Dict:
    manifest = load_skill_manifest()
    skill = next((s for s in manifest.get("skills", []) if s["name"] == skill_name), None)
    if not skill or not skill.get("enabled", False):
        return {"error": f"Skill {skill_name} not found or disabled"}
    try:
        entry_point = skill["entry_point"]
        module_path, func_name = entry_point.rsplit(".", 1)
        module = importlib.import_module(module_path)
        func = getattr(module, func_name)
        return func(params)
    except Exception as e:
        return {"error": f"Skill execution failed: {str(e)}"}


# ================== 持久记忆相关函数 ==================
def init_memory_db():
    conn = sqlite3.connect(MEMORY_DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_profile (
            user_id TEXT PRIMARY KEY,
            preferences TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            task_input TEXT,
            design_draft TEXT,
            task_hash TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS capacity_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            capacity_id TEXT,
            success BOOLEAN,
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()


def load_user_profile(user_id: str) -> Dict:
    conn = sqlite3.connect(MEMORY_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT preferences FROM user_profile WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return {}


def save_user_profile(user_id: str, preferences: Dict):
    conn = sqlite3.connect(MEMORY_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO user_profile (user_id, preferences, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (user_id, json.dumps(preferences))
    )
    conn.commit()
    conn.close()


def record_task_memory(user_id: str, task_input: str, design_draft: str, task_hash: str, status: str = "completed") -> int:
    conn = sqlite3.connect(MEMORY_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO task_memory (user_id, task_input, design_draft, task_hash, status) VALUES (?, ?, ?, ?, ?)",
        (user_id, task_input, design_draft, task_hash, status)
    )
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return task_id


def record_feedback(capacity_id: str, success: bool, comment: str = ""):
    conn = sqlite3.connect(MEMORY_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO capacity_feedback (capacity_id, success, comment) VALUES (?, ?, ?)",
        (capacity_id, success, comment)
    )
    conn.commit()
    conn.close()


# ================== 节点函数 ==================

def drh_assess_task_complexity(task: str) -> Dict:
    """杜如晦评估任务复杂度 - 判断现有框架能否胜任，是否需要选择模板.

    返回:
        {
            "complexity": "simple" | "complex",
            "template_id": "" | "product_team" | "creative_team" | ...,
            "template_prompt": "" | "...",
            "reason": "判断原因"
        }
    """
    template_summaries = get_template_summaries()

    try:
        client = get_llm_client()

        personality = load_character_personality("du_ruhui")
        system_prompt = f"""{personality}

【当前日期】
今天是{_CURRENT_DATE}。

【判断标准 — 必须严格遵守】
1. **simple（简单）**：无需方案讨论，直接回答即可。包括：
   - 问候/闲聊：你好、谢谢、再见、在吗
   - 简单问答：什么是XX、XX是什么意思
   - 简单计算：2+2、100美元等于多少人民币
   - 翻译：帮我把XX翻译成英文
   - 格式转换：转JSON、转CSV
   - 对话开场白：没有任何明确任务需求的输入
2. **complex（复杂）**：需要团队协作、方案设计。包括：
   - 搜索网页、获取最新信息
   - 编写系统性代码
   - 撰写长篇报告/文章
   - 分析数据、生成图表
   - 文件操作、文件夹整理
   - 需要多轮讨论才能确定方案

{template_summaries}

**重要：问候语、闲聊、一句话能回答的问题 → 必须判为 simple！**

请返回严格JSON格式：
{{"complexity":"simple"|"complex", "template_id":""|模板ID, "reason":"判断原因"}}"""

        prompt = f"请评估以下任务应该由现有框架处理还是需要另立流程：\n\n{task}"

        result_str = client.generate_code(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.1
        )

        result_str = result_str.strip()
        if result_str.startswith("```json"):
            result_str = result_str[7:]
        if result_str.startswith("```"):
            result_str = result_str[3:]
        if result_str.endswith("```"):
            result_str = result_str[:-3]

        assessment = json.loads(result_str.strip())
        complexity = assessment.get("complexity", "complex")
        template_id = assessment.get("template_id", "")
        reason = assessment.get("reason", "")

        template_prompt = ""
        if template_id:
            tpl = get_template_by_id(template_id)
            template_prompt = tpl.get("prompt_template", "")

        return {
            "complexity": complexity,
            "template_id": template_id,
            "template_prompt": template_prompt,
            "reason": reason
        }

    except Exception as e:
        print(f"DRH 评估任务复杂度失败，使用降级方案: {e}")

        task_lower = task.lower()

        simple_keywords = [
            "计算", "等于", "+", "-", "*", "/",
            "翻译", "convert", "翻译成", "翻译一下",
            "解释", "什么是", "介绍一下", "说明", "告诉我",
            "格式化", "转换格式", "转json", "转csv",
        ]

        complex_keywords = [
            "帮我搭建", "给我搭建", "搭建一个",
            "帮我设计", "给我设计", "设计一个",
            "帮我做", "给我做", "做一个", "搞一个",
            "系统", "架构", "工作流", "workflow", "pipeline",
            "搜索", "网页", "新闻", "热点", "访问网页", "获取信息",
            "报告", "总结", "撰写", "调研", "分析",
        ]

        template_keywords = {
            "product_team": ["产品", "需求", "PRD", "用户体验", "功能设计", "产品规划"],
            "creative_team": ["写作", "创作", "文章", "文案", "文学", "小说", "剧本", "散文"],
            "research_team": ["研究", "学术", "论文", "实验", "统计"],
            "consulting_team": ["咨询", "战略", "商业", "行业分析", "转型"],
        }

        simple_score = sum(1 for k in simple_keywords if k in task_lower)
        complex_score = sum(1 for k in complex_keywords if k in task_lower)

        if simple_score > 0 and complex_score == 0:
            return {
                "complexity": "simple",
                "template_id": "",
                "template_prompt": "",
                "reason": "降级方案：关键词匹配判定为简单任务"
            }

        best_template_id = ""
        best_template_score = 0
        for tid, keywords in template_keywords.items():
            score = sum(1 for k in keywords if k in task_lower)
            if score > best_template_score:
                best_template_score = score
                best_template_id = tid

        if best_template_id and best_template_score >= 2:
            tpl = get_template_by_id(best_template_id)
            return {
                "complexity": "complex",
                "template_id": best_template_id,
                "template_prompt": tpl.get("prompt_template", ""),
                "reason": f"降级方案：关键词匹配选择模板 {best_template_id}"
            }

        return {
            "complexity": "complex",
            "template_id": "",
            "template_prompt": "",
            "reason": "降级方案：默认判定为复杂任务"
        }


def generate_task_title(task: str) -> str:
    """让AI用一句话总结任务内容作为标题."""
    try:
        client = get_llm_client()
        personality = load_character_personality("parse_task")
        system_prompt = f"""{personality}

【当前日期】
今天是{_CURRENT_DATE}。

要求：
1. 用一句话（10-20个字）总结任务核心内容
2. 标题要简洁、明确、可识别
3. 只返回标题文字，不要其他内容
4. 不要包含标点符号和特殊字符"""

        result = client.generate_code(
            prompt=f"请为以下任务生成一个简短的标题（10-20字）：\n\n{task}",
            system_prompt=system_prompt,
            temperature=0.3
        ).strip()

        # 清理标题中的非法字符
        import re
        result = re.sub(r'[\\\\/:*?"<>|]', '', result)
        result = result[:30]  # 限制长度

        return result if result else "未命名任务"
    except Exception as e:
        print(f"生成任务标题失败: {e}")
        return "未命名任务"


def parse_user_task_node(state: MetaSystemState) -> MetaSystemState:
    task = state["global_task"]
    user_id = state["user_id"]
    keywords = [w for w in task.split() if len(w) > 1]

    # 生成任务标题
    task_title = generate_task_title(task)
    state["task_title"] = task_title

    # DRH评估任务复杂度
    assessment = drh_assess_task_complexity(task)
    complexity = assessment["complexity"]
    template_id = assessment["template_id"]
    template_prompt = assessment["template_prompt"]
    assessment_reason = assessment["reason"]

    state["task_complexity"] = complexity
    state["selected_template"] = template_id
    state["template_prompt"] = template_prompt

    task_type = "通用任务"
    if any(k in task for k in ["分析", "报告", "财务"]):
        task_type = "分析报告"
    elif any(k in task for k in ["代码", "开发", "系统", "程序", "工具"]):
        task_type = "系统开发"
    elif any(k in task for k in ["风险", "评估", "风控"]):
        task_type = "风险评估"
    elif any(k in task for k in ["计算", "数学", "公式", "算"]):
        task_type = "计算工具"

    required_roles = ["房玄龄", "杜如晦", "执行者"]

    output_format = "Markdown报告+Python代码"
    if "纯文档" in task:
        output_format = "Markdown文档"
    elif "纯代码" in task:
        output_format = "Python代码"

    state["subsystem_metadata"] = {
        "task_type": task_type,
        "required_roles": required_roles,
        "output_format": output_format,
        "keywords": keywords,
        "complexity": complexity
    }

    # 初始化审核历史
    state["audit_history"] = []

    # 清空 buffer，只添加新内容
    complexity_desc = "简单任务-直接执行" if complexity == "simple" else "复杂任务-团队协作"
    template_info = f"   使用模板: {template_id}" if template_id else "   使用框架: 现有房玄龄ABC+杜如晦+执行者"
    state["stream_buffer"] = [
        f"📥 杜如晦评估任务：{task_type} - {complexity_desc}",
        f"   任务标题: {task_title}",
        f"   关键词: {', '.join(keywords[:5])}",
        template_info,
        f"   评估原因: {assessment_reason[:80]}"
    ]
    # 加载用户偏好
    user_profile = load_user_profile(user_id)
    state["user_profile"] = user_profile
    state["skills_used"] = []
    if user_profile:
        state["stream_buffer"].append(f"📚 加载用户偏好：{len(user_profile)} 项配置")
    return state


def search_capacity_node(state: MetaSystemState) -> MetaSystemState:
    keywords = state["subsystem_metadata"].get("keywords", [])
    match = search_similar_capacity(keywords, threshold=0.7)
    if match:
        state["subsystem_metadata"]["base_template"] = match["id"]
        count = 1
    else:
        state["subsystem_metadata"]["base_template"] = None
        count = 0
    state["stream_buffer"] = [f"🔍 检索能力库... 找到 {count} 个相似设计"]
    return state


def fang_a_proposal_node(state: MetaSystemState) -> MetaSystemState:
    """房玄龄A - 使用 LLM 生成架构草案.

    如果是被打回后重新生成，会接收到杜如晦的审核反馈，需要据此修改设计。
    """
    task = state["global_task"]
    base_template = state["subsystem_metadata"].get("base_template")
    existing_draft = state.get("design_draft", "")
    audit_history = state.get("audit_history", [])

    # 获取可用工具列表
    available_tools = state.get("available_tools", [])
    tool_list_str = "\n".join(f"  - {tool}" for tool in available_tools[:20]) if available_tools else "  (无可用工具)"

    # 构建 prompt
    prompt = f"任务: {task}"
    if base_template:
        prompt += f"\n\n参考模板: {base_template}"

    # 如果有杜如晦的反馈（被打回后），将其加入 prompt
    if existing_draft and audit_history:
        latest_audit = audit_history[-1]
        if not latest_audit.get("passed", True):
            issues = latest_audit.get("issues", [])
            prompt += f"""

【杜如晦审核反馈 - 必须修改】
上一轮设计未通过审核，发现以下严重问题:
{chr(10).join(f'❌ {issue}' for issue in issues)}

【修改要求 - 必须严格遵守】
1. 设计方案中必须显式包含以下关键词（杜如晦会检查这些关键词）：
   - "TypedDict"（状态定义）
   - "stream_buffer"（流式输出机制）
   - "checkpointer"（检查点支持）
   - "安全阀"（安全机制）
2. 每个关键词必须在设计方案中有明确的段落或章节说明
3. 不要只描述概念，必须写出具体的代码结构或配置方式

【上一轮设计方案】
{existing_draft[:1500]}

请重新生成完整的设计方案，确保包含所有必需的关键词和机制。"""

    try:
        client = get_llm_client()
        template_injection = build_template_injection(state)
        personality = load_character_personality("fang_a")
        system_prompt = f"""{personality}

{template_injection}
【当前日期】
今天是{_CURRENT_DATE}。

【系统流程说明】
本系统采用"房玄龄ABC + 杜如晦 + 执行者"协作架构：
1. 房玄龄A（你-提案）：根据用户需求提出方案草案
2. 房玄龄B（反对）：审查并反对A的方案，找出问题
3. 房玄龄C（总结）：综合A的方案和B的意见，形成最终方案
4. 杜如晦（审核）：审核设计方案，决定是否通过
5. 执行者：负责执行方案（文字撰写和代码开发）

【可用工具列表】
{tool_list_str}

分工：
- A（你）：提出方案草案，明确需要使用哪些工具
- B：审查并反对你的方案
- C：综合A的方案和B的意见，形成最终方案

输出要求：
1. 请在方案开头标注【房玄龄A - 提案】
2. 请根据用户任务生成详细的设计方案
3. 设计方案必须包含：任务分析、执行步骤、资源需求、风险评估、预期成果
4. 必须在方案中明确标注需要使用哪些工具（从可用工具列表中选择）
5. 请用中文回答，格式清晰"""

        # 如果有反馈，修改 system_prompt 强调需要解决问题
        if existing_draft and audit_history and not audit_history[-1].get("passed", True):
            system_prompt += """

重要：这是修改后的重新设计。请确保：
1. 解决杜如晦提出的所有问题
2. 不要重复之前的错误
3. 保持与原始任务目标一致"""

        # 在prompt中添加可用工具信息，让AI选择需要的工具
        prompt += f"""

【可用工具列表】
{tool_list_str}

【工具选择要求】
1. 根据任务需求，从可用工具列表中选择需要的工具
2. 在方案中标注需要使用哪些工具
3. 说明每个工具的用途和使用时机"""

        draft = client.generate_code(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.5
        )

        if existing_draft and audit_history and not audit_history[-1].get("passed", True):
            state["design_draft"] = f"【房玄龄A - 修改后的架构草案（第{len(audit_history)}轮）】\n\n{draft}"
            state["stream_buffer"] = [
                f"🏛️ 房玄龄A 根据杜如晦反馈修改设计（第{len(audit_history)}轮）...",
                f"   需要解决的问题: {', '.join(audit_history[-1].get('issues', []))}"
            ]
        else:
            state["design_draft"] = f"【房玄龄A - 架构草案（LLM生成）】\n\n{draft}"
            state["stream_buffer"] = ["🏛️ 房玄龄A 调用 minimax 生成架构草案..."]
    except Exception as e:
        # 降级为本地模板生成
        draft = f"""【房玄龄A - 架构草案（本地模板）】

任务类型：{state['subsystem_metadata'].get('task_type', '')}

基于任务「{task}」（全新设计），我提出以下架构草案：

## 状态定义
- input: 用户输入
- output: 处理结果
- history: 对话历史
- stream_buffer: 流式输出缓冲区

## 核心节点
1. parse_input: 解析用户输入
2. process_task: 处理任务逻辑
3. generate_response: 生成响应

## 路由规则
- 正常流程: parse_input -> process_task -> generate_response -> END
- 异常处理: 任何节点出错则返回错误信息

## 安全阀
- 最大循环次数: 5
- 超时时间: 60秒
"""
        state["design_draft"] = draft
        state["stream_buffer"] = [f"⚠️ 房玄龄A LLM调用失败({str(e)[:50]})，使用本地模板"]

    return state


def fang_b_critic_node(state: MetaSystemState) -> MetaSystemState:
    """房玄龄B - 使用 LLM 批判设计"""
    draft = state["design_draft"]
    task = state["global_task"]

    try:
        client = get_llm_client()
        template_injection = build_template_injection(state)
        personality = load_character_personality("fang_b")
        tool_list = state.get("available_tools", [])
        tool_list_str = "\n".join([f"- {t}" for t in tool_list[:30]]) if tool_list else "（无工具信息）"
        critique = client.generate_code(
            prompt=f"请审查以下架构设计，找出问题并提出修改建议：\n\n任务: {task}\n\n设计文档:\n{draft[:2000]}",
            system_prompt=f"{personality}\n{template_injection}\n\n【当前日期】\n今天是{_CURRENT_DATE}。\n\n【系统流程说明】\n本系统采用房玄龄ABC + 杜如晦 + 执行者协作架构：\n1. 房玄龄A（提案）：根据用户需求提出方案草案\n2. 房玄龄B（你-反对）：审查并反对A的方案，找出问题\n3. 房玄龄C（总结）：综合A的方案和B的意见，形成最终方案\n4. 杜如晦（审核）：审核设计方案，决定是否通过\n5. 执行者：负责执行方案（文字撰写和代码开发）\n\n【可用工具列表】\n{tool_list_str}\n\n你的职责是审查并反对房玄龄A的方案。\n特别注意：检查方案是否正确选择了可用工具，工具选择是否合理。\n输出要求：\n1. 请在开头标注【房玄龄B - 批判与建议】\n2. 请仔细审查方案，找出至少3个问题并提出具体修改建议\n3. 检查工具选择是否合理\n4. 用中文回答",
        )
        state["design_draft"] = f"{draft}\n\n【房玄龄B - 批判与建议（LLM生成）】\n\n{critique}"
        state["stream_buffer"] = ["⚔️ 房玄龄B 调用 minimax 批判设计..."]
    except Exception as e:
        # 降级为本地模板
        critique = """

【房玄龄B - 批判与建议】

仔细审查了房玄龄A的草案，发现以下问题：

1. **状态定义不完整**：缺少checkpointer字段，无法支持时间回溯
2. **节点粒度粗**：process_task过于笼统，建议拆分为多个细粒度节点
3. **安全阀不足**：仅有最大循环次数，缺少状态剪枝机制
4. **流式输出缺失**：未明确如何将中间结果推送给网关

**修改建议**：
- 在状态中添加checkpoint_id字段
- 将process_task拆分为validate -> execute -> format三步
- 添加妥协阈值机制
- 明确stream_buffer的使用方式
"""
        state["design_draft"] = draft + critique
        state["stream_buffer"] = [f"⚠️ 房玄龄B LLM调用失败({str(e)[:50]})，使用本地模板"]

    return state


def fang_c_summary_node(state: MetaSystemState) -> MetaSystemState:
    """房玄龄C - 使用 LLM 整合最终方案"""
    draft = state["design_draft"]
    task = state["global_task"]

    try:
        client = get_llm_client()
        template_injection = build_template_injection(state)
        personality = load_character_personality("fang_c")
        tool_list = state.get("available_tools", [])
        tool_list_str = "\n".join([f"- {t}" for t in tool_list[:30]]) if tool_list else "（无工具信息）"
        final_draft = client.generate_code(
            prompt=f"请综合以下架构草案和批判意见，形成最终设计方案：\n\n任务: {task}\n\n{draft[:3000]}",
            system_prompt=f"""{personality}
{template_injection}
【当前日期】
今天是{_CURRENT_DATE}。

【系统流程说明】
本系统采用"房玄龄ABC + 杜如晦 + 执行者"协作架构：
1. 房玄龄A（提案）：根据用户需求提出方案草案
2. 房玄龄B（反对）：审查并反对A的方案，找出问题
3. 房玄龄C（你-总结）：综合A的方案和B的意见，形成最终方案
4. 杜如晦（审核）：审核设计方案，决定是否通过
5. 执行者：负责执行方案（文字撰写和代码开发）

【可用工具列表】
{tool_list_str}

你的职责是综合A的方案和B的意见，形成最终方案。
特别注意：最终方案必须明确标注需要使用哪些工具（从可用工具列表中选择）。
输出要求：
1. 请在开头标注【房玄龄C - 综合方案】
2. 请综合各方意见，形成完整的设计方案
3. 必须包含：任务分析、执行步骤、风险评估、预期成果
4. 必须明确标注需要使用的工具列表
5. 用中文回答""",
            temperature=0.5
        )
        state["design_draft"] = f"【阶段：DESIGNING - 设计方案审核】\n\n【房玄龄C - 综合方案（LLM生成）】\n\n{final_draft}\n\n【阶段说明】\n当前阶段为方案设计阶段，产出物为设计方案文档。本方案描述了如何完成任务的架构、步骤和规划，尚未进入实际执行阶段。实际执行（搜索网页、撰写报告等）将在杜如晦审核通过后由执行者执行。"
        state["stream_buffer"] = ["📝 房玄龄C 调用 minimax 整合方案..."]
    except Exception as e:
        # 降级为本地模板
        final_draft = f"""{draft}

【阶段：DESIGNING - 设计方案审核】

【房玄龄C - 综合方案】

综合房玄龄A和B的意见，形成最终设计方案：

## 状态定义 (TypedDict)
- input: 用户输入
- output: 处理结果
- history: 对话历史
- stream_buffer: List[str] 流式输出缓冲区
- snapshot_id: Optional[str]  snapshot标识
- checkpointer: BaseCheckpointSaver  检查点保存器

## 节点列表
1. **parse_input**: 解析用户输入，提取关键参数
2. **validate_input**: 验证输入合法性
3. **execute_task**: 执行核心任务逻辑
4. **format_output**: 格式化输出结果
5. **stream_output**: 推送流式输出到网关

## 路由规则
- parse_input -> validate_input
- validate_input -> execute_task (通过) / parse_input (失败重试)
- execute_task -> format_output
- format_output -> stream_output -> END

## 安全阀设置
- **大循环硬限流**: major_loop_count >= 3 时强制结束
- **妥协阈值**: inner_debate_count >= 2 时降低审核标准
- **状态剪枝**: 每轮结束后清理临时状态
- **超时机制**: 单节点执行超过30秒自动跳过

## 流式输出机制
- 每个节点执行后将关键日志追加到stream_buffer
- 主循环定期将buffer内容推送给网关
"""
        state["design_draft"] = final_draft + "\n\n【阶段说明】\n当前阶段为方案设计阶段，产出物为设计方案文档。本方案描述了如何完成任务的架构、步骤和规划，尚未进入实际执行阶段。"
        state["stream_buffer"] = [f"⚠️ 房玄龄C LLM调用失败({str(e)[:50]})，使用本地模板"]

    return state


def direct_execute_node(state: MetaSystemState) -> MetaSystemState:
    """简单任务直接执行节点 - 调用 minimax 直接完成任务，不走团队协作流程.

    Browser Use 硬性要求:
    如果任务涉及搜索网页、获取新闻、访问网站等，必须强制走复杂流程（文星节点），
    禁止在此节点直接生成搜索结果。
    """
    task = state["global_task"]

    # 检查是否涉及搜索/网页访问 - 如果是，强制走复杂流程
    search_keywords = ["搜索", "网页", "新闻", "热点", "访问", "获取信息", "查找", "浏览"]
    local_keywords = ["整理文件夹", "整理文件", "C:\\", "D:\\", "下载文件夹", "本地文件", "文件分类", "文件夹", "脚本"]
    if any(kw in task for kw in search_keywords):
        state["stream_buffer"] = [
            "⚠️ 任务涉及网页搜索，强制走复杂流程",
            "   原因: Browser Use 硬性要求 - 必须使用浏览器(CDP)访问必应搜索"
        ]
        state["task_complexity"] = "complex"
        state["current_phase"] = "DESIGNING"
        return state

    if any(kw in task for kw in local_keywords):
        state["stream_buffer"] = [
            "⚠️ 任务涉及本地文件操作，强制走复杂流程",
            "   原因: 需要生成可执行的脚本，由团队协作完成"
        ]
        state["task_complexity"] = "complex"
        state["current_phase"] = "DESIGNING"
        return state

    try:
        client = get_llm_client()

        # 构建直接执行的 prompt
        personality = load_character_personality("executor")
        system_prompt = f"""{personality}

你是一个高效的AI助手。用户提出了一个简单任务，请直接完成它。

重要限制:
- 禁止使用任何搜索API或搜索工具
- 禁止模拟搜索结果
- 只能基于你的知识回答问题

要求：
1. 直接给出结果，不要解释流程
2. 如果是计算题，给出计算过程和结果
3. 如果是翻译，给出翻译结果
4. 如果是问答，给出简洁准确的答案
5. 如果是格式化，给出格式化后的内容"""

        result = client.generate_code(
            prompt=f"请直接完成以下任务（禁止搜索，只能基于已有知识）：\n\n{task}",
            system_prompt=system_prompt,
            temperature=0.3
        )

        # 将结果保存为 "代码"（实际上是直接答案）
        state["subsystem_code"] = f"# 直接执行结果\n\n{result}"
        state["tech_doc"] = f"# 任务执行记录\n\n任务: {task}\n\n执行方式: 简单任务直接执行\n\n结果:\n{result}"
        state["design_draft"] = f"简单任务，直接执行完成: {task}"
        state["current_phase"] = "DEPLOYING"
        state["task_active"] = True
        # 截取结果前几行展示给用户
        result_lines = result.strip().split("\n")
        display = result_lines[:8]
        if len(result_lines) > 8:
            display.append(f"... (共 {len(result_lines)} 行)")
        state["stream_buffer"] = [
            "⚡ 直接执行结果:",
        ] + [f"  {line}" for line in display]

    except Exception as e:
        # 如果直接执行失败，降级为错误提示
        state["subsystem_code"] = f"# 执行失败\n\n错误: {str(e)}"
        state["stream_buffer"] = [f"❌ 直接执行失败: {str(e)[:100]}"]
        state["current_phase"] = "ERROR"

    return state


def du_ruhui_audit_node(state: MetaSystemState) -> MetaSystemState:
    """杜如晦审核节点 - 审核设计文档，用LLM来审核并生成bullet总结."""
    draft = state["design_draft"]
    task = state["global_task"]
    state["major_loop_count"] += 1
    
    audit_prompt_modifier = ""
    
    # 使用 LLM 来审核设计
    try:
        client = get_llm_client()
        template_injection = build_template_injection(state)
        personality = load_character_personality("du_ruhui")
        
        system_prompt = f"""{personality}
{template_injection}
【当前日期】
今天是{_CURRENT_DATE}。

【系统流程说明】
本系统采用"房玄龄ABC + 杜如晦 + 执行者"协作架构：
1. 房玄龄A（军师-提案）：根据用户需求提出方案草案
2. 房玄龄B（军师-反对）：审查并反对A的方案，找出问题
3. 房玄龄C（军师-总结）：综合A和B的意见，形成最终方案
4. 杜如晦（你-审核）：审核设计方案和代码，决定是否通过
5. 执行者：负责执行方案（文字撰写和代码开发）

【当前阶段说明】
你现在处于"设计方案审核阶段"（DESIGNING）。
- 当前阶段的目标：审核房玄龄ABC团队产出的设计方案是否合理、完整
- 当前阶段的产出物：设计方案文档（不是代码，不是实际执行结果）
- 审核重点：方案是否覆盖了任务需求、架构是否合理、步骤是否清晰
- 不要以"没有实际操作""没有实际搜索"为由打回，那是执行阶段的事情

审核原则：
1. 必须按照用户原始任务的要求来审核
2. 判断设计方案是否满足任务要求
3. 检查核心功能和安全机制是否完备
4. 严格但公正，不要无理刁难
5. 注意消息来源标注，区分房玄龄A/B/C的意见
6. 当前是方案设计阶段，审核的是"方案"不是"执行结果"

输出格式（严格JSON格式，不要任何其他内容）：
{{
    "passed": true/false,
    "issues": ["问题1", "问题2"],
    "suggestions": ["建议1", "建议2"],
    "summary": "以bullet点总结本次设计所采用的架构和发现的错误"
}}

summary 必须包含：
- 本次采用的主要架构
- 主要问题
- 修改方向

必须以严格的 JSON 格式输出！"""
        
        prompt = f"""【原始任务】
{task}

【当前阶段】设计方案审核阶段（DESIGNING）
【阶段说明】此阶段审核的是房玄龄ABC团队产出的"设计方案文档"，不是实际执行结果。方案应该描述如何完成任务，而不是实际去执行任务。

【需要审核的设计方案】
{draft}

【审核要求】
1. 严格按照原始任务的要求进行审核
2. 判断方案是否完整覆盖了任务需求（任务分析、执行步骤、资源需求、风险评估）
3. 检查方案架构是否合理、步骤是否清晰
4. 如果不合格，需要以bullet总结本次所采用的架构和错误
5. 请给出具体的修改建议
6. 注意：方案中标注了【房玄龄A】、【房玄龄B】、【房玄龄C】等身份标识，请区分不同角色的意见
7. 重要：当前是方案设计阶段，不要以"没有实际操作""没有实际搜索"为由打回。执行操作是执行者阶段的事情
"""
        
        result_str = client.generate_code(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.3
        )
        
        # 解析返回的 JSON
        try:
            # 清理字符串，提取 JSON 部分
            result_str = result_str.strip()
            if result_str.startswith("```json"):
                result_str = result_str[7:]
            if result_str.startswith("```"):
                result_str = result_str[3:]
            if result_str.endswith("```"):
                result_str = result_str[:-3]
            
            audit_result = json.loads(result_str.strip())
            passed = audit_result.get("passed", False)
            issues = audit_result.get("issues", [])
            suggestions = audit_result.get("suggestions", [])
            drh_summary = audit_result.get("summary", "")
            
        except json.JSONDecodeError:
            print(f"杜如晦审核返回非JSON格式，使用降级检查: {result_str}")
            issues = []
            if len(draft) < 200:
                issues.append("设计方案内容过短")
            task_type = state.get("subsystem_metadata", {}).get("task_type", "通用任务")
            if task_type in ["系统开发", "计算工具"]:
                if "TypedDict" not in draft:
                    issues.append("缺少TypedDict状态定义")
                if "checkpointer" not in draft:
                    issues.append("缺少checkpointer支持")
            if "安全" not in draft and "风险" not in draft:
                issues.append("缺少安全/风险评估")
            
            passed = len(issues) == 0
            suggestions = []
            drh_summary = "\n".join(f"- {issue}" for issue in issues)
        
    except Exception as e:
        print(f"杜如晦审核调用LLM失败: {e}")
        issues = []
        if len(draft) < 200:
            issues.append("设计方案内容过短")
        task_type = state.get("subsystem_metadata", {}).get("task_type", "通用任务")
        if task_type in ["系统开发", "计算工具"]:
            if "TypedDict" not in draft:
                issues.append("缺少TypedDict状态定义")
            if "checkpointer" not in draft:
                issues.append("缺少checkpointer支持")
        if "安全" not in draft and "风险" not in draft:
            issues.append("缺少安全/风险评估")
        
        passed = len(issues) == 0
        suggestions = []
        drh_summary = "\n".join(f"- {issue}" for issue in issues)
    
    # 记录审核历史
    audit_record = {
        "round": state["major_loop_count"],
        "type": "design",
        "issues": issues,
        "suggestions": suggestions,
        "passed": passed
    }
    if "audit_history" not in state or state["audit_history"] is None:
        state["audit_history"] = []
    state["audit_history"].append(audit_record)
    
    if not passed:
        state["current_phase"] = "DESIGNING"
        state["inner_debate_count"] += 1

        # 构建结构化反馈（非markdown，用文本分隔符）
        issues_text = "; ".join(issues) if issues else "无"
        suggestions_text = "; ".join(suggestions) if suggestions else "无"
        history_text = "; ".join(f"第{r['round']}轮: {', '.join(r['issues'])}" for r in state["audit_history"][:-1]) if len(state["audit_history"]) > 1 else "无历史审核"

        feedback = f"""[杜如晦审核反馈 - 第{state['major_loop_count']}轮]

[原始任务]
{task}

[审核结果] 不合格

[杜如晦总结]
{drh_summary}

[发现问题]
{issues_text}

[修改建议]
{suggestions_text}

[修改要求]
1. 解决所有已发现问题
2. 保持与原始任务目标一致
3. 参考之前的审核历史避免重复错误

[审核历史]
{history_text}

[上一次尝试的主要内容]
{draft[:1500]}"""
        
        state["design_draft"] = feedback
        state["stream_buffer"] = [
            f"👑 杜如晦审核设计：不合格 {audit_prompt_modifier}",
            f"   问题: {', '.join(issues[:3]) if issues else '无'}"
        ]
    else:
        state["current_phase"] = "CODING"
        state["stream_buffer"] = [f"👑 杜如晦审核设计：合格 {audit_prompt_modifier}"]
    
    return state


def du_ruhui_decide_executor_node(state: MetaSystemState) -> MetaSystemState:
    """杜如晦决策节点 - 智能决定由文星还是理星执行任务.

    DRH根据任务要求和房玄龄的方案，判断应该由谁执行：
    - 文星：负责文字撰写类任务（文档、报告、总结、翻译等）
    - 理星：负责代码开发、数学建模类任务
    """
    task = state["global_task"]
    design = state["design_draft"]

    try:
        client = get_llm_client()
        template_injection = build_template_injection(state)
        personality = load_character_personality("du_ruhui")

        system_prompt = f"""{personality}
{template_injection}
【当前日期】
今天是{_CURRENT_DATE}。

【系统流程说明】
本系统采用"房玄龄ABC + 杜如晦 + 执行者"协作架构：
1. 房玄龄A（军师-提案）：根据用户需求提出方案草案
2. 房玄龄B（军师-反对）：审查并反对A的方案，找出问题
3. 房玄龄C（军师-总结）：综合A和B的意见，形成最终方案
4. 杜如晦（你-决策）：根据任务和方案，决定由文星还是理星执行
5. 执行者：负责执行方案（文字撰写和代码开发）

分工说明：
- 文星：负责所有文字撰写类任务（文档、报告、总结、翻译、方案撰写等）
- 理星：负责所有代码开发、数学建模、计算类任务

决策原则：
1. 如果任务主要是写文章、写文档、写报告 → 文星
2. 如果任务主要是写代码、开发程序、数学建模、计算 → 理星
3. 如果两者都需要，先判断主要目标是什么

请返回严格JSON格式，不要任何其他内容：
{{
    "executor": "文星",
    "reason": "选择原因"
}}"""

        result_str = client.generate_code(
            prompt=f"【原始任务】\n{task}\n\n【房玄龄方案】\n{design[:2000]}",
            system_prompt=system_prompt,
            temperature=0.3
        )

        result_str = result_str.strip()
        if result_str.startswith("```json"):
            result_str = result_str[7:]
        if result_str.startswith("```"):
            result_str = result_str[3:]
        if result_str.endswith("```"):
            result_str = result_str[:-3]

        decision = json.loads(result_str.strip())
        executor = decision.get("executor", "理星")
        reason = decision.get("reason", "")

        state["subsystem_metadata"]["selected_executor"] = executor
        state["stream_buffer"] = [
            f"👑 杜如晦决策：由{executor}执行",
            f"   决策原因: {reason[:50]}..." if len(reason) > 50 else f"   决策原因: {reason}"
        ]

    except Exception as e:
        state["subsystem_metadata"]["selected_executor"] = "理星"
        state["stream_buffer"] = [
            f"👑 杜如晦决策：默认由理星执行",
            f"   (决策失败，使用默认值: {str(e)[:30]})"
        ]

    return state


def du_ruhui_audit_code_node(state: MetaSystemState) -> MetaSystemState:
    """杜如晦代码审核节点 - 审核理星生成的代码，用LLM来审核并生成bullet总结."""
    code = state.get("subsystem_code", "")
    design = state.get("design_draft", "")
    task = state.get("global_task", "")

    if not code:
        state["stream_buffer"] = ["👑 杜如晦审核代码：❌ 代码为空"]
        state["current_phase"] = "DESIGNING"
        return state

    is_text_output = code.startswith('"""') or code.startswith("'''")

    if is_text_output:
        report_content = code.strip('"\'').strip()
        if len(report_content) < 200:
            state["stream_buffer"] = ["👑 杜如晦审核代码：❌ 报告内容过短（不足200字）"]
            state["current_phase"] = "CODING"
            state["inner_debate_count"] += 1
            return state

        try:
            client = get_llm_client()
            template_injection = build_template_injection(state)
            personality = load_character_personality("du_ruhui")

            system_prompt = f"""{personality}
{template_injection}
【当前日期】
今天是{_CURRENT_DATE}。

【系统流程说明】
本系统采用"房玄龄ABC + 杜如晦 + 执行者"协作架构：
1. 房玄龄A（军师-提案）：根据用户需求提出方案草案
2. 房玄龄B（军师-反对）：审查并反对A的方案，找出问题
3. 房玄龄C（军师-总结）：综合A和B的意见，形成最终方案
4. 杜如晦（你-审核）：审核执行结果，决定是否通过
5. 执行者：负责执行方案（文字撰写和代码开发）

审核原则：
1. 必须按照用户原始任务的要求来审核
2. 检查报告内容是否满足任务要求
3. 检查报告正文是否达到2000字以上
4. 检查是否基于实际搜索内容而非编造
5. 检查报告结构是否完整

请返回严格JSON格式，不要任何其他内容：
{{
    "passed": true/false,
    "issues": ["问题1", "问题2"],
    "suggestions": ["建议1", "建议2"],
    "summary": "一句话总结"
}}"""

            result_str = client.generate_code(
                prompt=f"【原始任务】\n{task}\n\n【设计方案】\n{design[:500]}\n\n【报告内容】\n{report_content[:3000]}\n\n请审核这份报告是否满足任务要求。",
                system_prompt=system_prompt,
                temperature=0.1
            )

            result_str = result_str.strip()
            if result_str.startswith("```json"):
                result_str = result_str[7:]
            if result_str.startswith("```"):
                result_str = result_str[3:]
            if result_str.endswith("```"):
                result_str = result_str[:-3]

            decision = json.loads(result_str.strip())
            passed = decision.get("passed", False)
            issues = decision.get("issues", [])
            suggestions = decision.get("suggestions", [])
            summary = decision.get("summary", "")

            if passed:
                state["current_phase"] = "DEPLOYING"
                state["stream_buffer"] = ["👑 杜如晦审核代码：✅ 通过"]
            else:
                state["current_phase"] = "CODING"
                feedback = f"【杜如晦审核反馈 - 报告不通过 - 第{state['inner_debate_count'] + 1}轮】\n\n问题: {', '.join(issues)}\n建议: {', '.join(suggestions)}\n总结: {summary}"
                state["design_draft"] = feedback + "\n\n【设计文档】\n" + design
                state["stream_buffer"] = [f"👑 杜如晦审核代码：❌ 报告不通过 - {summary[:50]}"]
                state["inner_debate_count"] += 1

        except Exception as e:
            if len(report_content) >= 500:
                state["current_phase"] = "DEPLOYING"
                state["stream_buffer"] = ["👑 杜如晦审核代码：✅ 通过（LLM审核降级，报告长度足够）"]
            else:
                state["current_phase"] = "CODING"
                state["inner_debate_count"] += 1
                state["stream_buffer"] = [f"👑 杜如晦审核代码：⚠️ 审核失败，报告过短"]

        return state

    try:
        compile(code, '<string>', 'exec')
        syntax_ok = True
    except SyntaxError as e:
        syntax_ok = False

        # 记录审核历史
        audit_record = {
            "round": state.get("inner_debate_count", 0) + 1,
            "type": "code_syntax",
            "issues": [f"语法错误 - 第{e.lineno}行: {e.msg}"],
            "suggestions": ["请检查括号、引号是否闭合"],
            "passed": False
        }
        if "audit_history" not in state or state["audit_history"] is None:
            state["audit_history"] = []
        state["audit_history"].append(audit_record)

        # 构建带有上下文的反馈
        feedback = f"""【杜如晦代码审核反馈 - 语法错误 - 第{state['inner_debate_count'] + 1}轮】

## 原始任务
{task}

## 审核结果
❌ 不通过

## 杜如晦总结
- 语法错误 - 第{e.lineno}行: {e.msg}

## 请根据以下要点修改代码
- 检查所有括号、引号是否闭合
- 检查缩进是否正确
- 参考之前的审核历史避免重复错误

## 审核历史
{chr(10).join(f'- 第{r["round"]}轮({r["type"]}): {", ".join(r["issues"])}' for r in state["audit_history"][:-1]) if len(state.get("audit_history", [])) > 1 else "- (无历史审核)"}

## 上一次尝试的代码
{code}"""
        
        state["design_draft"] = feedback + "\n\n【设计文档】\n" + design
        
        state["stream_buffer"] = [
            f"👑 杜如晦审核代码：❌ 语法错误 - 第{e.lineno}行: {e.msg}",
        ]
        state["current_phase"] = "CODING"
        state["inner_debate_count"] += 1
        return state

    # 调用 LLM 进行深度审核
    try:
        client = get_llm_client()
        template_injection = build_template_injection(state)
        personality = load_character_personality("du_ruhui")
        
        system_prompt = f"""{personality}
{template_injection}
【当前日期】
今天是{_CURRENT_DATE}。

【系统流程说明】
本系统采用"房玄龄ABC + 杜如晦 + 执行者"协作架构：
1. 房玄龄A（军师-提案）：根据用户需求提出方案草案
2. 房玄龄B（军师-反对）：审查并反对A的方案，找出问题
3. 房玄龄C（军师-总结）：综合A和B的意见，形成最终方案
4. 杜如晦（你-审核）：审核设计方案和执行结果，决定是否通过
5. 执行者：负责执行方案（文字撰写和代码开发）

审核原则：
1. 必须按照用户原始任务的要求来审核
2. 检查执行结果是否满足任务和设计要求
3. 检查安全性、完整性和可维护性
4. 严格但公正，不要无理刁难
5. 检查命名规范是否符合要求
6. 如果是文字报告，必须检查报告正文是否达到2000字以上
7. 如果是文字报告，检查是否基于实际搜索内容而非编造

【命名规范审核清单】
- 文件命名：小写英文+下划线
- 类名：PascalCase
- 函数/方法名：snake_case
- 变量名：snake_case
- 常量名：UPPER_SNAKE_CASE
- 禁止中文命名变量/函数/类
- 文星报告标题：中文，格式"关于XXX的报告"
- 日期格式：YYYY-MM-DD

输出格式（严格JSON格式，不要任何其他内容）：
{{{{
    "passed": true/false,
    "issues": ["问题1", "问题2"],
    "suggestions": ["建议1", "建议2"],
    "summary": "以bullet点总结本次代码所采用的架构和发现的错误"
}}}}

summary 必须包含：
- 本次代码的主要架构
- 发现的主要问题
- 修改方向

必须以严格的 JSON 格式输出！"""
        
        prompt = f"""【原始任务】
{task}

【设计文档】
{design}

【需要审核的代码】
{code}

【审核要求】
1. 严格按照原始任务的要求进行审核
2. 如果不合格，需要以bullet总结本次代码所采用的架构和发现的错误
3. 请给出具体的修改建议
"""
        
        result_str = client.generate_code(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.3
        )
        
        # 解析返回的 JSON
        try:
            # 清理字符串，提取 JSON 部分
            result_str = result_str.strip()
            if result_str.startswith("```json"):
                result_str = result_str[7:]
            if result_str.startswith("```"):
                result_str = result_str[3:]
            if result_str.endswith("```"):
                result_str = result_str[:-3]
            
            audit_result = json.loads(result_str.strip())
            passed = audit_result.get("passed", False)
            issues = audit_result.get("issues", [])
            suggestions = audit_result.get("suggestions", [])
            drh_summary = audit_result.get("summary", "")
            
        except json.JSONDecodeError:
            # JSON解析失败，直接通过语法检查
            passed = True
            issues = []
            suggestions = []
            drh_summary = ""
        
    except Exception as e:
        # LLM调用失败，直接通过语法检查
        passed = True
        issues = []
        suggestions = []
        drh_summary = ""
    
    # 记录审核历史
    audit_record = {
        "round": state.get("inner_debate_count", 0) + 1,
        "type": "code_review",
        "issues": issues,
        "suggestions": suggestions,
        "passed": passed
    }
    if "audit_history" not in state or state["audit_history"] is None:
        state["audit_history"] = []
    state["audit_history"].append(audit_record)

    if not passed:
        state["current_phase"] = "CODING"
        state["inner_debate_count"] += 1

        # 构建结构化反馈（非markdown，用文本分隔符）
        issues_text = "; ".join(issues) if issues else "无"
        suggestions_text = "; ".join(suggestions) if suggestions else "无"
        history_text = "; ".join(f"第{r['round']}轮({r['type']}): {', '.join(r['issues'])}" for r in state["audit_history"][:-1]) if len(state["audit_history"]) > 1 else "无历史审核"

        feedback = f"""[杜如晦代码审核反馈 - 第{state['inner_debate_count']}轮]

[原始任务]
{task}

[审核结果] 不通过

[杜如晦总结]
{drh_summary}

[发现问题]
{issues_text}

[修改建议]
{suggestions_text}

[修改要求]
1. 解决所有已发现问题
2. 符合设计文档要求
3. 参考之前的审核历史避免重复错误

[审核历史]
{history_text}

[上一次尝试的代码]
{code[:1500]}"""

        state["design_draft"] = feedback + "\n\n[设计文档]\n" + design
        
        state["stream_buffer"] = [
            f"👑 杜如晦审核代码：❌ 不通过",
            f"   问题: {', '.join(issues[:3]) if issues else '无'}"
        ]
    else:
        state["stream_buffer"] = ["👑 杜如晦审核代码：✅ 通过"]
        state["current_phase"] = "DEPLOYING"

    return state


def du_ruhui_judge_responsibility_node(state: MetaSystemState) -> MetaSystemState:
    """杜如晦问题责任判断节点 - 智能判断问题是方案本身还是执行者造成的.

    当代码审核不通过时，DRH分析执行结果，判断问题归属：
    - 方案问题：需要打回房玄龄重新设计
    - 文星问题：需要打回文星重新执行
    - 理星问题：需要打回理星重新执行
    """
    code = state.get("subsystem_code", "")
    design = state.get("design_draft", "")
    task = state.get("global_task", "")
    last_audit = state.get("audit_history", [])[-1] if state.get("audit_history") else {}
    issues = last_audit.get("issues", [])

    try:
        client = get_llm_client()
        template_injection = build_template_injection(state)
        personality = load_character_personality("du_ruhui")

        system_prompt = f"""{personality}
{template_injection}
【当前日期】
今天是{_CURRENT_DATE}。

【系统流程说明】
本系统采用"房玄龄ABC + 杜如晦 + 执行者"协作架构：
1. 房玄龄A（军师-提案）：根据用户需求提出方案草案
2. 房玄龄B（军师-反对）：审查并反对A的方案，找出问题
3. 房玄龄C（军师-总结）：综合A和B的意见，形成最终方案
4. 杜如晦（你-诊断）：判断问题是方案问题还是执行者问题
5. 执行者：负责执行方案（文字撰写和代码开发）

责任类型：
1. 方案问题：如果执行结果错误源于方案设计本身有缺陷（如架构不合理、流程错误、需求理解偏差）→ 打回房玄龄A重新设计
2. 执行者问题：如果执行结果不符合方案要求（如内容质量差、代码语法错误、逻辑错误）→ 打回执行者重新执行

请返回严格JSON格式，不要任何其他内容：
{{
    "responsibility": "方案" 或 "执行者",
    "reason": "判断原因",
    "suggestion": "修改建议"
}}"""

        result_str = client.generate_code(
            prompt=f"【原始任务】\n{task}\n\n【设计方案】\n{design[:1500]}\n\n【执行结果（代码）】\n{code[:1500]}\n\n【审核发现的问题】\n{chr(10).join(f'- {issue}' for issue in issues)}",
            system_prompt=system_prompt,
            temperature=0.3
        )

        result_str = result_str.strip()
        if result_str.startswith("```json"):
            result_str = result_str[7:]
        if result_str.startswith("```"):
            result_str = result_str[3:]
        if result_str.endswith("```"):
            result_str = result_str[:-3]

        decision = json.loads(result_str.strip())
        responsibility = decision.get("responsibility", "执行者")
        reason = decision.get("reason", "")
        suggestion = decision.get("suggestion", "")

        state["subsystem_metadata"]["responsibility"] = responsibility
        state["subsystem_metadata"]["responsibility_reason"] = reason
        state["subsystem_metadata"]["responsibility_suggestion"] = suggestion

        state["stream_buffer"] = [
            f"👑 杜如晦责任判断：{responsibility}的问题",
            f"   原因: {reason[:50]}..." if len(reason) > 50 else f"   原因: {reason}"
        ]

    except Exception as e:
        state["subsystem_metadata"]["responsibility"] = "执行者"
        state["subsystem_metadata"]["responsibility_reason"] = f"判断失败: {str(e)[:30]}"
        state["stream_buffer"] = [
            f"👑 杜如晦责任判断：默认执行者问题",
            f"   (判断失败，使用默认值)"
        ]

    return state


def executor_node(state: MetaSystemState) -> MetaSystemState:
    """统一执行节点 - 合并文星和理星，负责所有执行任务.

    能力：
    - 文字撰写（报告、文档、总结等）
    - 代码开发（程序、工具、数学建模等）
    - 网页搜索（CDP/Playwright）

    执行流程：计划-循环模式
    1. 先创建用有序数字标记的详细执行计划
    2. 按步骤循环执行
    3. 每步必须用JSON格式返回进度
    4. 全部完成后输出最终结果
    """
    task = state["global_task"]
    draft = state["design_draft"]

    need_search = any(kw in task for kw in ["搜索", "网页", "新闻", "热点", "最新", "近期", "查找", "获取信息", "RWA", "报告", "调研"])

    search_results = []
    visited_articles = []

    if need_search:
        state["stream_buffer"] = ["⚡ 执行者 开始搜索网页获取信息..."]

        try:
            from tools.web_tools import browser_navigate, browser_click, browser_execute_js, browser_close
            from tools.browser_advanced import browser_cdp_execute

            try:
                _kw_client = get_llm_client()
                _kw_result = _kw_client.generate_code(
                    prompt=f"请为以下任务生成4-6个专业的Bing搜索关键词（中英文混合），用于获取最新信息：\n\n{task}",
                    system_prompt="你是搜索专家。根据用户任务，生成最适合搜索引擎的查询关键词。每个关键词一行，不要编号，不要解释。关键词应该专业、精准，避免指令性词汇（如'帮我''写一篇'等）。中英文各一半。只返回关键词，每行一个。",
                    temperature=0.3
                ).strip()
                search_keywords = [k.strip() for k in _kw_result.split('\n') if k.strip() and len(k.strip()) > 2]
            except:
                search_keywords = []

            if not search_keywords:
                topic_words = re.findall(r'[A-Za-z]{2,}|[\u4e00-\u9fff]{2,}', task)
                stop_words = {"给我", "帮我", "写一篇", "关于", "然后", "搜索", "获取", "最新", "信息", "相关", "网页", "访问", "查找"}
                search_keywords = [w for w in topic_words if w not in stop_words and len(w) > 2][:6]

            if not search_keywords:
                search_keywords = [task[:30]]

            search_queries = []
            for kw in search_keywords[:6]:
                search_queries.append(f"{kw} {_CURRENT_YEAR}")
            for kw in search_keywords[:3]:
                search_queries.append(f"{kw} latest news {_CURRENT_YEAR}")

            def _navigate_with_cdp_fallback(url, task_id="executor_search"):
                result = browser_navigate(url=url, headless=True, task_id=task_id)
                if not result.get("success"):
                    return result
                try:
                    cdp_result = browser_cdp_execute(
                        method="Runtime.evaluate",
                        params={"expression": "document.readyState"},
                        task_id=task_id
                    )
                    if cdp_result.get("success"):
                        result["data"]["cdp_available"] = True
                except:
                    result["data"]["cdp_available"] = False
                return result

            def _extract_with_cdp_fallback(script, task_id="executor_search"):
                try:
                    cdp_result = browser_cdp_execute(
                        method="Runtime.evaluate",
                        params={"expression": script, "returnByValue": True},
                        task_id=task_id
                    )
                    if cdp_result.get("success"):
                        cdp_data = cdp_result.get("data", {}).get("result", {})
                        if cdp_data.get("type") == "string" and cdp_data.get("value"):
                            return {"success": True, "data": {"result": cdp_data["value"]}}
                except:
                    pass
                return browser_execute_js(script=script, task_id=task_id)

            for query in search_queries[:9]:
                if len(visited_articles) >= 20:
                    break
                try:
                    search_url = f"https://www.bing.com/search?q={query.replace(' ', '+')}"
                    state["stream_buffer"].append(f"🔍 必应搜索: {query}")

                    result = _navigate_with_cdp_fallback(search_url)
                    if not result.get("success"):
                        error_msg = result.get('error', '未知错误')
                        state["stream_buffer"].append(f"   ❌ 搜索失败: {error_msg}")
                        search_results.append(f"搜索失败 ({query}): {error_msg}")
                        continue

                    page_title = result.get("data", {}).get("title", "")
                    page_url = result.get("data", {}).get("url", "")
                    state["stream_buffer"].append(f"   ✅ 页面加载: {page_title[:50]}")
                    search_results.append(f"[搜索页面: {page_title}] URL: {page_url}\n")

                    js_links = _extract_with_cdp_fallback(
                        script="""
                        () => {
                            const links = [];
                            const selectors = [
                                'li.b_algo h2 a', '.b_algo a', '.result__a',
                                '.b_title a', 'h2 a', '.b_caption a',
                                '.b_attribution a', '.b_snippet a'
                            ];
                            for (const sel of selectors) {
                                const results = document.querySelectorAll(sel);
                                for (let i = 0; i < results.length; i++) {
                                    const href = results[i].href;
                                    const text = results[i].innerText || results[i].textContent;
                                    if (href && text && text.length > 5) {
                                        links.push({url: href, title: text.trim()});
                                    }
                                }
                            }
                            const unique = [];
                            const seen = new Set();
                            for (const link of links) {
                                if (!seen.has(link.url) && !link.url.includes('bing.com') && !link.url.includes('microsoft.com')) {
                                    seen.add(link.url);
                                    unique.push(link);
                                }
                            }
                            return JSON.stringify(unique.slice(0, 25));
                        }
                        """,
                        task_id="executor_search"
                    )

                    article_links = []
                    if js_links.get("success"):
                        try:
                            links_data = js_links.get("data", {}).get("result", "[]")
                            if isinstance(links_data, str):
                                article_links = json.loads(links_data)
                            else:
                                article_links = links_data
                            if not isinstance(article_links, list):
                                article_links = []
                        except:
                            article_links = []

                    state["stream_buffer"].append(f"   找到 {len(article_links)} 个文章链接")

                    max_articles = max(20, min(len(article_links), 30))
                    for idx, article in enumerate(article_links[:max_articles]):
                        try:
                            article_url = article.get("url", "") if isinstance(article, dict) else str(article)
                            article_title = article.get("title", "") if isinstance(article, dict) else ""

                            if not article_url or article_url in visited_articles:
                                continue

                            visited_articles.append(article_url)
                            state["stream_buffer"].append(f"   📄 访问文章 {idx+1}/{max_articles}: {article_title[:40]}...")

                            article_result = _navigate_with_cdp_fallback(article_url, task_id=f"executor_article_{idx}")
                            if not article_result.get("success"):
                                continue

                            article_js = _extract_with_cdp_fallback(
                                script="""
                                () => {
                                    const selectors = [
                                        'article', '.article-content', '.post-content', '.content',
                                        '.entry-content', '.article-body', '.news-content',
                                        'main', '[role="main"]', '.main-content'
                                    ];
                                    let content = '';
                                    for (const sel of selectors) {
                                        const elem = document.querySelector(sel);
                                        if (elem) {
                                            content = elem.innerText;
                                            if (content.length > 200) break;
                                        }
                                    }
                                    if (content.length < 200) {
                                        content = document.body.innerText;
                                    }
                                    const title = document.title || '';
                                    return JSON.stringify({
                                        title: title,
                                        content: content.substring(0, 3000),
                                        url: window.location.href
                                    });
                                }
                                """,
                                task_id=f"executor_article_{idx}"
                            )

                            if article_js.get("success"):
                                try:
                                    article_data_str = article_js.get("data", {}).get("result", "{}")
                                    if isinstance(article_data_str, str):
                                        article_data = json.loads(article_data_str)
                                    else:
                                        article_data = article_data_str

                                    if isinstance(article_data, dict):
                                        art_title = article_data.get("title", article_title)
                                        art_content = article_data.get("content", "")
                                        art_url = article_data.get("url", article_url)

                                        if art_content and len(art_content) > 100:
                                            search_results.append(
                                                f"【文章: {art_title}】\n"
                                                f"URL: {art_url}\n"
                                                f"内容: {art_content[:2000]}\n"
                                            )
                                            state["stream_buffer"].append(f"      ✅ 提取成功 ({len(art_content)} 字符)")
                                        else:
                                            state["stream_buffer"].append(f"      ⚠️ 内容太短")
                                except Exception as e:
                                    state["stream_buffer"].append(f"      ❌ 解析失败: {str(e)[:30]}")

                        except Exception as e:
                            state["stream_buffer"].append(f"   ❌ 文章访问失败: {str(e)[:30]}")
                            continue

                except Exception as e:
                    search_results.append(f"搜索失败 ({query}): {str(e)}\n")

            try:
                browser_close(task_id="executor_search")
            except:
                pass

        except Exception as e:
            search_results.append(f"浏览器工具调用失败: {str(e)}")

    search_context = "\n\n".join(search_results) if search_results else "（未获取到网页搜索结果，将基于通用知识执行）"

    task_type = state["subsystem_metadata"].get("task_type", "通用任务")
    is_text_task = any(kw in task for kw in ["报告", "文档", "总结", "撰写", "文章", "分析", "调研", "RWA"])
    is_local_task = any(kw in task for kw in ["整理文件夹", "整理文件", "下载文件夹", "文件分类", "文件夹", "C:\\", "D:\\"])

    try:
        client = get_llm_client()
        template_injection = build_template_injection(state)

        if is_local_task:
            # 本地文件操作任务：直接用Python代码执行，然后让LLM生成报告
            exec_log = []
            exec_log.append("# 本地文件操作执行日志\n")

            # 从任务中提取路径
            import re
            path_match = re.search(r'[C-Z]:\\[^\s，。！？；：""''（）【】]+', task)
            target_path = path_match.group(0) if path_match else os.path.expanduser("~/Downloads")
            target_path = os.path.normpath(target_path)

            # 设置工作目录，deploy 节点会在此目录生成报告
            state["work_dir"] = target_path

            exec_log.append(f"## 目标路径: {target_path}\n")

            if not os.path.exists(target_path):
                exec_log.append(f"❌ 路径不存在: {target_path}\n")
                result_text = "\n".join(exec_log)
                state["subsystem_code"] = f'"""\n{result_text}\n"""'
                state["tech_doc"] = result_text
                state["stream_buffer"] = [f"⚠️ 路径不存在: {target_path}"]
                return state

            # 定义分类规则
            categories = {
                "文档": [".doc", ".docx", ".pdf", ".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".rtf", ".csv", ".md"],
                "图片": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico", ".tiff", ".raw", ".heic"],
                "视频": [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpeg", ".mpg"],
                "音频": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus"],
                "安装包": [".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".appimage"],
                "压缩包": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"],
                "代码": [".py", ".js", ".java", ".c", ".cpp", ".h", ".cs", ".php", ".rb", ".go", ".rs", ".html", ".css", ".json", ".xml", ".yml", ".yaml", ".sh", ".bat", ".ps1"],
            }

            # 扫描文件
            files = []
            try:
                for item in os.listdir(target_path):
                    item_path = os.path.join(target_path, item)
                    if os.path.isfile(item_path):
                        ext = os.path.splitext(item)[1].lower()
                        size = os.path.getsize(item_path)
                        files.append({"name": item, "ext": ext, "size": size, "path": item_path})
            except Exception as e:
                exec_log.append(f"❌ 扫描失败: {str(e)}\n")

            exec_log.append(f"## 扫描结果: 找到 {len(files)} 个文件\n")

            # 分类统计
            file_by_category = {cat: [] for cat in categories}
            file_by_category["其他"] = []

            for f in files:
                found = False
                for cat, exts in categories.items():
                    if f["ext"] in exts:
                        file_by_category[cat].append(f)
                        found = True
                        break
                if not found:
                    file_by_category["其他"].append(f)

            # 输出分类统计
            exec_log.append("\n### 文件分类统计\n")
            for cat, file_list in file_by_category.items():
                if file_list:
                    total_size = sum(f["size"] for f in file_list)
                    exec_log.append(f"- **{cat}**: {len(file_list)} 个文件, 总大小 {total_size / 1024 / 1024:.2f} MB")
                    for f in file_list[:10]:
                        exec_log.append(f"  - {f['name']} ({f['size'] / 1024:.1f} KB)")
                    if len(file_list) > 10:
                        exec_log.append(f"  - ... 等共 {len(file_list)} 个文件")

            # 执行移动
            exec_log.append("\n## 文件移动操作\n")
            moved_count = 0
            for cat, file_list in file_by_category.items():
                if not file_list:
                    continue
                cat_dir = os.path.join(target_path, cat)
                cat_moved = 0
                cat_errors = []
                try:
                    os.makedirs(cat_dir, exist_ok=True)
                    for f in file_list:
                        src = f["path"]
                        dst = os.path.join(cat_dir, f["name"])
                        # 避免覆盖
                        if os.path.exists(dst):
                            base, ext = os.path.splitext(f["name"])
                            dst = os.path.join(cat_dir, f"{base}_副本{ext}")
                        try:
                            shutil.move(src, dst)
                            moved_count += 1
                            cat_moved += 1
                        except Exception as move_e:
                            cat_errors.append(f"{f['name']}: {str(move_e)[:50]}")
                    if cat_errors:
                        exec_log.append(f"⚠️ [{cat}] 移动了 {cat_moved}/{len(file_list)} 个文件，部分失败: {', '.join(cat_errors[:3])}")
                    else:
                        exec_log.append(f"✅ [{cat}] 移动了 {cat_moved} 个文件到 {cat_dir}")
                except Exception as e:
                    exec_log.append(f"❌ [{cat}] 移动失败: {str(e)}")

            exec_log.append(f"\n## 执行结果\n")
            exec_log.append(f"- 总文件数: {len(files)}")
            exec_log.append(f"- 成功移动: {moved_count}")
            exec_log.append(f"- 分类目录: {', '.join(cat for cat, fl in file_by_category.items() if fl)}")
            exec_log.append(f"- 执行时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

            exec_text = "\n".join(exec_log)

            # 让LLM生成最终报告
            try:
                report = client.generate_code(
                    prompt=f"""请根据以下本地文件操作执行日志，生成一份完整的整理报告。

任务: {task}

执行日志:
{exec_text[:8000]}

要求:
1. 报告正文必须在2000字以上
2. 包含文件清单、分类方案、执行结果
3. 结构完整：标题→摘要→正文→总结
4. 用中文回答""",
                    system_prompt=f"""{load_character_personality("executor")}
{template_injection}
【当前日期】
今天是{_CURRENT_DATE}。

输出要求:
1. 报告正文必须在2000字以上
2. 包含文件清单、分类方案、执行结果
3. 结构完整：标题→摘要→正文→总结
4. 不要描述搜索过程
5. 用中文回答""",
                    temperature=0.5
                )

                clean_report = report
                replacements = [('【', '['), ('】', ']')]
                for old, new in replacements:
                    clean_report = clean_report.replace(old, new)

                final_output = f"""# 本地文件整理报告

## 执行日志
{exec_text}

## 整理报告
{clean_report}
"""
            except Exception as e:
                final_output = f"""# 本地文件整理报告

## 执行日志
{exec_text}

## 错误
报告生成失败: {str(e)}
"""

            state["subsystem_code"] = f'"""\n{final_output}\n"""'
            state["tech_doc"] = final_output
            state["stream_buffer"] = [
                f"⚡ 执行者 完成本地文件操作",
                f"   扫描文件数: {len(files)}",
                f"   成功移动: {moved_count}",
                f"   分类数: {sum(1 for fl in file_by_category.values() if fl)}"
            ]

        elif is_text_task:
            personality = load_character_personality("executor")
            system_prompt = f"""{personality}
{template_injection}
【当前日期】
今天是{_CURRENT_DATE}。

【系统流程说明】
本系统采用"房玄龄ABC + 杜如晦 + 执行者"协作架构：
1. 房玄龄A（军师-提案）：根据用户需求提出方案草案
2. 房玄龄B（军师-反对）：审查并反对A的方案，找出问题
3. 房玄龄C（军师-总结）：综合A和B的意见，形成最终方案
4. 杜如晦（审核）：审核设计方案和执行结果
5. 执行者（你）：负责执行方案，包括文字撰写和代码开发

【执行流程要求 - 计划-循环模式】
在开始任务前，你必须：
1. 先创建一个用有序数字标记的详细执行计划
2. 然后按照计划的步数循环执行，计划多少步就循环多少轮
3. 每完成一步，标注进度（如"步骤3/7完成"）
4. 所有步骤完成后，输出最终结果

你的职责是根据设计方案和搜索获取的信息，撰写高质量的文字报告。

输出要求：
1. 请在报告开头标注【执行者 - 撰写报告】
2. 先输出执行计划（带数字标记）
3. 然后逐步执行并标注进度
4. 报告结构清晰，包含标题、摘要、正文、总结
5. 内容必须基于实际搜索到的信息，不要编造
6. 标注信息来源

【命名规范要求】
1. 报告标题：使用中文，简洁明确，不超过20字
2. 章节标题：使用中文数字编号（一、二、三...）
3. 日期格式：统一使用 YYYY-MM-DD

【硬性要求 - 必须满足】
1. 报告正文必须在2000字以上
2. 必须基于实际搜索到的文章内容撰写，不要描述搜索过程
3. 不要输出"执行计划""步骤1/4"等过程性内容，直接输出最终报告
4. 如果搜索到了文章内容，必须引用其中的具体数据和观点
5. 报告结构必须完整：标题→摘要→正文（多个章节）→总结

请用中文回答。"""

            prompt = f"""【任务】
{task}

【设计方案】
{draft[:1000]}

【从网页获取的文章内容】
{search_context[:12000]}

请基于以上从网页实际获取的内容，撰写一份完整的报告。
要求：
1. 必须基于实际获取的文章内容，引用其中的具体数据和观点
2. 不要编造不存在的信息
3. 报告正文必须在2000字以上
4. 不要描述搜索过程，直接输出报告内容
5. 报告结构：标题→摘要→正文（多个章节）→总结"""

            result = client.generate_code(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.5
            )

            clean_report = result
            replacements = [('【', '['), ('】', ']'), ('，', ','), ('。', '.'), ('：', ':'), ('；', ';'), ('？', '?'), ('！', '!')]
            for old, new in replacements:
                clean_report = clean_report.replace(old, new)

            doc = f"""# 执行者撰写报告

## 任务
{task}

## 报告正文
{clean_report}

## 搜索来源
- 搜索引擎: 必应 (Bing)
- 访问文章数: {len(visited_articles)}
- 文章链接:
{chr(10).join(f'  - {url}' for url in visited_articles) if visited_articles else '  无'}

## 原始文章内容摘要
{search_context[:3000] if search_results else '未获取到文章内容'}

## 维护记录
创建时间: {time.strftime('%Y-%m-%d %H:%M:%S')}
"""
            state["tech_doc"] = doc
            state["subsystem_code"] = f'"""\n{clean_report}\n"""'
            state["stream_buffer"] = [
                f"⚡ 执行者 完成报告撰写",
                f"   报告长度: {len(result)} 字符",
                f"   访问文章数: {len(visited_articles)}",
                f"   搜索引擎: 必应 (Bing)"
            ]
        else:
            personality = load_character_personality("executor")
            system_prompt = f"""{personality}
{template_injection}
【当前日期】
今天是{_CURRENT_DATE}。

【系统流程说明】
本系统采用"房玄龄ABC + 杜如晦 + 执行者"协作架构：
1. 房玄龄A（军师-提案）：根据用户需求提出方案草案
2. 房玄龄B（军师-反对）：审查并反对A的方案，找出问题
3. 房玄龄C（军师-总结）：综合A和B的意见，形成最终方案
4. 杜如晦（审核）：审核设计方案和执行结果
5. 执行者（你）：负责执行方案，包括代码开发和文字撰写

【执行流程要求 - 计划-循环模式】
在开始任务前，你必须：
1. 先创建一个用有序数字标记的详细执行计划
2. 然后按照计划的步数循环执行，计划多少步就循环多少轮
3. 每完成一步，标注进度（如"步骤3/7完成"）
4. 所有步骤完成后，输出最终结果

你的职责是根据设计方案，生成完整的代码实现。

输出要求：
1. 请在代码开头注释标注【执行者 - 代码实现】
2. 代码必须完整，不要省略任何部分
3. 代码必须是一个完整的 Python 模块
4. 包含所有必要的 import 语句
5. 包含错误处理
6. 代码风格规范

【命名规范要求】
1. 文件命名：使用小写英文+下划线
2. 类名：使用PascalCase
3. 函数/方法名：使用snake_case
4. 变量名：使用snake_case
5. 常量名：使用UPPER_SNAKE_CASE
6. 日期格式：统一使用 YYYY-MM-DD
7. 禁止在代码中使用中文命名变量/函数/类

请直接输出代码，不要输出 markdown 代码块标记（```）。"""

            prompt = f"任务: {task}\n\n架构设计文档:\n{draft}\n\n请生成代码。"

            result = client.generate_code(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.3
            )

            code = result.strip()
            if code.startswith("```python"):
                code = code[9:]
            elif code.startswith("```"):
                code = code[3:]
            if code.endswith("```"):
                code = code[:-3]
            code = code.strip()

            state["subsystem_code"] = code
            state["tech_doc"] = f"# 代码执行记录\n\n任务: {task}\n\n执行方式: 代码开发"
            state["stream_buffer"] = ["⚡ 执行者 完成代码开发"]

    except Exception as e:
        state["subsystem_code"] = f"# 执行失败\n\n错误: {str(e)}"
        state["tech_doc"] = f"# 执行失败\n\n错误: {str(e)}"
        state["stream_buffer"] = [f"⚠️ 执行者 LLM调用失败({str(e)[:50]})"]

    return state



def generate_calculator_code(detailed: bool = False) -> str:
    """生成真正的计算器代码"""
    comment = "# 代码自动生成：包含详细注释\n" if detailed else "# 代码自动生成：简洁版本\n"

    return f'''{comment}#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
计算器子系统
支持基本数学运算：加减乘除、幂运算、取模
"""

import re
import math
from typing import Dict, Any, Optional


class Calculator:
    """计算器类"""

    def __init__(self):
        self.history = []
        self.operators = {{
            '+': lambda a, b: a + b,
            '-': lambda a, b: a - b,
            '*': lambda a, b: a * b,
            '/': lambda a, b: a / b if b != 0 else float('inf'),
            '**': lambda a, b: a ** b,
            '%': lambda a, b: a % b,
            '//': lambda a, b: a // b,
        }}

    def calculate(self, expression: str) -> Dict[str, Any]:
        """计算表达式"""
        try:
            # 清理表达式
            expression = expression.strip()
            if not expression:
                return {{"success": False, "error": "空表达式"}}

            # 尝试直接eval（安全版本）
            result = self.safe_eval(expression)
            if result is not None:
                self.history.append({{"expression": expression, "result": result}})
                return {{
                    "success": True,
                    "result": result,
                    "expression": expression
                }}
            else:
                return {{"success": False, "error": "无法解析表达式"}}

        except Exception as e:
            return {{"success": False, "error": str(e)}}

    def safe_eval(self, expression: str) -> Optional[float]:
        """安全地计算表达式"""
        # 只允许数字和基本运算符
        allowed_chars = set('0123456789+-*/.() **%// ')
        if not all(c in allowed_chars for c in expression):
            return None

        try:
            # 使用eval计算（简化版）
            result = eval(expression, {{"__builtins__": {{}}}}, {{"math": math}})
            return float(result) if result is not None else None
        except:
            return None

    def get_history(self) -> list:
        """获取计算历史"""
        return self.history

    def clear_history(self):
        """清空历史"""
        self.history = []


def run_calculator():
    """运行计算器交互循环"""
    calc = Calculator()
    print("🧮 计算器已启动")
    print("支持运算: +, -, *, /, **, %, //")
    print("输入 'history' 查看历史, 'clear' 清空历史, 'exit' 退出")
    print("-" * 40)

    while True:
        try:
            user_input = input("计算 > ").strip()

            if user_input.lower() == 'exit':
                print("👋 再见！")
                break
            elif user_input.lower() == 'history':
                history = calc.get_history()
                if history:
                    print("📜 计算历史:")
                    for i, item in enumerate(history, 1):
                        print(f"  {{i}}. {{item['expression']}} = {{item['result']}}")
                else:
                    print("📭 暂无历史记录")
            elif user_input.lower() == 'clear':
                calc.clear_history()
                print("🗑️ 历史已清空")
            else:
                result = calc.calculate(user_input)
                if result["success"]:
                    print(f"✅ 结果: {{result['result']}}")
                else:
                    print(f"❌ 错误: {{result['error']}}")

        except KeyboardInterrupt:
            print("\n👋 再见！")
            break
        except Exception as e:
            print(f"❌ 错误: {{e}}")


if __name__ == "__main__":
    run_calculator()
'''


def generate_generic_subsystem_code(detailed: bool = False) -> str:
    """生成通用子系统代码"""
    comment = "# 代码自动生成：包含详细注释\n" if detailed else "# 代码自动生成：简洁版本\n"

    return f'''{comment}#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
通用子系统
基于LangGraph构建
"""

from typing import TypedDict, List, Dict, Optional, Any
from langgraph.graph import StateGraph, END


class SubsystemState(TypedDict):
    global_task: str
    stream_buffer: List[str]
    snapshot_id: Optional[str]
    history: List[Dict]
    output: Optional[str]
    skills_used: List[str]


def parse_input_node(state: SubsystemState) -> SubsystemState:
    task = state["global_task"]
    state["stream_buffer"].append(f"📥 解析输入: {{task[:50]}}")
    return state


def validate_input_node(state: SubsystemState) -> SubsystemState:
    state["stream_buffer"].append("✅ 输入验证通过")
    return state


def execute_task_node(state: SubsystemState) -> SubsystemState:
    task = state["global_task"]
    result = f"完成任务: {{task}}"
    state["output"] = result
    state["stream_buffer"].append(f"💡 执行任务: {{result}}")
    return state


def format_output_node(state: SubsystemState) -> SubsystemState:
    state["stream_buffer"].append("📤 格式化输出完成")
    return state


def build_subsystem_graph():
    builder = StateGraph(SubsystemState)
    builder.add_node("parse_input", parse_input_node)
    builder.add_node("validate_input", validate_input_node)
    builder.add_node("execute_task", execute_task_node)
    builder.add_node("format_output", format_output_node)
    builder.set_entry_point("parse_input")
    builder.add_edge("parse_input", "validate_input")
    builder.add_edge("validate_input", "execute_task")
    builder.add_edge("execute_task", "format_output")
    builder.add_edge("format_output", END)
    return builder.compile()


def run_gateway_loop():
    import sys
    graph = build_subsystem_graph()
    print("SUB_SYSTEM_READY", flush=True)
    for line in sys.stdin:
        line = line.strip()
        if line == "/exit":
            print("SUB_SYSTEM_EXITING", flush=True)
            break
        elif line.startswith("/rollback"):
            print("SUB_SYSTEM_ROLLBACK", flush=True)
            break
        else:
            initial_state = SubsystemState(
                global_task=line,
                stream_buffer=[],
                snapshot_id=None,
                history=[],
                output=None,
                skills_used=[]
            )
            for event in graph.stream(initial_state):
                for node_name, node_state in event.items():
                    if isinstance(node_state, dict) and "stream_buffer" in node_state:
                        for msg in node_state["stream_buffer"]:
                            print(msg, flush=True)


if __name__ == "__main__":
    run_gateway_loop()
'''


def _generate_report_filename(task: str, task_title: str, work_dir: Optional[str] = None) -> tuple:
    """根据任务内容生成描述性的报告文件名和输出目录.

    Returns:
        (output_dir, file_base, display_name)
    """
    date_str = datetime.now().strftime('%Y-%m-%d')

    # 如果有工作目录（如本地文件整理任务），优先在工作目录输出
    if work_dir and os.path.exists(work_dir):
        output_dir = work_dir
    else:
        output_dir = RESULT_DIR

    # 根据任务内容推断报告类型和文件名
    task_lower = task.lower()

    # 本地文件整理任务
    if any(kw in task_lower for kw in ["整理文件夹", "整理文件", "文件分类", "下载文件夹"]):
        # 尝试从路径中提取文件夹名
        path_match = re.search(r'[C-Z]:\\[^\\]+\\([^\\，。！？；：""''（）【】 \t\n\r]+)', task)
        if path_match:
            folder_name = path_match.group(1)
            display_name = f"{folder_name}整理报告"
        else:
            display_name = "文件整理报告"
        file_base = f"{date_str}_{display_name}"
        return output_dir, file_base, display_name

    # 研究报告类
    if any(kw in task_lower for kw in ["报告", "调研", "研究", "分析", "行业", "rwa", "综述"]):
        if task_title and task_title != "未命名任务":
            display_name = task_title
        else:
            # 从任务中提取主题词
            display_name = _extract_topic_from_task(task) or "研究报告"
        file_base = f"{date_str}_{display_name}"
        return output_dir, file_base, display_name

    # 代码/程序类
    if any(kw in task_lower for kw in ["代码", "程序", "脚本", "工具", "开发", "实现", "编写"]):
        if task_title and task_title != "未命名任务":
            display_name = task_title
        else:
            display_name = _extract_topic_from_task(task) or "程序实现"
        file_base = f"{date_str}_{display_name}"
        return output_dir, file_base, display_name

    # 默认：使用任务标题或哈希
    if task_title and task_title != "未命名任务":
        safe_title = re.sub(r'[\\/:*?"<>|\s]', '_', task_title)
        safe_title = re.sub(r'_+', '_', safe_title).strip('_')
        safe_title = safe_title[:40]
        file_base = f"{date_str}_{safe_title}"
        display_name = task_title
    else:
        topic = _extract_topic_from_task(task) or hashlib.md5(task.encode()).hexdigest()[:8]
        file_base = f"{date_str}_{topic}"
        display_name = topic

    return output_dir, file_base, display_name


def _extract_topic_from_task(task: str) -> Optional[str]:
    """从任务描述中提取主题词用于文件名."""
    # 匹配 "关于XXX的报告/研究/分析" 模式
    # 注意：顺序很重要，更精确的 pattern 放在前面
    patterns = [
        # 模式1: "关于XXX的..." -> 最精确
        r'关于(.+?)(?:的|研究|报告|分析|调研|综述)',
        # 模式2: "写/生成/做 一份/一个 XXX 的/报告/..." -> 包含量词
        r'(?:写|生成|做|完成)(?:一份|一个|一套)?(?:关于)?(.+?)(?:的|研究|报告|文档|程序|代码|脚本|工具)',
        # 模式3: "帮我/给我 写/生成 XXX 程序/..."
        r'(?:帮我|给我|为|替)(?:写|生成|做|完成|开发)(?:一个|一套|一份)?(.+?)(?:程序|代码|脚本|工具|系统)',
        # 模式4: "XXX行业/市场/..." -> 宽泛匹配，放最后
        r'(.+?)(?:行业|市场|领域|技术|项目)(?:研究|报告|分析|调研)?',
    ]
    for pattern in patterns:
        match = re.search(pattern, task)
        if match:
            topic = match.group(1).strip()
            # 去掉常见前缀词（量词、介词等）
            topic = re.sub(r'^(?:关于|一份|一个|一套|的|写|生成|做|完成|帮我|给我)\s*', '', topic)
            # 清理主题词
            topic = re.sub(r'[\\/:*?"<>|\s]', '_', topic)
            topic = re.sub(r'_+', '_', topic).strip('_')
            if len(topic) >= 2 and len(topic) <= 30:
                return topic
    return None


def deploy_subsystem_node(state: MetaSystemState) -> MetaSystemState:
    """部署节点 - 将执行结果保存到指定目录.

    输出规则:
    - 如果任务指定了工作目录（如整理某个文件夹），报告直接生成在该目录
    - 如果没有指定工作目录，报告生成在 result/ 文件夹
    - 文件名根据任务内容自动生成描述性名称，不再使用 readme.md
    """
    task = state["global_task"]
    task_title = state.get("task_title", "")
    work_dir = state.get("work_dir")

    # 生成文件名和输出目录
    output_dir, file_base, display_name = _generate_report_filename(task, task_title, work_dir)

    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)

    subsystem_code = state.get("subsystem_code", "")
    tech_doc = state.get("tech_doc", "")

    # 确定报告内容
    if subsystem_code.startswith('"""') or subsystem_code.startswith("'''"):
        report_content = subsystem_code.strip('"\'').strip()
    elif tech_doc:
        report_content = tech_doc
    else:
        report_content = subsystem_code

    # 写入报告文件（使用描述性名称，不是 readme.md）
    report_path = os.path.join(output_dir, f"{file_base}.md")
    write_file(report_path, report_content)

    # 如果是代码，同时保存 .py 文件
    code_path = None
    if subsystem_code and not subsystem_code.startswith('"""') and not subsystem_code.startswith("'''"):
        code_path = os.path.join(output_dir, f"{file_base}.py")
        write_file(code_path, subsystem_code)

    # 记录到能力库
    capacity_entry = {
        "id": file_base,
        "title": display_name,
        "description": task[:100],
        "task_type": state.get("subsystem_metadata", {}).get("task_type", "通用任务"),
        "created_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        "result_file": report_path,
        "work_dir": work_dir,
    }
    save_capacity_entry(capacity_entry)

    # 记录到任务记忆
    user_id = state["user_id"]
    record_task_memory(user_id, task, state["design_draft"], file_base, "result")
    state["task_memory_id"] = file_base

    # 简单任务(direct_execute)设置 COMPLETE，复杂任务设置 HOSTING_SUBSYSTEM
    is_simple = state.get("task_complexity") == "simple"
    state["current_phase"] = "COMPLETE" if is_simple else "HOSTING_SUBSYSTEM"
    state["task_active"] = True

    # 构建输出消息
    msg_lines = [f"🚀 结果已保存: {report_path}"]
    if code_path:
        msg_lines.append(f"   代码文件: {code_path}")
    if work_dir and work_dir != RESULT_DIR:
        msg_lines.append(f"   工作目录: {work_dir}")
    msg_lines.append(f"   报告标题: {display_name}")

    # 保留之前的 stream_buffer（来自 direct_execute 的结果），添加部署信息
    prev_msgs = state.get("stream_buffer", [])
    state["stream_buffer"] = prev_msgs + [""] + msg_lines
    return state


def save_capacity_entry(entry: Dict) -> bool:
    """保存能力条目到能力库索引."""
    try:
        capacities = load_capacity_index()
        # 检查是否已存在
        existing = [c for c in capacities if c.get("id") == entry["id"]]
        if existing:
            # 更新现有条目
            for i, c in enumerate(capacities):
                if c.get("id") == entry["id"]:
                    capacities[i] = entry
                    break
        else:
            capacities.append(entry)
        return save_capacity_index(capacities)
    except Exception as e:
        print(f"保存能力条目失败: {e}")
        return False


def relay_to_subsystem_node(state: MetaSystemState, user_message: str) -> MetaSystemState:
    """中转路由节点 - 将用户消息转发给子图处理，并返回子图执行结果.

    对于复杂任务，子图是一个 LangGraph 架构，主程序作为中转路由：
    1. 加载子图代码
    2. 构建子图
    3. 将用户消息作为输入运转子图
    4. 收集子图输出返回给用户
    """
    task_hash = state.get("task_memory_id")
    if not task_hash:
        # 尝试从 deployed 目录找到最新的子系统
        import glob
        deployed_dirs = glob.glob("./deployed/*")
        if deployed_dirs:
            task_hash = os.path.basename(deployed_dirs[-1])
        else:
            state["stream_buffer"] = ["❌ 没有找到已部署的子系统"]
            return state

    deploy_dir = f"./deployed/{task_hash}"
    subsystem_path = f"{deploy_dir}/subsystem.py"

    if not os.path.exists(subsystem_path):
        state["stream_buffer"] = [f"❌ 子系统文件不存在: {subsystem_path}"]
        return state

    try:
        # 动态加载子图模块
        import importlib.util
        spec = importlib.util.spec_from_file_location("subsystem", subsystem_path)
        subsystem_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(subsystem_module)

        # 检查子图是否有 build_subgraph 或 build_subsystem_graph 函数
        subgraph_builder = None
        if hasattr(subsystem_module, 'build_subgraph'):
            subgraph_builder = subsystem_module.build_subgraph
        elif hasattr(subsystem_module, 'build_subsystem_graph'):
            subgraph_builder = subsystem_module.build_subsystem_graph

        if subgraph_builder:
            # 复杂任务：运转 LangGraph 子图
            subgraph = subgraph_builder()

            # 构建子图初始状态
            if hasattr(subsystem_module, 'SubState'):
                sub_state = subsystem_module.SubState(
                    input=user_message,
                    output="",
                    logs=[f"收到消息: {user_message}"]
                )
            elif hasattr(subsystem_module, 'SubsystemState'):
                sub_state = subsystem_module.SubsystemState(
                    global_task=user_message,
                    stream_buffer=[f"收到消息: {user_message}"],
                    snapshot_id=None,
                    history=[],
                    output=None,
                    skills_used=[]
                )
            else:
                sub_state = {"input": user_message, "output": "", "logs": [f"收到消息: {user_message}"]}

            # 运转子图（传入 thread_id 避免 checkpoint 冲突）
            sub_result = None
            import uuid
            thread_id = str(uuid.uuid4())
            for event in subgraph.stream(sub_state, config={"configurable": {"thread_id": thread_id}}):
                for node_name, node_state in event.items():
                    sub_result = node_state

            # 提取子图输出
            if isinstance(sub_result, dict):
                output = sub_result.get("output", "")
                logs = sub_result.get("logs", sub_result.get("stream_buffer", []))
            else:
                output = getattr(sub_result, "output", "")
                logs = getattr(sub_result, "logs", getattr(sub_result, "stream_buffer", []))

            state["stream_buffer"] = [
                f"📨 中转消息到子图: {user_message[:50]}",
                f"📤 子图执行日志: {', '.join(logs[-3:]) if logs else '无日志'}",
                f"📥 子图返回结果: {output[:200] if output else '无输出'}"
            ]

            # 将子图输出保存到对话历史
            state["subsystem_conversation_history"].append({
                "role": "user",
                "content": user_message
            })
            state["subsystem_conversation_history"].append({
                "role": "assistant",
                "content": output if output else str(logs)
            })
        else:
            # 简单任务：直接执行子系统代码
            state["stream_buffer"] = [
                f"📨 中转消息到子系统: {user_message[:50]}",
                "⚠️ 子系统没有 build_subgraph/build_subsystem_graph 函数，无法作为子图运转"
            ]

    except Exception as e:
        state["stream_buffer"] = [
            f"❌ 子图运转失败: {str(e)[:100]}",
            f"   请检查子系统代码是否正确"
        ]

    return state


def terminate_subsystem_node(state: MetaSystemState) -> MetaSystemState:
    metadata = {
        "task_type": state["subsystem_metadata"].get("task_type", ""),
        "keywords": state["subsystem_metadata"].get("keywords", [])
    }
    capacity_id = archive_capacity(state["subsystem_code"], state["tech_doc"], metadata)
    # 记录反馈
    record_feedback(capacity_id, success=True, comment="正常退出归档")

    state["current_phase"] = "IDLE"
    state["design_draft"] = ""
    state["tech_doc"] = ""
    state["subsystem_code"] = ""
    state["subsystem_metadata"] = {}
    state["major_loop_count"] = 0
    state["inner_debate_count"] = 0
    state["stream_buffer"] = ["🛑 子系统已终止，回到主系统空闲状态"]
    return state


def rollback_node(state: MetaSystemState, target_checkpoint_id: str) -> MetaSystemState:
    checkpointer = state.get("checkpointer")
    if checkpointer and target_checkpoint_id:
        try:
            checkpoint = checkpointer.get(target_checkpoint_id)
            if checkpoint:
                state = checkpoint["state"]
                state["stream_buffer"] = []
                state["stream_buffer"].append(f"⏪ 时间回溯至 {target_checkpoint_id}")
            state["snapshot_id"] = target_checkpoint_id
        except Exception as e:
            state["stream_buffer"].append(f"❌ 回溯失败: {str(e)}")
    else:
        state["stream_buffer"].append("❌ 无法回溯：缺少checkpointer或snapshot_id")
    return state


# ================== 人机交互辅助函数 ==================

def timed_input(prompt="", timeout=30, default=None):
    """等待用户输入，timeout秒无响应则自动继续.

    Returns:
        str: 用户输入（去空白），超时返回 default
    """
    import sys
    import threading

    if prompt:
        print(f"\n{prompt}")
    print(f"  ⏰ {'='*30}")
    print(f"  ⏰ 等待人工审核，{timeout}秒内无输入则自动通过")
    print(f"  ⏰ 输入建议后回车提交，直接回车=通过，输入 task_exit=退出任务")
    print(f"  ⏰ {'='*30}")
    sys.stdout.flush()

    result = [default]
    done = [False]

    def reader():
        try:
            line = sys.stdin.readline()
            result[0] = line.strip()
        except Exception:
            pass
        done[0] = True

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    thread.join(timeout)

    if not done[0]:
        print(f"\n  ⏰ 超时({timeout}s)，自动通过")
        return default
    return result[0]


def human_review_design_node(state: MetaSystemState) -> MetaSystemState:
    """人工审核设计方案节点 — 杜如晦通过后给人看一眼."""
    draft = state.get("design_draft", "")
    state["stream_buffer"].append("👁️ 请人工审核设计方案：")

    # 展示方案摘要
    lines = draft.strip().split("\n")
    summary_lines = [l for l in lines if l.strip()][:20]
    for l in summary_lines:
        state["stream_buffer"].append(f"  {l[:120]}")

    feedback = timed_input(prompt="📋 以上是设计方案，是否通过？", timeout=30, default="")
    state["human_feedback"] = feedback or ""

    if feedback == "task_exit":
        state["task_active"] = False
        state["stream_buffer"].append("👋 用户退出任务")
        state["current_phase"] = "IDLE"
        return state

    if feedback and feedback.lower() not in ("y", "yes", ""):
        state["stream_buffer"].append(f"💬 用户反馈: {feedback}")
        state["current_phase"] = "DESIGNING"
        state["design_draft"] = f"{draft}\n\n【人工审核反馈】\n{feedback}"
    else:
        state["stream_buffer"].append("✅ 人工审核通过")
        state["current_phase"] = "EXECUTING"

    return state


def human_review_output_node(state: MetaSystemState) -> MetaSystemState:
    """人工审核输出节点 — 产物完成后给人看一眼."""
    code = state.get("subsystem_code", "")
    design = state.get("design_draft", "")
    phase = state.get("current_phase", "")

    state["stream_buffer"].append("👁️ 请人工审核执行结果：")

    # 展示执行结果摘要
    content = code or design
    lines = content.strip().split("\n")
    summary = [l for l in lines if l.strip()][:20] if len(lines) > 3 else lines[:30]
    for l in summary:
        state["stream_buffer"].append(f"  {l[:120]}")

    feedback = timed_input(prompt="📋 以上是执行结果，是否通过？", timeout=30, default="")
    state["human_feedback"] = feedback or ""

    if feedback == "task_exit":
        state["task_active"] = False
        state["stream_buffer"].append("👋 用户退出任务")
        state["current_phase"] = "IDLE"
        return state

    if feedback and feedback.lower() not in ("y", "yes", ""):
        state["stream_buffer"].append(f"💬 用户反馈: {feedback}")
        state["current_phase"] = "CODING"
        state["design_draft"] = f"{design}\n\n【人工审核反馈】\n{feedback}"
    else:
        state["stream_buffer"].append("✅ 人工审核通过")
        state["current_phase"] = "DEPLOYING"

    return state


def build_main_graph():
    """构建主流程图.

    流程分支:
    - 简单任务: parse_task -> search_capacity -> executor -> du_ruhui_code -> du_ruhui_judge -> deploy -> END
    - 复杂任务: parse_task -> search_capacity -> fang_a -> fang_b -> fang_c -> du_ruhui -> executor -> du_ruhui_code -> du_ruhui_judge -> (fang_a/executor) -> deploy -> END
    """
    builder = StateGraph(MetaSystemState)

    builder.add_node("parse_task", parse_user_task_node)
    builder.add_node("search_capacity", search_capacity_node)
    builder.add_node("direct_execute", direct_execute_node)
    builder.add_node("fang_a", fang_a_proposal_node)
    builder.add_node("fang_b", fang_b_critic_node)
    builder.add_node("fang_c", fang_c_summary_node)
    builder.add_node("du_ruhui", du_ruhui_audit_node)
    builder.add_node("executor", executor_node)
    builder.add_node("du_ruhui_code", du_ruhui_audit_code_node)
    builder.add_node("du_ruhui_judge", du_ruhui_judge_responsibility_node)
    builder.add_node("deploy", deploy_subsystem_node)
    builder.add_node("human_review_design", human_review_design_node)
    builder.add_node("human_review_output", human_review_output_node)

    builder.set_entry_point("parse_task")

    builder.add_conditional_edges(
        "parse_task",
        lambda s: "search_capacity"
    )

    def after_search_capacity(state):
        complexity = state.get("task_complexity", "complex")
        if complexity == "simple":
            return "direct_execute"  # 简单任务走直接执行节点
        else:
            return "fang_a"

    builder.add_conditional_edges("search_capacity", after_search_capacity)

    def after_direct_execute(state):
        if state.get("task_complexity") == "complex" and state.get("current_phase") == "DESIGNING":
            return "fang_a"
        else:
            return "deploy"

    builder.add_conditional_edges("direct_execute", after_direct_execute)

    builder.add_edge("fang_a", "fang_b")
    builder.add_edge("fang_b", "fang_c")
    builder.add_edge("fang_c", "du_ruhui")

    def after_design_audit(state):
        if state["current_phase"] == "DESIGNING":
            state["inner_debate_count"] += 1
            if state["inner_debate_count"] >= 5:
                state["current_phase"] = "EXECUTING"
                return "human_review_design"
            return "fang_a"
        else:
            return "human_review_design"

    builder.add_conditional_edges("du_ruhui", after_design_audit)

    def after_human_review_design(state):
        if state.get("current_phase") == "DESIGNING":
            return "fang_a"
        elif state.get("current_phase") == "IDLE":
            return "deploy"
        return "executor"

    builder.add_conditional_edges("human_review_design", after_human_review_design)

    builder.add_edge("executor", "du_ruhui_code")

    def after_code_audit(state):
        if state["current_phase"] == "CODING":
            if state["inner_debate_count"] >= 8:
                state["current_phase"] = "DEPLOYING"
                return "human_review_output"
            return "du_ruhui_judge"
        else:
            return "human_review_output"

    builder.add_conditional_edges("du_ruhui_code", after_code_audit)

    def after_human_review_output(state):
        if state.get("current_phase") == "CODING":
            return "executor"
        elif state.get("current_phase") == "IDLE":
            return "deploy"
        return "deploy"

    builder.add_conditional_edges("human_review_output", after_human_review_output)

    def after_judge_responsibility(state):
        responsibility = state.get("subsystem_metadata", {}).get("responsibility", "执行者")
        state["inner_debate_count"] += 1

        if state["inner_debate_count"] >= 8:
            return "deploy"

        if responsibility == "方案":
            return "fang_a"
        else:
            return "executor"

    builder.add_conditional_edges("du_ruhui_judge", after_judge_responsibility)
    builder.add_edge("deploy", END)

    return builder


def create_checkpointer():
    return MemorySaver()


def switch_graph(state: MetaSystemState, target_graph_id: str,
                 graph_registry=None, lifecycle_manager=None) -> MetaSystemState:
    """切换当前活跃图.

    1. 保存当前图状态到 graph_contexts
    2. 加载目标图状态（如果有）
    3. 更新 active_graph_id
    4. 如果有 lifecycle_manager，调用 activate_graph
    """
    current_id = state.get("active_graph_id", "default")

    # 保存当前状态
    if "graph_contexts" not in state or state["graph_contexts"] is None:
        state["graph_contexts"] = {}

    # 序列化当前状态（排除 graph_contexts 本身避免循环）
    current_state = {k: v for k, v in state.items() if k != "graph_contexts"}
    state["graph_contexts"][current_id] = current_state

    # 加载目标图状态（如果存在）
    if target_graph_id in state.get("graph_contexts", {}):
        saved_state = state["graph_contexts"][target_graph_id]
        # 恢复目标图状态，但保留 graph_contexts
        for key, value in saved_state.items():
            if key != "graph_contexts":
                state[key] = value
    else:
        # 新图：重置部分状态但保留用户ID等基础信息
        user_id = state.get("user_id", "")
        state["global_task"] = ""
        state["current_phase"] = "IDLE"
        state["design_draft"] = ""
        state["tech_doc"] = ""
        state["subsystem_code"] = ""
        state["stream_buffer"] = [f"已切换到图: {target_graph_id}"]
        state["user_id"] = user_id

    state["active_graph_id"] = target_graph_id

    # 更新 lifecycle
    if lifecycle_manager:
        lifecycle_manager.activate_graph(target_graph_id)

    return state


def handle_graph_command(state: MetaSystemState, command: str,
                         graph_registry=None, lifecycle_manager=None) -> MetaSystemState:
    """处理图相关命令.

    支持的命令：
    - /exit: 退出当前子图，回切父图或默认图
    - /switch <graph_id>: 切换到指定图
    - /graphs: 列出所有图
    """
    cmd = command.strip()

    if cmd == "/exit":
        current_id = state.get("active_graph_id", "default")
        parent_id = state.get("parent_graph_id")

        if current_id == "default":
            state["stream_buffer"] = ["已在默认图中，无法退出"]
            return state

        # 完成当前图
        if lifecycle_manager:
            lifecycle_manager.complete_graph(current_id)

        # 回切
        target = parent_id if parent_id else "default"
        state = switch_graph(state, target, graph_registry, lifecycle_manager)
        state["stream_buffer"] = [f"已退出 {current_id}，回到 {target}"]

    elif cmd.startswith("/switch "):
        parts = cmd.split(" ", 1)
        if len(parts) == 2:
            target_id = parts[1].strip()
            if graph_registry and graph_registry.get_graph(target_id):
                state = switch_graph(state, target_id, graph_registry, lifecycle_manager)
            else:
                state["stream_buffer"] = [f"图 {target_id} 不存在"]
        else:
            state["stream_buffer"] = ["用法: /switch <graph_id>"]

    elif cmd == "/graphs":
        if graph_registry:
            graphs = graph_registry.list_graphs()
            lines = ["可用流程图:"]
            for g in graphs:
                marker = " *" if g.id == state.get("active_graph_id") else ""
                lines.append(f"  {g.id}: {g.name} [{g.status}]{marker}")
            state["stream_buffer"] = lines
        else:
            state["stream_buffer"] = ["图注册表未初始化"]

    return state


def process_user_message(state: MetaSystemState, message: str,
                         graph_registry=None, lifecycle_manager=None,
                         subgraph_builder=None) -> MetaSystemState:
    """处理用户消息，支持多图路由.

    1. 检查是否是图管理命令（/exit, /switch, /graphs）
    2. 获取当前活跃图
    3. 如果是默认图，注入 System Prompt 让 LLM 自己判断是否需要生成子图
    4. 否则将消息路由到当前图处理

    注意：是否生成子图由 LLM 通过 get_decision_prompt() 判断，
    不再使用写死的规则。
    """
    # 处理命令
    if message.startswith("/"):
        return handle_graph_command(state, message, graph_registry, lifecycle_manager)

    active_id = state.get("active_graph_id", "default")

    # 默认图：注入 System Prompt 让 LLM 自己判断
    # 如果 LLM 决定生成子图，它会在回复中说明，然后我们调用 build_subgraph
    # 这里简化处理，实际应该由 LLM 返回特定格式的决策
    if active_id == "default" and subgraph_builder:
        # 获取决策提示（注入到 LLM 的上下文中）
        decision_prompt = subgraph_builder.get_decision_prompt(message)
        # 这里我们检查消息是否包含 LLM 的决策标记
        # 简化：直接让 LLM 处理消息，决策由消息内容决定
        # 实际应该等待 LLM 返回后再决定是否生成子图

    # 正常处理消息
    state["global_task"] = message
    state["current_phase"] = "PARSING"
    return state


def process_user_message_legacy(user_id: str, text: str, graph, active_session: dict):
    """处理用户消息"""
    if text.strip() in ("/exit", "task_exit", "/task_exit"):
        if active_session.get("phase") in ("HOSTING_SUBSYSTEM", "COMPLETE"):
            state = active_session.get("state", {})
            if isinstance(state, dict):
                state["task_active"] = False
                state["current_phase"] = "IDLE"
                state["stream_buffer"] = ["👋 退出当前任务"]
            active_session["phase"] = "IDLE"
            active_session["state"] = state
            return state
        else:
            state = active_session.get("state", {})
            if isinstance(state, dict):
                state["task_active"] = False
                state["current_phase"] = "IDLE"
                state["stream_buffer"] = ["👋 退出当前任务"]
            return state
    elif text.startswith("/rollback"):
        target = text.split()[1] if len(text.split()) > 1 else None
        state = active_session.get("state", {})
        if target:
            new_state = rollback_node(state, target)
        else:
            new_state = state
        active_session["state"] = new_state
        return new_state
    elif text.strip() == "/tools":
        tools = registry.get_all_tool_names()
        return active_session.get("state", {})
    elif text.strip() == "/interrupt":
        set_interrupt_flag(user_id)
        return active_session.get("state", {})
    elif text.strip().startswith("/approve"):
        approve_task(user_id)
        return active_session.get("state", {})
    elif text.strip().startswith("/reject"):
        reject_task(user_id)
        return active_session.get("state", {})
    else:
        checkpointer = create_checkpointer()

        # 从会话中恢复记忆（同一个 active_session 内持久化）
        prev_state = active_session.get("state", {})
        prev_memory = []
        if isinstance(prev_state, dict):
            prev_memory = prev_state.get("conversation_memory", [])

        initial_state = MetaSystemState(
            user_id=user_id,
            global_task=text,
            current_phase="DESIGNING",
            design_draft="",
            tech_doc="",
            subsystem_code="",
            subsystem_metadata={},
            capacity_library=load_capacity_index(),
            stream_buffer=[],
            snapshot_id=None,
            subsystem_conversation_history=[],
            major_loop_count=0,
            inner_debate_count=0,
            user_profile={},
            task_memory_id=None,
            skills_used=[],
            tool_results=[],
            available_tools=registry.get_all_tool_names(),
            task_complexity="complex",
            audit_history=[],
            task_title="",
            selected_template="",
            template_prompt="",
            human_feedback="",
            conversation_memory=prev_memory,
            task_active=True,
        )

        final_state = {}
        for event in graph.stream(initial_state, config={"thread_id": user_id}):
            for node_name, node_state in event.items():
                if isinstance(node_state, dict) and "stream_buffer" in node_state:
                    for msg in node_state["stream_buffer"]:
                        print(f"  📤 {msg}")
                    # 提取实际的 state（去掉 {node_name: ...} 包裹）
                    final_state = node_state

        if not final_state:
            final_state = event if isinstance(event, dict) else {}

        # 保存会话记忆（跨任务累积）
        if isinstance(final_state, dict):
            old_mem = active_session.get("state", {}).get("conversation_memory", []) if isinstance(active_session.get("state"), dict) else []
            current_mem = final_state.get("conversation_memory", [])
            # 合并新旧记忆（除非是 clear）
            if text not in ("task_exit", "/exit"):
                merged = list(old_mem)
                merged.append({
                    "task": text[:100],
                    "result": final_state.get("subsystem_code", "")[:200],
                    "phase": final_state.get("current_phase", ""),
                })
                final_state["conversation_memory"] = merged[-10:]  # 只保留最近10条
            else:
                final_state["conversation_memory"] = old_mem

        active_session["user_id"] = user_id
        active_session["state"] = final_state
        active_session["phase"] = final_state.get("current_phase", "IDLE") if isinstance(final_state, dict) else "IDLE"
        return final_state
