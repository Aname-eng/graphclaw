
from __future__ import annotations

import requests
import os
import json
import time
from typing import Any, Callable, Optional, Dict

from tools.registry import registry, tool_result, tool_error
from tools.utils import redact_sensitive_text


_tenant_token_cache: Optional[Dict[str, Any]] = None


def _get_tenant_access_token() -> Dict[str, Any]:
    global _tenant_token_cache

    if _tenant_token_cache and _tenant_token_cache["expire_at"] > time.time() - 60:
        return tool_result(_tenant_token_cache)

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")

    if not app_id or not app_secret:
        return tool_error("FEISHU_CONFIG_ERROR", "FEISHU_APP_ID or FEISHU_APP_SECRET not set")

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": app_id, "app_secret": app_secret}

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        result = response.json()

        if result.get("code") != 0:
            return tool_error("FEISHU_TOKEN_ERROR", f"Failed to get token: {result.get('msg')}")

        _tenant_token_cache = {
            "access_token": result["tenant_access_token"],
            "expire_at": time.time() + result["expire"]
        }

        return tool_result(_tenant_token_cache)
    except requests.exceptions.RequestException as e:
        return tool_error("FEISHU_REQUEST_ERROR", f"Network error: {e}")


def feishu_send_message(
    receive_id: str,
    msg_type: str,
    content: dict,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> Dict[str, Any]:
    try:
        token_result = _get_tenant_access_token()
        if not token_result["success"]:
            return token_result

        access_token = token_result["data"]["access_token"]

        url = "https://open.feishu.cn/open-apis/im/v1/messages"
        params = {"receive_id_type": "open_id"}
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False)
        }

        if on_log:
            on_log(f"Sending Feishu message to {receive_id}...")

        response = requests.post(url, params=params, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        if result.get("code") != 0:
            return tool_error("FEISHU_SEND_ERROR", f"Failed to send: {result.get('msg')}")

        if on_log:
            on_log(f"Feishu message sent successfully")

        return tool_result({"message_id": result["data"]["message_id"], "status": "sent"})
    except Exception as e:
        return tool_error("FEISHU_SEND_ERROR", f"Failed to send message: {e}")


def feishu_create_doc(
    title: str,
    folder_token: Optional[str] = None,
    content: Optional[str] = None,
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> Dict[str, Any]:
    try:
        token_result = _get_tenant_access_token()
        if not token_result["success"]:
            return token_result

        access_token = token_result["data"]["access_token"]

        url = "https://open.feishu.cn/open-apis/docx/v1/documents"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        payload = {"title": title, "folder_token": folder_token}

        if on_log:
            on_log(f"Creating Feishu doc: {title}...")

        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        if result.get("code") != 0:
            return tool_error("FEISHU_CREATE_DOC_ERROR", f"Failed: {result.get('msg')}")

        doc_token = result["data"]["document"]["document_id"]
        doc_url = f"https://feishu.cn/docx/{doc_token}"

        if on_log:
            on_log(f"Feishu doc created: {doc_url}")

        return tool_result({"doc_token": doc_token, "doc_url": doc_url, "title": title})
    except Exception as e:
        return tool_error("FEISHU_CREATE_DOC_ERROR", f"Failed: {e}")


def feishu_upload_file(
    file_path: str,
    parent_type: str = "doc",
    task_id: str = "",
    on_log: Optional[Callable[..., Any]] = None
) -> Dict[str, Any]:
    try:
        if not os.path.exists(file_path):
            return tool_error("FEISHU_FILE_NOT_FOUND", f"File not found: {file_path}")

        token_result = _get_tenant_access_token()
        if not token_result["success"]:
            return token_result

        access_token = token_result["data"]["access_token"]
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        url = "https://open.feishu.cn/open-apis/drive/v1/files/upload_all"
        headers = {"Authorization": f"Bearer {access_token}"}

        with open(file_path, "rb") as f:
            files = {"file": (file_name, f)}
            data = {"file_name": file_name, "parent_type": parent_type, "size": file_size}

            if on_log:
                on_log(f"Uploading to Feishu: {file_name}...")

            response = requests.post(url, headers=headers, data=data, files=files)
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 0:
                return tool_error("FEISHU_UPLOAD_ERROR", f"Failed: {result.get('msg')}")

            file_token = result["data"]["file_token"]

            if on_log:
                on_log(f"File uploaded: {file_token}")

            return tool_result({"file_token": file_token, "file_name": file_name, "size": file_size})
    except Exception as e:
        return tool_error("FEISHU_UPLOAD_ERROR", f"Failed: {e}")


def register_all() -> None:
    registry.register(
        name="feishu_send_message",
        handler=feishu_send_message,
        description="Send Feishu message",
        parameters={
            "type": "object",
            "properties": {
                "receive_id": {"type": "string"},
                "msg_type": {"type": "string", "enum": ["text", "post", "image", "interactive"]},
                "content": {"type": "object"},
                "task_id": {"type": "string"},
            },
            "required": ["receive_id", "msg_type", "content"],
        },
        toolset="feishu",
        emoji="💬",
    )

    registry.register(
        name="feishu_create_doc",
        handler=feishu_create_doc,
        description="Create Feishu doc",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "folder_token": {"type": "string"},
                "content": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["title"],
        },
        toolset="feishu",
        emoji="📄",
    )

    registry.register(
        name="feishu_upload_file",
        handler=feishu_upload_file,
        description="Upload file to Feishu",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "parent_type": {"type": "string", "enum": ["doc", "image", "file"], "default": "doc"},
                "task_id": {"type": "string"},
            },
            "required": ["file_path"],
        },
        toolset="feishu",
        emoji="📁",
    )


register_all()
