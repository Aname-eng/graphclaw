"""LangGraph 可视化编辑器 — 类 Studio 风格."""

import gradio as gr
import json
import os
from typing import List, Dict, Any, Tuple, Optional

from graph_generator import (
    GraphCodeGenerator,
    get_default_config,
    NodeConfig,
    EdgeConfig
)


class GraphEditor:
    """图编辑器状态管理."""

    def __init__(self):
        self.generator = GraphCodeGenerator()
        self.config = get_default_config()

    def to_mermaid(self) -> str:
        if not self.config.nodes:
            return "graph TD\n  empty[暂无节点]"

        lines = ["graph TD"]
        node_ids = {n.id: f'n{n.id.replace("-", "_")}' for n in self.config.nodes}

        # 子图：按类型分组
        sys_nodes = [n for n in self.config.nodes if n.type == "system"]
        char_nodes = [n for n in self.config.nodes if n.type == "character"]

        for n in self.config.nodes:
            safe_id = node_ids[n.id]
            label = n.label.replace('"', "'")
            color = "#4CAF50" if n.type == "system" else "#2196F3"
            text_color = "#fff"
            lines.append(f'  {safe_id}["{label}"]')
            lines.append(f'  style {safe_id} fill:{color},color:{text_color},stroke:{color},stroke-width:2px')

        for e in self.config.edges:
            src = node_ids.get(e.from_node, e.from_node)
            dst = node_ids.get(e.to_node, e.to_node)
            if e.condition:
                cond = e.condition.replace('"', "'")
                lines.append(f'  {src} --x|"{cond}"|{dst}')
            else:
                lines.append(f'  {src} --> {dst}')

        return "\n".join(lines)

    def to_nodes_df(self) -> List[List]:
        return [[n.id, n.label, n.type, n.position.get("x", 0), n.position.get("y", 0)]
                for n in self.config.nodes]

    def to_edges_df(self) -> List[List]:
        return [[e.from_node, e.to_node, e.condition] for e in self.config.edges]

    def get_node(self, node_id: str) -> Optional[NodeConfig]:
        for n in self.config.nodes:
            if n.id == node_id:
                return n
        return None

    def add_node(self, node_id: str, label: str, node_type: str, personality: str = ""):
        self.config.nodes.append(NodeConfig(
            id=node_id, label=label, type=node_type, position={"x": 200, "y": 200}
        ))
        if personality.strip():
            self.generator.create_character(node_id, label, personality)

    def update_node(self, node_id: str, label: str = None, node_type: str = None):
        n = self.get_node(node_id)
        if n:
            if label:
                n.label = label
            if node_type:
                n.type = node_type

    def remove_node(self, node_id: str):
        self.config.nodes = [n for n in self.config.nodes if n.id != node_id]
        self.config.edges = [e for e in self.config.edges if e.from_node != node_id and e.to_node != node_id]

    def add_edge(self, from_node: str, to_node: str, condition: str = ""):
        self.config.edges.append(EdgeConfig(from_node=from_node, to_node=to_node, condition=condition))

    def remove_edge(self, from_node: str, to_node: str, condition: str = ""):
        self.config.edges = [e for e in self.config.edges
                             if not (e.from_node == from_node and e.to_node == to_node and e.condition == condition)]

    def save_config(self):
        self.generator.save_config(self.config)

    def generate_code(self):
        return self.generator.generate_code()

    def node_ids(self) -> List[str]:
        return [n.id for n in self.config.nodes]

    def edges_summary(self) -> List[str]:
        return [f"{e.from_node} → {e.to_node}" + (f" [{e.condition}]" if e.condition else "")
                for e in self.config.edges]


editor = GraphEditor()

CSS = """
.mermaid-container { background: #f8f9fa; border-radius: 12px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.mermaid svg { max-width: 100%; }
.node-card { border: 1px solid #e0e0e0; border-radius: 8px; padding: 10px 14px; margin: 6px 0; display: flex; align-items: center; gap: 10px; background: white; }
.node-card.system { border-left: 4px solid #4CAF50; }
.node-card.character { border-left: 4px solid #2196F3; }
.node-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
.badge-system { background: #E8F5E9; color: #2E7D32; }
.badge-character { background: #E3F2FD; color: #1565C0; }
"""


def _render_mermaid_html(mermaid_code: str) -> str:
    import html
    safe = html.escape(mermaid_code)
    return f"""<div class="mermaid-container">
  <pre class="mermaid" style="text-align:center;">
{safe}
  </pre>
  <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
  <script>mermaid.initialize({{startOnLoad:true,theme:'default',flowchart:{{useMaxWidth:true,htmlLabels:true,curve:'basis'}}}});</script>
</div>"""


def _node_cards_html() -> str:
    """生成节点卡片 HTML（仿 Studio 风格）."""
    parts = ['<div style="font-family:sans-serif;">']
    for n in editor.config.nodes:
        cls = n.type
        icon = "⚙️" if cls == "system" else "👤"
        badge_cls = "badge-system" if cls == "system" else "badge-character"
        parts.append(f"""
        <div class="node-card {cls}">
            <span style="font-size:20px;">{icon}</span>
            <div style="flex:1;">
                <div style="font-weight:600;font-size:14px;">{n.label}</div>
                <div style="font-size:11px;color:#666;">{n.id}</div>
            </div>
            <span class="node-badge {badge_cls}">{cls}</span>
        </div>""")
    if not editor.config.nodes:
        parts.append('<div style="color:#999;padding:12px;">暂无节点</div>')
    parts.append('</div>')
    return "".join(parts)


def render_graph_editor():
    """渲染图编辑器."""

    with gr.Blocks(title="元·内阁 图编辑器") as demo:
        gr.Markdown("# 🎨 LangGraph 图编辑器")
        gr.Markdown("可视化编辑工作流图，自动生成 Python 代码。")

        with gr.Row():
            # 左侧：画布
            with gr.Column(scale=7):
                mermaid_html = gr.HTML(value=_render_mermaid_html(editor.to_mermaid()))
                with gr.Row():
                    refresh_btn = gr.Button("🔄 刷新图", variant="secondary", size="sm")
                    load_config_btn = gr.Button("📂 加载配置", variant="secondary", size="sm")

            # 右侧：面板
            with gr.Column(scale=3):
                with gr.Tabs():
                    # === Tab 1: 节点 ===
                    with gr.TabItem("📦 节点"):
                        with gr.Group():
                            gr.Markdown("**添加节点**")
                            with gr.Row():
                                node_id_input = gr.Textbox(label="ID", value="new_node", scale=1)
                                node_label_input = gr.Textbox(label="名称", value="新节点", scale=2)
                            node_type_input = gr.Radio(["system", "character"], label="类型", value="system")
                            node_personality_input = gr.Textbox(label="Personality", lines=2, placeholder="角色设定（可选）")
                            add_node_btn = gr.Button("➕ 添加", variant="primary", size="sm")

                        gr.Markdown("**节点列表**")
                        node_cards = gr.HTML(value=_node_cards_html())
                        with gr.Row():
                            edit_node_select = gr.Dropdown([], label="选择编辑", scale=2)
                            edit_node_label = gr.Textbox(label="新名称", scale=1)
                        with gr.Row():
                            edit_node_btn = gr.Button("✏️ 改名", variant="secondary", size="sm")
                            delete_node_select = gr.Dropdown([], label="要删除的节点", scale=2)
                            delete_node_btn = gr.Button("🗑️ 删除", variant="stop", size="sm")

                    # === Tab 2: 连线 ===
                    with gr.TabItem("🔗 连线"):
                        gr.Markdown("**添加连线**")
                        from_node_select = gr.Dropdown([], label="从节点")
                        to_node_select = gr.Dropdown([], label="到节点")
                        edge_condition_input = gr.Textbox(label="条件（可选）", placeholder="如: after_search")
                        add_edge_btn = gr.Button("➕ 添加连线", variant="primary", size="sm")

                        gr.Markdown("**连线列表**")
                        edge_list = gr.Dropdown([], label="选择要删除的连线", multiselect=False)
                        delete_edge_btn = gr.Button("🗑️ 删除选中连线", variant="stop", size="sm")

                    # === Tab 3: 导出 ===
                    with gr.TabItem("📤 导出"):
                        gr.Markdown("**生成 LangGraph 代码**")
                        preview_code = gr.Code(label="Python 代码", language="python", interactive=False)
                        with gr.Row():
                            generate_btn = gr.Button("🚀 生成代码", variant="primary")
                            save_config_btn = gr.Button("💾 保存配置", variant="secondary")

        status = gr.Textbox(label="状态", value="就绪")

        # =========== 事件处理 ===========

        def refresh_all():
            m = editor.to_mermaid()
            return (
                _render_mermaid_html(m),
                _node_cards_html(),
                gr.Dropdown(choices=editor.node_ids()),
                gr.Dropdown(choices=editor.node_ids()),
                gr.Dropdown(choices=editor.node_ids()),
                gr.Dropdown(choices=editor.node_ids()),
                gr.Dropdown(choices=editor.edges_summary()),
                gr.Dropdown(choices=editor.node_ids()),
            )

        def on_add_node(node_id, label, ntype, personality):
            if node_id in [n.id for n in editor.config.nodes]:
                return list(refresh_all()) + ["❌ ID 已存在"]
            editor.add_node(node_id, label, ntype, personality)
            return list(refresh_all()) + [f"✅ 已添加: {label}"]

        def on_delete_node(node_id):
            if not node_id:
                return list(refresh_all()) + ["⚠️ 请选择节点"]
            editor.remove_node(node_id)
            return list(refresh_all()) + [f"🗑️ 已删除: {node_id}"]

        def on_edit_node(node_id, new_label):
            if not node_id or not new_label:
                return list(refresh_all()) + ["⚠️ 请选择节点并输入新名称"]
            editor.update_node(node_id, label=new_label)
            return list(refresh_all()) + [f"✏️ 已改名为: {new_label}"]

        def on_add_edge(from_n, to_n, cond):
            if not from_n or not to_n:
                return list(refresh_all()) + ["⚠️ 请选择起止节点"]
            if from_n == to_n:
                return list(refresh_all()) + ["⚠️ 不能自环"]
            editor.add_edge(from_n, to_n, cond)
            return list(refresh_all()) + [f"✅ 已添加连线: {from_n} → {to_n}"]

        def on_delete_edge(edge_label):
            if not edge_label:
                return list(refresh_all()) + ["⚠️ 请选择连线"]
            parts = edge_label.split(" → ")
            if len(parts) >= 2:
                from_n = parts[0]
                rest = parts[1]
                # 提取条件（如果有）
                if " [" in rest:
                    to_n = rest.split(" [")[0]
                    cond = rest.split(" [")[1].rstrip("]")
                else:
                    to_n = rest
                    cond = ""
                editor.remove_edge(from_n, to_n, cond)
            return list(refresh_all()) + [f"🗑️ 已删除连线: {edge_label}"]

        def on_generate_code():
            code = editor.generate_code()
            return code, "✅ 代码已生成"

        def on_save_config():
            editor.save_config()
            _, cards, *rest = refresh_all()
            return [cards] + list(rest) + ["✅ 配置已保存"]

        def on_load_config():
            editor.generator.load_config()
            editor.config = editor.generator.config
            return list(refresh_all()) + ["✅ 配置已加载"]

        # 输出列表
        out = [mermaid_html, node_cards, edit_node_select, delete_node_select,
               from_node_select, to_node_select, edge_list, delete_node_select, status]

        demo.load(fn=refresh_all, outputs=out)
        refresh_btn.click(fn=refresh_all, outputs=out)
        load_config_btn.click(fn=on_load_config, outputs=out)

        add_node_btn.click(
            fn=on_add_node,
            inputs=[node_id_input, node_label_input, node_type_input, node_personality_input],
            outputs=out
        )
        delete_node_btn.click(fn=on_delete_node, inputs=[delete_node_select], outputs=out)
        edit_node_btn.click(fn=on_edit_node, inputs=[edit_node_select, edit_node_label], outputs=out)
        add_edge_btn.click(
            fn=on_add_edge,
            inputs=[from_node_select, to_node_select, edge_condition_input],
            outputs=out
        )
        delete_edge_btn.click(fn=on_delete_edge, inputs=[edge_list], outputs=out)
        generate_btn.click(fn=on_generate_code, outputs=[preview_code, status])
        save_config_btn.click(fn=on_save_config, outputs=out)

    return demo


if __name__ == "__main__":
    demo = render_graph_editor()
    demo.launch(share=False)
