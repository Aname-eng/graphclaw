#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
飞书网关 - 集成飞书开放 API
支持消息发送、文档创建、文件上传
"""

import os
import json
import time
import requests
from typing import Dict, Optional, Callable, Any


class FeishuGateway:
    """飞书网关"""

    def __init__(self, app_id: Optional[str] = None, app_secret: Optional[str] = None):
        self.app_id = app_id or os.environ.get("FEISHU_APP_ID")
        self.app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET")
        self._token_cache: Optional[Dict[str, Any]] = None
        self._token_expire_time = 0

    def _get_access_token(self) -> Optional[str]:
        """获取 tenant_access_token"""
        # 检查缓存
        if self._token_cache and time.time() < self._token_expire_time - 60:
            return self._token_cache.get("tenant_access_token")

        if not self.app_id or not self.app_secret:
            print("⚠️ 飞书配置缺失，请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
            return None

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            result = response.json()

            if result.get("code") == 0:
                self._token_cache = result
                self._token_expire_time = time.time() + result.get("expire", 7200)
                return result.get("tenant_access_token")
            else:
                print(f"❌ 获取飞书token失败: {result.get('msg')}")
                return None
        except Exception as e:
            print(f"❌ 飞书请求失败: {e}")
            return None

    def send_message(self, receive_id: str, content: str, msg_type: str = "text") -> bool:
        """发送消息"""
        token = self._get_access_token()
        if not token:
            return False

        url = "https://open.feishu.cn/open-apis/im/v1/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        params = {"receive_id_type": "open_id"}

        if msg_type == "text":
            message_content = json.dumps({"text": content}, ensure_ascii=False)
        else:
            message_content = content

        payload = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": message_content
        }

        try:
            response = requests.post(url, headers=headers, params=params, json=payload, timeout=10)
            result = response.json()
            return result.get("code") == 0
        except Exception as e:
            print(f"❌ 发送消息失败: {e}")
            return False

    def create_document(self, title: str, content: Optional[str] = None) -> Optional[str]:
        """创建文档"""
        token = self._get_access_token()
        if not token:
            return None

        url = "https://open.feishu.cn/open-apis/docx/v1/documents"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        payload = {"title": title}
        if content:
            payload["content"] = content

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            result = response.json()
            if result.get("code") == 0:
                return result.get("data", {}).get("document", {}).get("document_id")
            return None
        except Exception as e:
            print(f"❌ 创建文档失败: {e}")
            return None

    def upload_file(self, file_path: str) -> Optional[str]:
        """上传文件"""
        token = self._get_access_token()
        if not token or not os.path.exists(file_path):
            return None

        url = "https://open.feishu.cn/open-apis/drive/v1/files/upload_all"
        headers = {"Authorization": f"Bearer {token}"}

        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        try:
            with open(file_path, "rb") as f:
                files = {"file": (file_name, f)}
                data = {
                    "file_name": file_name,
                    "parent_type": "explorer",
                    "size": file_size
                }
                response = requests.post(url, headers=headers, data=data, files=files, timeout=30)
                result = response.json()
                if result.get("code") == 0:
                    return result.get("data", {}).get("file_token")
                return None
        except Exception as e:
            print(f"❌ 上传文件失败: {e}")
            return None

    def is_configured(self) -> bool:
        """检查是否已配置"""
        return bool(self.app_id and self.app_secret)


def create_feishu_gateway(app_id: Optional[str] = None, app_secret: Optional[str] = None) -> FeishuGateway:
    """创建飞书网关实例"""
    return FeishuGateway(app_id, app_secret)
