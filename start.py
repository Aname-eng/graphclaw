#!/usr/bin/env python
"""元·内阁 统一启动脚本 - 命令行/Web UI/飞书/图配置."""

import os
import sys
import subprocess
import json
import threading
from typing import Optional

try:
    import gradio as gr
    HAS_GRADIO = True
except ImportError:
    HAS_GRADIO = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(BASE_DIR, "meta_venv", "Scripts", "python.exe")
PYTHON = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable


def load_config():
    config_path = os.path.join(BASE_DIR, "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(config):
    with open(os.path.join(BASE_DIR, "config.json"), "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _launch_subprocess(mode: str):
    """在子进程中启动模式."""
    subprocess.Popen(
        [PYTHON, os.path.join(BASE_DIR, "start.py"), mode],
        cwd=BASE_DIR,
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
    )


def run_cli_mode():
    """运行命令行模式."""
    print("🚀 启动命令行模式...")
    from main import main as _main
    import sys
    # 去掉 'cli' 参数，让 main() 走默认 CLI 流程
    sys.argv = [sys.argv[0]]
    _main()


def run_web_ui_mode():
    print("🚀 启动Web UI模式...")
    from web_ui import launch_web_ui
    launch_web_ui()


def run_feishu_mode():
    """运行飞书模式（WebSocket 长连接，无需公网 IP）."""
    print("🚀 启动飞书 WebSocket 长连接模式...")
    print("   🤖 收到消息自动回复（TYPING反应 → LLM → 回复）")
    from feishu_gateway import run_feishu

    run_feishu(mode="websocket")


def run_graph_editor_mode():
    print("🚀 启动图编辑器...")
    from graph_editor import render_graph_editor
    demo = render_graph_editor()
    demo.launch(share=False)


def render_main_menu():
    config = load_config()

    with gr.Blocks(title="元·内阁 - 启动器") as demo:
        gr.Markdown("# 🏛️ 元·内阁 - 多智能体系统")
        gr.Markdown("点击按钮在新窗口中启动对应模式：")

        with gr.Row():
            with gr.Column(scale=2):
                gr.Markdown("## 📱 选择启动")
                with gr.Row():
                    cli_btn = gr.Button("💬 命令行模式", variant="primary", size="lg")
                    web_ui_btn = gr.Button("🌐 Web UI模式", variant="primary", size="lg")
                with gr.Row():
                    feishu_btn = gr.Button("📱 飞书模式", variant="primary", size="lg")
                    editor_btn = gr.Button("🎨 图编辑器", variant="primary", size="lg")

            with gr.Column(scale=1):
                gr.Markdown("## ⚙️ 配置")
                api_base = gr.Textbox(
                    label="API Base URL",
                    value=config.get("api_base", "https://api.minimaxi.com/anthropic"),
                    placeholder="API Base URL"
                )
                api_key = gr.Textbox(
                    label="API Key",
                    value=config.get("api_key", ""),
                    placeholder="sk-...", type="password"
                )
                model = gr.Textbox(
                    label="LLM Model",
                    value=config.get("model", "MiniMax-M2.1"),
                    placeholder="模型名称"
                )

                gr.Markdown("### 🤖 飞书配置")
                feishu_app_id = gr.Textbox(
                    label="飞书 APP ID",
                    value=config.get("feishu_app_id", ""),
                    placeholder="cli_...",
                )
                feishu_app_secret = gr.Textbox(
                    label="飞书 APP Secret",
                    value=config.get("feishu_app_secret", ""),
                    placeholder="xxx", type="password"
                )

                with gr.Row():
                    save_config_btn = gr.Button("💾 保存配置", variant="secondary")

        status = gr.Textbox(label="状态", value="就绪")

        def on_save_config(api_base_val, api_key_val, model_val, app_id_val, app_secret_val):
            config.update({
                "api_base": api_base_val, "api_key": api_key_val,
                "model": model_val, "feishu_app_id": app_id_val,
                "feishu_app_secret": app_secret_val
            })
            save_config(config)
            return "✅ 配置已保存"

        def on_launch_cli():
            _launch_subprocess("cli")
            return "✅ 命令行已在新窗口启动"

        def on_launch_web_ui():
            _launch_subprocess("web")
            return "✅ Web UI已在新窗口启动"

        def on_launch_feishu():
            _launch_subprocess("feishu")
            return "✅ 飞书已在新窗口启动"

        def on_launch_editor():
            _launch_subprocess("editor")
            return "✅ 图编辑器已在新窗口启动"

        save_config_btn.click(on_save_config, inputs=[api_base, api_key, model, feishu_app_id, feishu_app_secret], outputs=[status])
        cli_btn.click(on_launch_cli, outputs=[status])
        web_ui_btn.click(on_launch_web_ui, outputs=[status])
        feishu_btn.click(on_launch_feishu, outputs=[status])
        editor_btn.click(on_launch_editor, outputs=[status])

    return demo


def main():
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
        if mode == "cli":
            run_cli_mode()
        elif mode == "web":
            run_web_ui_mode()
        elif mode == "feishu":
            run_feishu_mode()
        elif mode == "editor":
            run_graph_editor_mode()
        else:
            print(f"未知模式: {mode}")
            print("用法: python start.py [cli|web|feishu|editor]")
    else:
        if not HAS_GRADIO:
            print("⚠️ gradio 未安装，自动使用命令行模式")
            print("   安装: pip install gradio")
            run_cli_mode()
            return
        print("🚀 启动主菜单...")
        demo = render_main_menu()
        demo.launch(share=False)


if __name__ == "__main__":
    main()
