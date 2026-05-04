import os
import json
import uuid
import re
from typing import Dict, List, Optional, Any

from graph_registry import GraphRegistry
from graph_lifecycle import GraphLifecycleManager


def get_subgraph_decision_prompt(task: str) -> str:
    """
    生成子图决策提示，让 LLM 智能判断是否需要新建子图。

    这个提示会被注入到 LLM 的 System Prompt 中，
    LLM 会根据这个提示自主判断当前任务是否需要创建新的子图。
    """
    return f"""## 🤔 子图创建决策

在你开始处理任务之前，请先判断：当前的默认架构能否有效完成这个任务？

**请从以下几个维度自我评估：**

1. **任务类型匹配度**
   - 这个任务是否需要多轮对话、多步骤处理？
   - 是否需要不同于默认流程的特殊处理逻辑？
   - 是否需要专门的工具组合？

2. **上下文复杂度**
   - 这个任务与当前的对话上下文是否高度相关？
   - 是否需要长期记忆或状态保持？
   - 是否需要隔离的执行环境？

3. **资源需求**
   - 是否需要特殊的工具或权限？
   - 是否可能影响其他任务的正常执行？
   - 是否需要独立的错误处理机制？

4. **可扩展性**
   - 这个任务未来是否会变得更复杂？
   - 是否可能需要并行处理多个子任务？
   - 是否需要专门的状态管理？

**决策指南：**
- 如果以上大部分问题的答案是"否"，=> 使用当前默认架构处理
- 如果以上大部分问题的答案是"是"，=> 考虑创建专门的子图

**创建子图的情况包括但不限于：**
- 复杂的多阶段项目（研究与报告、代码开发与测试、数据分析与可视化）
- 需要专门角色的任务（作为专家顾问、作为审核员、作为架构师）
- 需要长期运行的任务（监控、持续集成、后台处理）
- 高风险操作（系统配置、文件大批量处理、第三方服务集成）

---

**你的任务：** {task}

请基于以上分析，决定：
A) 继续使用当前默认架构处理
B) 创建新的专门子图来处理

在回复中，请明确说明你的判断理由。"""


def should_create_subgraph(task: str) -> dict:
    """
    判断是否应该创建子图。

    注意：这个函数不再做写死的规则判断，
    而是返回一个提示模板供 LLM 使用。
    LLM 会根据 get_subgraph_decision_prompt 自主决策。

    为了向后兼容，返回一个标准格式。
    实际决策由 LLM 在 process_user_message 中做出。
    """
    return {
        "decision_prompt": get_subgraph_decision_prompt(task),
        "note": "实际决策由 LLM 基于上述提示自主判断，此处不再做写死判断"
    }


class SubGraphBuilder:
    """
    子图构建器：根据任务需求构建 LangGraph 子图。

    决策由 LLM 做出，这个类负责根据决策生成子图代码。
    """

    def __init__(
        self,
        registry: GraphRegistry,
        lifecycle: GraphLifecycleManager,
        graphs_dir: str = "./graphs",
    ):
        self.registry = registry
        self.lifecycle = lifecycle
        self.graphs_dir = os.path.abspath(graphs_dir)

    def get_decision_prompt(self, task: str) -> str:
        """
        获取子图决策提示，让 LLM 自己判断是否需要子图。

        Args:
            task: 任务描述

        Returns:
            包含决策提示的字符串
        """
        return get_subgraph_decision_prompt(task)

    def build_subgraph(self, task: str, parent_graph_id: str = "default",
                       complexity_score: int = None) -> Optional[str]:
        """
        构建子图。

        注意：这个方法不再自己做决策。
        调用方应该先通过 get_decision_prompt 让 LLM 决策，
        然后根据 LLM 的决策调用这个方法。

        Args:
            task: 任务描述字符串
            parent_graph_id: 父图 ID，默认为 "default"
            complexity_score: 复杂度评分（可选，用于决定节点数量）

        Returns:
            生成的子图 graph_id
        """
        if complexity_score is None:
            complexity_score = 60  # 默认中等复杂度

        complexity = {
            "score": complexity_score,
            "is_complex": True,
            "reason": "LLM 决策生成子图"
        }

        # 生成子图定义
        graph_def = self._generate_graph_definition(task, complexity)

        # 先注册获取 graph_id
        temp_name = graph_def.get("name", "subgraph_temp")
        temp_desc = graph_def.get("description", f"Auto-generated subgraph for: {task[:50]}")
        registered_id = self.registry.register_graph(
            name=temp_name,
            description=temp_desc,
            task_description=task,
            file_path="",  # 临时，后面更新
            parent_id=parent_graph_id,
        )

        # 生成可执行代码
        code = self._generate_graph_code(graph_def, registered_id)

        # 保存文件（使用 registered_id）
        file_path = self._save_graph_files(registered_id, graph_def, code)

        # 更新 file_path
        graph_info = self.registry.get_graph(registered_id)
        if graph_info:
            graph_info.file_path = file_path
            self.registry._persist_registry()

        return registered_id

    def _generate_graph_definition(self, task: str, complexity: dict) -> dict:
        """
        基于规则引擎生成子图定义（节点列表、边、状态定义）。

        Args:
            task: 任务描述
            complexity: 复杂度分析结果

        Returns:
            子图定义字典
        """
        score = complexity["score"]

        if score >= 80:
            # 高复杂度：5 节点图
            nodes = [
                {"id": "entry", "type": "entry", "description": "入口节点：接收输入并初始化状态"},
                {"id": "analyze", "type": "process", "description": "分析节点：解析任务需求"},
                {"id": "process", "type": "process", "description": "处理节点：执行核心逻辑"},
                {"id": "review", "type": "process", "description": "审核节点：检查结果质量"},
                {"id": "exit", "type": "exit", "description": "出口节点：输出最终结果"},
            ]
            edges = [
                {"from": "entry", "to": "analyze"},
                {"from": "analyze", "to": "process"},
                {"from": "process", "to": "review"},
                {"from": "review", "to": "exit"},
            ]
            name = "complex_subgraph"
            description = f"高复杂度子图（score={score}），包含分析-处理-审核流程"
        else:
            # 中等复杂度：3 节点图
            nodes = [
                {"id": "entry", "type": "entry", "description": "入口节点：接收输入并初始化状态"},
                {"id": "process", "type": "process", "description": "处理节点：执行核心逻辑"},
                {"id": "exit", "type": "exit", "description": "出口节点：输出最终结果"},
            ]
            edges = [
                {"from": "entry", "to": "process"},
                {"from": "process", "to": "exit"},
            ]
            name = "simple_subgraph"
            description = f"中等复杂度子图（score={score}），包含标准处理流程"

        # 状态定义
        state_schema = {
            "task": {"type": "str", "description": "原始任务描述"},
            "input_data": {"type": "Any", "description": "输入数据"},
            "output_data": {"type": "Any", "description": "输出数据"},
            "status": {"type": "str", "description": "当前状态: pending, processing, completed, error"},
            "metadata": {"type": "dict", "description": "附加元数据"},
        }

        # 如果有 analyze 节点，添加分析结果字段
        if score >= 80:
            state_schema["analysis_result"] = {"type": "Any", "description": "分析结果"}
            state_schema["review_result"] = {"type": "Any", "description": "审核结果"}

        graph_def = {
            "name": name,
            "description": description,
            "complexity": complexity,
            "task": task,
            "nodes": nodes,
            "edges": edges,
            "state_schema": state_schema,
        }

        return graph_def

    def _generate_graph_code(self, graph_def: dict, graph_id: str) -> str:
        """
        根据子图定义生成可执行的 Python 代码字符串。

        生成的代码使用 LangGraph 的 StateGraph，可独立导入执行。

        Args:
            graph_def: 子图定义字典
            graph_id: 子图唯一标识

        Returns:
            Python 代码字符串
        """
        nodes = graph_def["nodes"]
        edges = graph_def["edges"]
        state_schema = graph_def["state_schema"]

        # 判断是否有 analyze 和 review 节点
        has_analyze = any(n["id"] == "analyze" for n in nodes)
        has_review = any(n["id"] == "review" for n in nodes)

        # 构建 TypedDict 字段
        typed_dict_fields = []
        for field_name, field_info in state_schema.items():
            field_type = field_info["type"]
            typed_dict_fields.append(f'    {field_name}: Optional[{field_type}]')

        # 构建节点函数
        node_functions = []

        # entry_node
        entry_func = '''def entry_node(state: SubGraphState) -> SubGraphState:
    """入口节点：接收输入并初始化状态。"""
    return {
        **state,
        "status": "processing",
        "metadata": {**(state.get("metadata") or {}), "graph_id": "''' + graph_id + '''"},
    }'''
        node_functions.append(entry_func)

        # analyze_node (可选)
        if has_analyze:
            analyze_func = '''def analyze_node(state: SubGraphState) -> SubGraphState:
    """分析节点：解析任务需求。"""
    task = state.get("task", "")
    analysis = f"分析任务: {task[:50]}..."
    return {
        **state,
        "analysis_result": analysis,
        "metadata": {**(state.get("metadata") or {}), "analyzed": True},
    }'''
            node_functions.append(analyze_func)

        # process_node
        process_func = '''def process_node(state: SubGraphState) -> SubGraphState:
    """处理节点：执行核心逻辑。"""
    task = state.get("task", "")
    input_data = state.get("input_data")
    # 核心处理逻辑（占位，实际使用时替换）
    result = f"处理结果: 任务 '{task[:30]}...' 已执行"
    return {
        **state,
        "output_data": result,
        "metadata": {**(state.get("metadata") or {}), "processed": True},
    }'''
        node_functions.append(process_func)

        # review_node (可选)
        if has_review:
            review_func = '''def review_node(state: SubGraphState) -> SubGraphState:
    """审核节点：检查结果质量。"""
    output = state.get("output_data")
    review = f"审核结果: 输出 '{str(output)[:30]}...' 质量合格"
    return {
        **state,
        "review_result": review,
        "metadata": {**(state.get("metadata") or {}), "reviewed": True},
    }'''
            node_functions.append(review_func)

        # exit_node
        exit_func = '''def exit_node(state: SubGraphState) -> SubGraphState:
    """出口节点：输出最终结果。"""
    return {
        **state,
        "status": "completed",
        "metadata": {**(state.get("metadata") or {}), "finished": True},
    }'''
        node_functions.append(exit_func)

        # 构建 add_node 和 add_edge 代码
        add_nodes = []
        for node in nodes:
            node_id = node["id"]
            add_nodes.append(f'    builder.add_node("{node_id}", {node_id}_node)')

        add_edges = []
        for edge in edges:
            add_edges.append(f'    builder.add_edge("{edge["from"]}", "{edge["to"]}")')

        # 设置入口点
        set_entry = '    builder.set_entry_point("entry")'

        # 组装完整代码
        code_lines = [
            '"""',
            f'Auto-generated subgraph: {graph_id}',
            '',
            f'Generated for task: {graph_def.get("task", "")[:80]}',
            f'Complexity score: {graph_def.get("complexity", {}).get("score", 0)}',
            '"""',
            '',
            'from typing import Optional, Any, Dict',
            'from langgraph.graph import StateGraph',
            'from typing_extensions import TypedDict',
            '',
            '',
            'class SubGraphState(TypedDict, total=False):',
        ]
        code_lines.extend(typed_dict_fields)
        code_lines.extend([
            '',
            '',
        ])
        code_lines.extend(node_functions)
        code_lines.extend([
            '',
            '',
            'def build_graph():',
            '    """构建并编译子图，返回编译后的图对象。"""',
            '    builder = StateGraph(SubGraphState)',
        ])
        code_lines.extend(add_nodes)
        code_lines.extend(add_edges)
        code_lines.append(set_entry)
        code_lines.extend([
            '    return builder.compile()',
            '',
            '',
            'if __name__ == "__main__":',
            '    # 简单测试',
            '    graph = build_graph()',
            '    result = graph.invoke({',
            '        "task": "测试任务",',
            '        "input_data": None,',
            '        "status": "pending",',
            '    })',
            '    print("Result:", result)',
            '',
        ])

        return "\n".join(code_lines)

    def _save_graph_files(self, graph_id: str, graph_def: dict, code: str) -> str:
        """
        保存子图定义和代码到 graphs/{graph_id}/ 目录。

        Args:
            graph_id: 子图唯一标识
            graph_def: 子图定义字典
            code: 生成的 Python 代码

        Returns:
            保存的代码文件路径
        """
        graph_dir = os.path.join(self.graphs_dir, graph_id)
        os.makedirs(graph_dir, exist_ok=True)

        # 保存 graph_def.json
        def_path = os.path.join(graph_dir, "graph_def.json")
        with open(def_path, "w", encoding="utf-8") as f:
            json.dump(graph_def, f, ensure_ascii=False, indent=2)

        # 保存 subgraph.py
        code_path = os.path.join(graph_dir, "subgraph.py")
        with open(code_path, "w", encoding="utf-8") as f:
            f.write(code)

        return code_path
