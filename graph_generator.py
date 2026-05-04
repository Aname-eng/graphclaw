"""LangGraph 配置到代码生成器."""

import json
import os
from typing import Dict, Any, List

from dataclasses import dataclass


@dataclass
class NodeConfig:
    id: str
    label: str
    type: str  # "system" | "character"
    position: Dict[str, int]
    personality_file: str = ""


@dataclass
class EdgeConfig:
    from_node: str
    to_node: str
    condition: str = ""


@dataclass
class GraphConfig:
    version: str = "1.0"
    name: str = "工作流"
    entry_point: str = "parse_task"
    nodes: List[NodeConfig] = None
    edges: List[EdgeConfig] = None

    def __post_init__(self):
        if self.nodes is None:
            self.nodes = []
        if self.edges is None:
            self.edges = []


class GraphCodeGenerator:
    """从图配置生成 LangGraph Python 代码."""

    def __init__(self, config_path: str = "graph_config.json"):
        self.config_path = config_path
        self.config = self.load_config()

    def load_config(self) -> GraphConfig:
        """加载图配置."""
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                nodes = [NodeConfig(**n) for n in data.get("nodes", [])]
                # 'from'/'to' 是 Python 关键字，映射为 from_node/to_node
                raw_edges = data.get("edges", [])
                edges = []
                for e in raw_edges:
                    edges.append(EdgeConfig(
                        from_node=e.get("from", ""),
                        to_node=e.get("to", ""),
                        condition=e.get("condition", ""),
                    ))
                return GraphConfig(
                    version=data.get("version", "1.0"),
                    name=data.get("name", "工作流"),
                    entry_point=data.get("entry_point", "parse_task"),
                    nodes=nodes,
                    edges=edges
                )
        return GraphConfig()

    def save_config(self, config: GraphConfig):
        """保存图配置."""
        data = {
            "version": config.version,
            "name": config.name,
            "entry_point": config.entry_point,
            "nodes": [
                {
                    "id": n.id,
                    "label": n.label,
                    "type": n.type,
                    "position": n.position,
                    "personality_file": n.personality_file
                } for n in config.nodes
            ],
            "edges": [
                {
                    "from": e.from_node,
                    "to": e.to_node,
                    "condition": e.condition
                } for e in config.edges
            ]
        }
        config_dir = os.path.dirname(self.config_path)
        if config_dir:
            os.makedirs(config_dir, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.config = config

    def create_character(self, char_id: str, label: str, personality: str) -> bool:
        """创建新角色文件夹和personality文件."""
        char_dir = f"characters/{char_id}"
        os.makedirs(char_dir, exist_ok=True)

        personality_path = os.path.join(char_dir, "personality.md")
        with open(personality_path, "w", encoding="utf-8") as f:
            f.write(personality)

        return True

    def generate_code(self, output_path: str = "generated_graph.py") -> str:
        """生成 LangGraph Python 代码."""
        code = []

        # 头部导入
        code.append('"""自动生成的 LangGraph 工作流."""')
        code.append("")
        code.append("from typing import TypedDict, List, Dict, Optional, Any")
        code.append("from langgraph.graph import StateGraph, END")
        code.append("")
        code.append("from meta_system import MetaSystemState")
        code.append("from meta_system import (")
        code.append("    parse_user_task_node,")
        code.append("    search_capacity_node,")
        code.append("    fang_a_proposal_node,")
        code.append("    fang_b_critic_node,")
        code.append("    fang_c_summary_node,")
        code.append("    du_ruhui_audit_node,")
        code.append("    executor_node,")
        code.append("    du_ruhui_audit_code_node,")
        code.append("    du_ruhui_judge_responsibility_node,")
        code.append("    deploy_subsystem_node")
        code.append(")")
        code.append("")
        code.append("")

        # 构建函数
        code.append("def build_custom_graph():")
        code.append("    \"\"\"构建自定义工作流图.\"\"\"")
        code.append("    builder = StateGraph(MetaSystemState)")
        code.append("")

        # 添加节点
        node_map = {}
        for node in self.config.nodes:
            node_func = f"{node.id}_node"
            code.append(f'    builder.add_node("{node.id}", {node_func})')
            node_map[node.id] = node_func

        code.append("")

        # 设置入口
        code.append(f'    builder.set_entry_point("{self.config.entry_point}")')
        code.append("")

        # 添加边
        conditional_count = 0
        for edge in self.config.edges:
            if edge.condition:
                code.append(f'    def after_{edge.from_node}_{conditional_count}(state):')
                code.append(f'        return "{edge.to_node}"')
                code.append("")
                code.append(f'    builder.add_conditional_edges("{edge.from_node}", after_{edge.from_node}_{conditional_count})')
                conditional_count += 1
            else:
                code.append(f'    builder.add_edge("{edge.from_node}", "{edge.to_node}")')

        code.append("")

        # 结束
        code.append("    return builder.compile()")
        code.append("")

        # 生成完整代码
        full_code = "\n".join(code)

        # 保存文件
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(full_code)

        return full_code


def get_default_config() -> GraphConfig:
    """获取默认的工作流配置."""
    return GraphConfig(
        name="默认工作流",
        entry_point="parse_task",
        nodes=[
            NodeConfig(id="parse_task", label="任务解析", type="system", position={"x": 100, "y": 100}, personality_file="characters/parse_task/personality.md"),
            NodeConfig(id="search_capacity", label="能力搜索", type="system", position={"x": 300, "y": 100}),
            NodeConfig(id="fang_a", label="房玄龄A", type="character", position={"x": 500, "y": 100}, personality_file="characters/fang_a/personality.md"),
            NodeConfig(id="fang_b", label="房玄龄B", type="character", position={"x": 500, "y": 250}, personality_file="characters/fang_b/personality.md"),
            NodeConfig(id="fang_c", label="房玄龄C", type="character", position={"x": 700, "y": 100}, personality_file="characters/fang_c/personality.md"),
            NodeConfig(id="du_ruhui", label="杜如晦", type="character", position={"x": 900, "y": 100}, personality_file="characters/du_ruhui/personality.md"),
            NodeConfig(id="executor", label="执行者", type="character", position={"x": 700, "y": 250}, personality_file="characters/executor/personality.md"),
            NodeConfig(id="du_ruhui_code", label="杜如晦代码", type="character", position={"x": 900, "y": 250}, personality_file="characters/du_ruhui/personality.md"),
            NodeConfig(id="du_ruhui_judge", label="杜如晦判断", type="character", position={"x": 1100, "y": 150}),
            NodeConfig(id="deploy", label="部署", type="system", position={"x": 1300, "y": 150}),
        ],
        edges=[
            EdgeConfig(from_node="parse_task", to_node="search_capacity"),
            EdgeConfig(from_node="search_capacity", to_node="fang_a", condition="after_search_capacity"),
            EdgeConfig(from_node="fang_a", to_node="fang_b"),
            EdgeConfig(from_node="fang_b", to_node="fang_c"),
            EdgeConfig(from_node="fang_c", to_node="du_ruhui"),
            EdgeConfig(from_node="du_ruhui", to_node="executor", condition="after_design_audit"),
            EdgeConfig(from_node="executor", to_node="du_ruhui_code"),
            EdgeConfig(from_node="du_ruhui_code", to_node="du_ruhui_judge", condition="after_code_audit"),
            EdgeConfig(from_node="du_ruhui_judge", to_node="deploy", condition="after_judge_responsibility"),
            EdgeConfig(from_node="deploy", to_node="END"),
        ]
    )


if __name__ == "__main__":
    generator = GraphCodeGenerator()
    default_config = get_default_config()
    generator.save_config(default_config)
    code = generator.generate_code()
    print(f"代码已生成:\n{code[:500]}...")
