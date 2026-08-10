# coding=utf-8
"""缩略图生成服务：内存 LRU 缓存 + 磁盘分桶缓存 + 异步按需生成。"""

from services.thumbnail_provider.provider import ThumbnailProvider

__all__ = ["ThumbnailProvider"]
