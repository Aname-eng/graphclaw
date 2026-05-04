"""飞书文件收发 — 支持接收和发送文件."""
import os
import json
import requests
from pathlib import Path

FEISHU_DOMAIN = "https://open.feishu.cn"


def download_file(token: str, message_id: str, file_key: str, 
                  file_type: str = "file", output_dir: str = "feishu_files") -> str:
    """从飞书消息中下载文件.

    Args:
        token: tenant_access_token
        message_id: 消息 ID
        file_key: 文件 key
        file_type: 文件类型 (file/image/audio/media)
        output_dir: 输出目录

    Returns:
        保存的文件路径
    """
    url = f"{FEISHU_DOMAIN}/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type={file_type}"
    headers = {"Authorization": f"Bearer {token}"}

    os.makedirs(output_dir, exist_ok=True)
    resp = requests.get(url, headers=headers, stream=True, timeout=30)
    if resp.status_code != 200:
        raise Exception(f"下载文件失败: {resp.status_code} {resp.text[:200]}")

    # 从 Content-Disposition 或 URL 中提取文件名
    filename = f"{file_key}.bin"
    cd = resp.headers.get("Content-Disposition", "")
    if cd:
        import re
        m = re.search(r'filename\*?=(?:UTF-8\'\')?([^;\s]+)', cd)
        if m:
            filename = m.group(1).strip("'\"")
    if not filename or filename == file_key:
        # 根据 file_type 设置扩展名
        ext_map = {"image": "png", "file": "bin", "audio": "ogg", "media": "mp4"}
        filename = f"{file_key}.{ext_map.get(file_type, 'bin')}"

    filepath = os.path.join(output_dir, filename)
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    return filepath


def upload_file(token: str, file_path: str, file_type: str = "stream") -> str:
    """上传文件到飞书.

    Args:
        token: tenant_access_token
        file_path: 本地文件路径
        file_type: 文件类型 (stream/image/file/audio/media)

    Returns:
        file_key (后续用于发送消息)
    """
    url = f"{FEISHU_DOMAIN}/open-apis/im/v1/files"
    headers = {"Authorization": f"Bearer {token}"}

    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)

    with open(file_path, "rb") as f:
        resp = requests.post(url, headers=headers, files={
            "file": (file_name, f, "application/octet-stream"),
        }, data={
            "file_name": file_name,
            "file_type": file_type,
            "file_size": str(file_size),
        }, timeout=60)

    result = resp.json()
    if result.get("code") != 0:
        raise Exception(f"上传文件失败: {result.get('msg', '')}")

    return result["data"]["file_key"]


def send_file_message(token: str, receive_id: str, file_key: str,
                      file_name: str = "", receive_id_type: str = "open_id") -> bool:
    """发送文件消息.

    Args:
        token: tenant_access_token
        receive_id: 接收者 ID
        file_key: 上传后获得的 file_key
        file_name: 文件名
        receive_id_type: open_id / user_id / chat_id

    Returns:
        是否成功
    """
    url = f"{FEISHU_DOMAIN}/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    content = {"file_key": file_key}
    if file_name:
        content["file_name"] = file_name
    payload = {
        "receive_id": receive_id,
        "msg_type": "file",
        "content": json.dumps(content),
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    result = resp.json()
    return result.get("code") == 0


def handle_file_message(token: str, message_id: str, file_key: str,
                        file_name: str = "", output_dir: str = "feishu_files") -> str:
    """处理接收到的文件消息：下载并返回路径."""
    try:
        filepath = download_file(token, message_id, file_key, "file", output_dir)
        # 重命名为原始文件名
        if file_name:
            ext = os.path.splitext(filepath)[1]
            new_path = os.path.join(output_dir, file_name)
            if not os.path.exists(new_path):
                os.rename(filepath, new_path)
                filepath = new_path
        return filepath
    except Exception as e:
        raise Exception(f"处理文件消息失败: {e}")
