#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
元·内阁 — 主入口.

支持两种模式:
  chat (默认) — 直接和 AI 助手聊天
  task        — 通过 LangGraph 工作流处理复杂任务

命令:
  /chat     — 切换到聊天模式
  /task     — 切换到任务模式
  /shutdown — 保存全部状态并退出
  /cron     — 查看/管理定时任务
"""

import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()


# ============ 聊天模式 ============

def run_chat_mode():
    """聊天模式 — 类 Hermes 单智能体（支持多轮工具调用）."""
    from agent_loop import run_chat_session
    return run_chat_session()


# ============ 任务模式 ============

def run_task_mode(registry, lifecycle):
    """任务模式 — 通过 LangGraph 工作流."""
    from meta_system import build_main_graph, create_checkpointer, process_user_message_legacy
    from tools import registry as tool_registry

    print("=" * 40)
    print("📋 任务模式 (输入 /chat 切换到聊天, /shutdown 退出)")
    print("=" * 40)

    graph = build_main_graph()
    compiled = graph.compile(checkpointer=create_checkpointer())
    session = {"state": {}, "phase": "IDLE", "user_id": "cli_user"}

    while True:
        try:
            in_task = (
                isinstance(session.get("state"), dict)
                and session["state"].get("task_active", False)
            )
            prompt = "🔧" if in_task else "📋"
            user_input = input(f"{prompt} > ").strip()
            if not user_input:
                continue

            if user_input in ("/chat",):
                return "chat"

            if user_input == "/shutdown":
                return "shutdown"

            if user_input in ("/exit", "task_exit", "/task_exit"):
                if in_task:
                    final = process_user_message_legacy("cli_user", "task_exit", compiled, session)
                    print("  👋 已退出当前任务")
                continue

            if user_input == "/help":
                print("  命令: /chat 聊天 /shutdown 退出 /task_exit 退出当前任务")
                continue

            # 执行任务
            final = process_user_message_legacy("cli_user", user_input, compiled, session)
            if isinstance(final, dict):
                still_active = final.get("task_active", False)
                phase = final.get("current_phase", "IDLE")
                if not still_active or phase in ("IDLE", "COMPLETE"):
                    print("  💡 输入新内容继续修改，输入 task_exit 结束任务")

        except KeyboardInterrupt:
            print()
            return "shutdown"
        except EOFError:
            print()
            return "shutdown"
        except Exception as e:
            print(f"  ⚠️ 错误: {e}")


# ============ Cron 管理命令 ============

def handle_cron_command(cmd: str):
    """处理 /cron 系列命令."""
    from cron_manager import add_job, remove_job, list_jobs

    parts = cmd.split(maxsplit=2)
    sub = parts[0] if len(parts) > 0 else "list"

    if sub == "list":
        jobs = list_jobs()
        if not jobs:
            print("  暂无定时任务")
        else:
            for j in jobs:
                status = "✅" if j.get("enabled", True) else "⏸️"
                nxt = j.get("next_run_at", "?").split(".")[0].replace("T", " ")
                last = j.get("last_run_at", "从未").split(".")[0].replace("T", " ") if j.get("last_run_at") else "从未"
                print(f"  {status} {j['id']}: {j.get('name', '?')}")
                print(f"     下次: {nxt} | 上次: {last}")
        return

    if sub == "add" and len(parts) >= 3:
        schedule_str = parts[1]
        prompt = parts[2]
        job = add_job(prompt[:30], prompt, schedule_str)
        print(f"  ✅ 已添加 Cron 任务: {job['id']}")
        print(f"     提示词: {prompt[:60]}...")
        print(f"     调度: {schedule_str}")
        return

    if sub == "rm" and len(parts) >= 2:
        job_id = parts[1]
        if remove_job(job_id):
            print(f"  ✅ 已删除任务: {job_id}")
        else:
            print(f"  ❌ 未找到任务: {job_id}")
        return

    print("  Cron 命令:")
    print("    /cron list              — 列出任务")
    print("    /cron add <sched> <msg> — 添加 (sched: 30m, every 10m, 0 9 * * *)")
    print("    /cron rm <id>           — 删除任务")


# ============ 主入口 ============

def main():
    parser = argparse.ArgumentParser(description="元·内阁")
    parser.add_argument("--mode", type=str, choices=["web", "cli"], default="cli")
    args = parser.parse_args()

    if args.mode == "web":
        try:
            import web_ui
            app = web_ui.create_web_ui()
            app.launch(share=False)
        except ImportError as e:
            print(f"❌ 无法导入 web_ui: {e}")
        return

    # CLI 模式 — 启动 cron 调度器
    from cron_manager import CronScheduler, add_job, list_jobs

    def cron_executor(prompt):
        from meta_system import get_llm_client, load_character_personality
        client = get_llm_client()
        personality = load_character_personality("executor")
        messages = [
            {"role": "system", "content": f"{personality}\n\n你是一个定时任务执行者。请根据以下提示词完成任务，输出详细的结果报告。"},
            {"role": "user", "content": prompt},
        ]
        result = client.chat(messages, temperature=0.5)
        reply = ""
        if "content" in result and len(result["content"]) > 0:
            for block in result["content"]:
                if block.get("type") == "text":
                    reply = block["text"]
                    break
        return reply or str(result)

    scheduler = CronScheduler(executor=cron_executor)
    scheduler.start()

    # 恢复已保存的记忆
    memory_file = "saved_memory.json"
    memories = []
    if os.path.exists(memory_file):
        try:
            with open(memory_file, "r", encoding="utf-8") as f:
                memories = json.load(f)
            print(f"  📚 已恢复 {len(memories)} 条会话记忆")
        except Exception:
            pass

    # 默认进入聊天模式
    current_mode = "chat"

    while True:
        if current_mode == "chat":
            result = run_chat_mode()
            if result == "task":
                current_mode = "task"
                continue
            elif result == "shutdown":
                break
            else:
                continue

        elif current_mode == "task":
            from graph_registry import GraphRegistry
            from graph_lifecycle import GraphLifecycleManager
            registry = GraphRegistry("./graphs")
            lifecycle = GraphLifecycleManager(registry)

            result = run_task_mode(registry, lifecycle)
            if result == "chat":
                current_mode = "chat"
                continue
            elif result == "shutdown":
                break
            else:
                continue

        break

    # ============ /shutdown 保存逻辑 ============
    print("\n💾 正在保存状态...")

    # 保存记忆
    try:
        with open(memory_file, "w", encoding="utf-8") as f:
            json.dump(memories, f, ensure_ascii=False, indent=2)
        print(f"  ✅ 记忆已保存: {len(memories)} 条")
    except Exception as e:
        print(f"  ⚠️ 记忆保存失败: {e}")

    # 保存飞书会话记忆
    try:
        from feishu_gateway import FeishuWSHandler
        mem_file = "feishu_memory.json"
        if os.path.exists(mem_file):
            print(f"  ✅ 飞书记忆已保留")
    except Exception:
        pass

    scheduler.stop()
    print("👋 系统已关闭")


if __name__ == "__main__":
    main()
