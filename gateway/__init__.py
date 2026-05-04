"""
网关模块 - 支持多种消息通道
"""

from .feishu import FeishuGateway, create_feishu_gateway
from .local import LocalGateway, create_local_gateway

__all__ = [
    "FeishuGateway",
    "create_feishu_gateway",
    "LocalGateway", 
    "create_local_gateway"
]
