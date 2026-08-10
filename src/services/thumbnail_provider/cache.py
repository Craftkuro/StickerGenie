# coding=utf-8
"""缩略图缓存：内存 LRU + 磁盘分桶缓存读取。"""

import logging
from collections import OrderedDict

from PyQt6.QtGui import QPixmap

from thumbnail_disk_storage import ThumbnailDiskStorage

logger = logging.getLogger(__name__)


class MemoryThumbnailCache(OrderedDict[str, QPixmap]):
    """带最大条目数限制的 LRU 内存缓存。"""

    def __init__(self, max_size: int):
        super().__init__()
        self._max_size = max(1, max_size)

    def store(self, file_hash: str, pixmap: QPixmap) -> QPixmap:
        """写入缓存并返回；缓存已满时淘汰最久未使用的条目。"""
        if file_hash in self:
            self.move_to_end(file_hash)
        else:
            if len(self) >= self._max_size:
                self.popitem(last=False)
            self[file_hash] = pixmap
        return pixmap


def load_disk_thumbnail(
    disk_storage: ThumbnailDiskStorage | None,
    file_hash: str,
) -> QPixmap | None:
    """从磁盘缓存加载缩略图；缺失或损坏时返回 None。"""
    if disk_storage is None:
        return None
    try:
        file_path = disk_storage.read_file(file_hash)
    except FileNotFoundError:
        return None

    pixmap = QPixmap(file_path)
    if pixmap.isNull():
        try:
            disk_storage.delete_file(file_hash)
        except OSError:
            # 文件可能正被工作线程写入或由其他进程占用，稍后会被覆盖。
            logger.warning("删除损坏的缩略图失败（文件可能正被占用）：%s", file_hash)
        except Exception:
            logger.exception("删除损坏的缩略图失败：%s", file_hash)
        return None
    return pixmap
