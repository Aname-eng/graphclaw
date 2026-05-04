"""Web UI 界面 - 与元·内阁对话."""

import gradio as gr
import os
from typing import Dict, Any, List

from meta_system import (
    MetaSystemState,
    process_user_message,
    build_main_graph,
    create_checkpointer,
    load_capacity_index,
    process_user_message_legacy,
)
from tools import registry


def create_web_ui():
    """创建Web UI界面（不启动服务器）."""
    graph = build_main_graph()
    compiled = graph.compile(checkpointer=create_checkpointer())
    session = {"state": {}, "phase": "IDLE", "user_id": "web_user"}

    with gr.Blocks(title="元·内阁 - Web UI") as demo:
        gr.Markdown("# 🏛️ 元·内阁 - 多智能体系统")
        gr.Markdown("与智能体团队对话：")

        chatbot = gr.Chatbot(height=500, label="对话")
        msg = gr.Textbox(label="你的输入", placeholder="输入你的任务...")
        clear = gr.Button("清空")

        def respond(message, history):
            try:
                from dotenv import load_dotenv
                load_dotenv()

                final_state = process_user_message_legacy(
                    "web_user", message, compiled, session
                )
                if isinstance(final_state, dict):
                    buf = final_state.get("stream_buffer", [])
                    response_text = "\n".join(buf) if buf else "任务完成"
                else:
                    response_text = "任务完成"

                history = history or []
                history.append((message, response_text))
                return "", history
            except Exception as e:
                history = history or []
                history.append((message, f"❌ 错误: {str(e)}"))
                return "", history

        def on_clear():
            return "", []

        msg.submit(respond, inputs=[msg, chatbot], outputs=[msg, chatbot])
        clear.click(on_clear, outputs=[msg, chatbot])

    return demo


def launch_web_ui():
    demo = create_web_ui()
    demo.launch(share=False)


if __name__ == "__main__":
    launch_web_ui()
