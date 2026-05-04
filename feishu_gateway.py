"""
飞书网关 - 支持 WebSocket 长连接 和 Webhook 两种模式.

WebSocket 模式（推荐）：
  通过 lark_oapi.ws.Client 建立长连接，实时接收飞书事件回调。
  无需公网 IP，适合本地开发。
"""

import os
import json
import time
import asyncio
import threading
import logging
from typing import Dict, Any, Optional, Callable

from dotenv import load_dotenv

try:
    import lark_oapi as lark
    from lark_oapi.ws import Client as FeishuWSClient
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
    FEISHU_AVAILABLE = True
except ImportError:
    FEISHU_AVAILABLE = False
    lark = None
    FeishuWSClient = None
    EventDispatcherHandler = None

load_dotenv()

logger = logging.getLogger("feishu_gateway")


def load_feishu_config(config: Optional[Dict] = None):
    if config:
        app_id = config.get("feishu_app_id", "")
        app_secret = config.get("feishu_app_secret", "")
    else:
        app_id = os.getenv("FEISHU_APP_ID", "")
        app_secret = os.getenv("FEISHU_APP_SECRET", "")
    return app_id, app_secret


def send_message_http(token: str, receive_id: str, text: str,
                      receive_id_type: str = "open_id") -> bool:
    import requests
    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"receive_id": receive_id, "msg_type": "text", "content": json.dumps({"text": text})}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        result = resp.json()
        return result.get("code") == 0
    except Exception as e:
        logger.error(f"消息发送异常: {e}")
        return False


def get_tenant_token(app_id: str, app_secret: str) -> Optional[str]:
    import requests
    try:
        resp = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                             headers={"Content-Type": "application/json"},
                             json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
        data = resp.json()
        return data.get("tenant_access_token") if data.get("code") == 0 else None
    except Exception as e:
        logger.error(f"获取 token 失败: {e}")
        return None


# ============================================================
# WebSocket 长连接模式 — 参考 Hermes 实现
# ============================================================

class FeishuWSHandler:
    """飞书 WebSocket 事件处理器 (Hermes 风格)."""

    def __init__(self, app_id: str, app_secret: str,
                 on_message: Optional[Callable] = None):
        self.app_id = app_id
        self.app_secret = app_secret
        self.on_message = on_message
        self.ws_client: Optional[FeishuWSClient] = None
        self._token_cache: Optional[str] = None
        self._token_expire: float = 0
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def _handle_command(self, text: str, token: str, message_id: str, sender_id: str) -> Optional[str]:
        """处理系统命令。返回回复文本，None 表示非命令."""
        cmd = text.lower().strip()

        if cmd == "/help":
            return (
                "📋 **元·内阁 飞书机器人命令**\n\n"
                "/help - 显示此帮助\n"
                "/shutdown - 停止机器人\n"
                "/task <内容> - 使用工作流处理任务\n"
                "/chat - 切换回聊天模式（默认）\n"
                "/cron list - 查看定时任务\n"
                "/cron add <间隔> <提示词> - 添加定时任务\n"
                "/cron rm <id> - 删除定时任务\n\n"
                "💬 直接发消息就是聊天模式"
            )

        if cmd == "/shutdown":
            print("  👋 收到 /shutdown，正在停止飞书网关...")
            # 延迟关闭，先回复再退出
            threading.Thread(target=lambda: (time.sleep(1), self.stop(), os._exit(0)), daemon=True).start()
            return "👋 正在停止机器人..."

        if cmd.startswith("/task "):
            task_content = text[6:]
            reply = self._run_task_mode(task_content, token, sender_id)
            return f"📋 任务模式结果:\n{reply[:1500]}"

        if cmd == "/chat":
            return "💬 已在聊天模式"

        if cmd.startswith("/cron"):
            parts = text.split(maxsplit=2)
            if len(parts) >= 1:
                return self._handle_cron_command(parts, token, sender_id)

        return None  # 非命令，交给 LLM

    def _run_task_mode(self, content: str, token: str, user_id: str) -> str:
        """运行一次任务模式（LangGraph 工作流）."""
        try:
            from meta_system import build_main_graph, create_checkpointer, process_user_message_legacy
            graph = build_main_graph()
            compiled = graph.compile(checkpointer=create_checkpointer())
            session = {"state": {}, "phase": "IDLE", "user_id": "feishu_user"}
            final = process_user_message_legacy("feishu_user", content, compiled, session)
            buf = final.get("stream_buffer", [])
            return "\n".join(buf) if isinstance(buf, list) else str(buf)
        except Exception as e:
            return f"任务执行失败: {e}"

    def _handle_cron_command(self, parts: list, token: str, user_id: str) -> str:
        """处理 /cron 子命令."""
        from cron_manager import add_job, remove_job, list_jobs
        sub = parts[1] if len(parts) > 1 else "list"

        if sub == "list":
            jobs = list_jobs()
            if not jobs:
                return "暂无定时任务"
            lines = ["📅 **定时任务列表**:", ""]
            for j in jobs:
                nxt = j.get("next_run_at", "?").split(".")[0].replace("T", " ")
                status = "✅" if j.get("enabled", True) else "⏸️"
                lines.append(f"{status} `{j['id']}`: {j.get('name', '?')}")
                lines.append(f"   下次: {nxt}")
            return "\n".join(lines)

        if sub == "add" and len(parts) >= 3:
            sched = parts[2]
            prompt = parts[3] if len(parts) > 3 else ""
            if not prompt:
                return "用法: /cron add <间隔> <提示词>\n如: /cron add 30m 检查系统状态"
            job = add_job(prompt[:30], prompt, sched)
            return f"✅ 已添加任务 `{job['id']}`\n调度: {sched}\n提示词: {prompt[:60]}"

        if sub == "rm" and len(parts) >= 2:
            job_id = parts[2] if len(parts) > 2 else ""
            if remove_job(job_id):
                return f"✅ 已删除任务 `{job_id}`"
            return f"❌ 未找到任务 `{job_id}`"

        return "Cron 命令: list / add <间隔> <提示词> / rm <id>"

    def _get_token(self) -> Optional[str]:
        if time.time() < self._token_expire:
            return self._token_cache
        self._token_cache = get_tenant_token(self.app_id, self.app_secret)
        if self._token_cache:
            self._token_expire = time.time() + 3600
        return self._token_cache

    def _reply_message(self, token: str, receive_id: str, text: str,
                       message_id: str = "") -> bool:
        """回复消息 — 优先用 message_id 回复，否则发新消息."""
        if message_id:
            # 尝试回复到原消息的会话
            url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            payload = {"content": json.dumps({"text": text}), "msg_type": "text"}
            try:
                import requests
                resp = requests.post(url, headers=headers, json=payload, timeout=10)
                if resp.json().get("code") == 0:
                    return True
            except Exception:
                pass
        # 降级：发新消息
        return send_message_http(token, receive_id, text)

    def _send_reaction(self, token: str, message_id: str, emoji: str = "TYPING"):
        """发送消息反应 (emoji)."""
        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"reaction_type": {"emoji_type": emoji}}
        try:
            import requests
            resp = requests.post(url, headers=headers, json=payload, timeout=5)
            return resp.json().get("code") == 0
        except Exception:
            return False

    def _handle_message(self, event):
        """处理消息事件 — Hermes 风格: 反应 → LLM → 回复."""
        try:
            event_data = event.event
            if not event_data:
                return

            message = event_data.message
            sender = event_data.sender
            if not message or not sender:
                return

            msg_type = message.message_type or ""
            content_str = message.content or "{}"
            chat_id = message.chat_id or ""
            message_id = message.message_id or ""

            sender_id = sender.sender_id if hasattr(sender, "sender_id") else None
            sender_open_id = ""
            if sender_id:
                sender_open_id = getattr(sender_id, "open_id", "") or ""
            if not sender_open_id and hasattr(sender, "open_id"):
                sender_open_id = sender.open_id or ""

            try:
                content = json.loads(content_str)
            except json.JSONDecodeError:
                content = {"text": content_str}

            text = ""
            file_path = None
            if msg_type == "text":
                text = content.get("text", "")
            elif msg_type == "post":
                try:
                    post_data = content.get("post", {})
                    locale = post_data.get("zh_cn", {}) or post_data.get("en_us", {}) or {}
                    for row in locale.get("content", []):
                        for item in row:
                            if item.get("tag") == "text":
                                text += item.get("text", "")
                except Exception:
                    text = "[富文本消息]"
            elif msg_type == "file":
                file_key = content.get("file_key", "")
                file_name = content.get("file_name", "未知文件")
                text = f"[文件] {file_name}"
                # 尝试下载文件
                try:
                    from feishu_files import handle_file_message
                    token = self._get_token()
                    if token and file_key:
                        file_path = handle_file_message(token, message_id, file_key, file_name)
                        text = f"[已接收文件] {file_name} → 保存到 {file_path}"
                except Exception as e:
                    logger.error(f"下载文件失败: {e}")

            if not text:
                return

            logger.info(f"📩 from={sender_open_id}: {text[:60]}")
            print(f"\n📩 来自 {sender_open_id}: {text}")

            token = self._get_token()

            # 0. 处理系统命令（不经过 LLM）
            cmd_reply = self._handle_command(text.strip(), token, message_id, sender_open_id)
            if cmd_reply is not None:
                if token and cmd_reply:
                    self._reply_message(token, sender_open_id, cmd_reply, message_id)
                return

            # 1. 立即发送反应 (TYPING)
            if token and message_id:
                self._send_reaction(token, message_id, "TYPING")

            # 2. 调用 on_message 回调
            msg_info = {"sender_id": sender_open_id, "chat_id": chat_id,
                        "text": text, "message_id": message_id}
            if self.on_message:
                try:
                    user_reply = self.on_message(msg_info)
                    if user_reply:
                        if token:
                            self._reply_message(token, sender_open_id, user_reply, message_id)
                        return
                except Exception:
                    pass

            # 3. 用 LLM 自动回复
            if token:
                threading.Thread(target=self._auto_reply,
                    args=(token, sender_open_id, text, message_id), daemon=True).start()

        except Exception as e:
            logger.error(f"处理消息失败: {e}")
            print(f"  ❌ 处理消息失败: {e}")

    def _auto_reply(self, token: str, user_id: str, text: str, message_id: str):
        """自动用 LLM 回复消息（支持工具调用）. """
        try:
            from agent_loop import run_agent
            reply = run_agent(text)
            if not reply:
                reply = "收到你的消息了！"
            reply = reply[:2000]
            self._reply_message(token, user_id, reply, message_id)
            print(f"  ✅ 已回复: {reply[:80]}...")
        except Exception as e:
            logger.error(f"自动回复失败: {e}")
            print(f"  ❌ 自动回复失败: {e}")

    def _build_event_handler(self):
        """构建事件处理器 — 返回简单 dict 而非 Response 类."""
        def p2_handler(*args, **kwargs):
            event = args[-1] if args else kwargs.get("event")
            if event is None:
                return {"code": 0, "msg": "ok"}
            threading.Thread(target=self._handle_message, args=(event,), daemon=True).start()
            return {"code": 0, "msg": "ok"}

        handler = EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(p2_handler) \
            .build()
        return handler

    def start(self):
        if not FEISHU_AVAILABLE:
            logger.error("lark_oapi 未安装")
            return

        def _run():
            self._running = True
            event_handler = self._build_event_handler()

            saved_proxies = {}
            for key in ['HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy',
                        'ALL_PROXY','all_proxy','SOCKS_PROXY','socks_proxy']:
                saved_proxies[key] = os.environ.pop(key, None)

            self.ws_client = FeishuWSClient(
                app_id=self.app_id,
                app_secret=self.app_secret,
                log_level=lark.LogLevel.INFO,
                event_handler=event_handler,
                domain="https://open.feishu.cn",
                auto_reconnect=True,
            )

            print("🔄 飞书 WebSocket 长连接已启动，等待消息...")
            print("   按 Ctrl+C 停止")

            import lark_oapi.ws.client as ws_client_module
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            ws_client_module.loop = loop

            try:
                self.ws_client.start()
            except Exception as e:
                logger.error(f"WebSocket 异常: {e}")
            finally:
                for key, val in saved_proxies.items():
                    if val is not None:
                        os.environ[key] = val
                self._running = False

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        logger.info("飞书 WebSocket 已停止")

    def send_message(self, receive_id: str, text: str,
                     receive_id_type: str = "open_id") -> bool:
        token = self._get_token()
        if not token:
            return False
        return send_message_http(token, receive_id, text, receive_id_type)


# ============================================================
# Webhook 模式
# ============================================================

def run_feishu_webhook(config: Optional[Dict] = None, port: int = 8080,
                       on_message: Optional[Callable] = None):
    print(f"🚀 飞书 Webhook 模式启动 (端口: {port})...")
    try:
        from flask import Flask, request, jsonify
        app_id, app_secret = load_feishu_config(config)
        app = Flask(__name__)

        @app.route("/feishu/webhook", methods=["POST"])
        def webhook():
            data = request.get_json()
            challenge = data.get("challenge")
            if challenge:
                return jsonify({"challenge": challenge})
            event = data.get("event", {})
            message = event.get("message", {})
            if message:
                content_str = message.get("content", "{}")
                sender = event.get("sender", {}).get("sender_id", {})
                sender_open_id = sender.get("open_id", "")
                text = ""
                try:
                    content = json.loads(content_str)
                    text = content.get("text", "")
                except Exception:
                    pass
                if text and on_message:
                    on_message({"sender_id": sender_open_id, "text": text})
            return jsonify({"success": True})

        app.run(host="0.0.0.0", port=port, debug=False)
    except ImportError:
        print("⚠️ Flask未安装")


# ============================================================
# 统一入口
# ============================================================

def run_feishu(config: Optional[Dict] = None, mode: str = "websocket",
               port: int = 8080, on_message: Optional[Callable] = None):
    app_id, app_secret = load_feishu_config(config)
    if not app_id or not app_secret:
        print("❌ 未配置飞书 APP ID/Secret")
        return

    if mode == "websocket":
        handler = FeishuWSHandler(app_id, app_secret, on_message)
        try:
            handler.start()
            while handler._running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n👋 正在停止飞书网关...")
            handler.stop()
    elif mode == "webhook":
        run_feishu_webhook(config, port, on_message)
    else:
        print(f"❌ 未知模式: {mode}")


if __name__ == "__main__":
    import sys
    mode = "websocket" if len(sys.argv) < 2 else sys.argv[1]

    print("🤖 飞书机器人已启动 — 收到消息将自动回复")
    print("   回复逻辑: 接收消息 → 发送TYPING反应 → LLM处理 → 发送回复")
    print()

    run_feishu(mode=mode)
