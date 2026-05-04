SUBGRAPH_TEMPLATE = """
# 占位符说明：
# {graph_id}    - 子图唯一标识符，用于区分不同的子图实例
# {task}        - 子图要处理的具体任务描述
# {node_count}  - 子图中节点的数量（根据任务复杂度动态生成）
# {node_names}  - 子图中所有节点的名称列表，逗号分隔

from typing import TypedDict, List, Dict, Optional, Any
from langgraph.graph import StateGraph, END

class SubGraphState(TypedDict):
    task: str
    context: Dict[str, Any]
    results: List[str]
    current_step: int
    is_complete: bool
    output: str

# 入口节点
def entry_node(state: SubGraphState) -> SubGraphState:
    state["current_step"] = 1
    state["results"] = []
    state["output"] = f"开始处理任务: {task}"
    return state

# 处理节点（根据复杂度可能有多个）
def process_node(state: SubGraphState) -> SubGraphState:
    state["current_step"] += 1
    # 实际处理逻辑由 SubGraphBuilder 填充
    state["results"].append("处理中...")
    return state

# 审核节点（高复杂度时启用）
def review_node(state: SubGraphState) -> SubGraphState:
    state["current_step"] += 1
    state["results"].append("审核完成")
    return state

# 出口节点
def exit_node(state: SubGraphState) -> SubGraphState:
    state["is_complete"] = True
    state["output"] = "\\n".join(state["results"])
    return state

# 路由函数
def should_continue(state: SubGraphState) -> str:
    if state["is_complete"]:
        return "end"
    return "continue"

# 构建图
def build_graph():
    workflow = StateGraph(SubGraphState)
    
    workflow.add_node("entry", entry_node)
    workflow.add_node("process", process_node)
    workflow.add_node("exit", exit_node)
    
    workflow.set_entry_point("entry")
    workflow.add_edge("entry", "process")
    workflow.add_edge("process", "exit")
    workflow.add_edge("exit", END)
    
    return workflow.compile()

# 执行入口
def run(task: str, context: Dict = None) -> Dict:
    graph = build_graph()
    initial_state = SubGraphState(
        task=task,
        context=context or {{}},
        results=[],
        current_step=0,
        is_complete=False,
        output=""
    )
    result = graph.invoke(initial_state)
    return result
"""
