# coding=utf-8
"""缩略图生成服务：内存 LRU 缓存 + 磁盘分桶缓存。"""

import logging
from collections import OrderedDict

from blob_storage import BlobFileEntity, BlobStorage
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap

import commons.constants
import services.global_instances
from thumbnail_disk_storage import ThumbnailDiskStorage

logger = logging.getLogger(__name__)


class ThumbnailProvider:
    """生成最长边为 THUMBNAIL_SIZE 的缩略图。

    查找顺序：内存 LRU -> 磁盘缓存 -> 从 Blob 生成。
    原图长宽均不超过 THUMBNAIL_SKIP_THRESHOLD 时直接返回原图（只进内存缓存，
    磁盘缓存只保存真正生成的缩略图）。磁盘缓存被删除后会自动按需重新生成。
    """

    THUMBNAIL_SIZE = commons.constants.THUMBNAIL_SIZE
    THUMBNAIL_SKIP_THRESHOLD = commons.constants.THUMBNAIL_SKIP_THRESHOLD

    def __init__(
        self,
        blob_storage: BlobStorage | None = None,
        disk_storage: ThumbnailDiskStorage | None = None,
        max_cache_size: int = commons.constants.THUMBNAIL_CACHE_MAX_COUNT,
    ):
        """初始化缩略图服务。

        Args:
            blob_storage: Blob 存储，缺省时使用全局实例。
            disk_storage: 缩略图磁盘缓存，缺省时使用全局实例。
            max_cache_size: 内存 LRU 最大条目数。
        """
        self._blob_storage = blob_storage
        self._disk_storage = disk_storage
        self._max_cache_size = max(1, max_cache_size)
        self._memory_cache: OrderedDict[str, QPixmap] = OrderedDict()

    def get_thumbnail(self, blob_entity: BlobFileEntity) -> QPixmap:
        """返回指定 Blob 图片的缩略图。"""
        blob_storage = (
            self._blob_storage
            or services.global_instances.current_blob_storage
        )
        disk_storage = (
            self._disk_storage
            or services.global_instances.current_thumbnail_disk_storage
        )
        if blob_storage is None:
            return QPixmap()

        file_hash = blob_entity.hash
        cached = self._memory_cache.get(file_hash)
        if cached is not None:
            self._memory_cache.move_to_end(file_hash)
            return cached

        disk_pixmap = self._load_disk_thumbnail(disk_storage, file_hash)
        if disk_pixmap is not None:
            return self._store_in_memory(file_hash, disk_pixmap)

        try:
            file_path = blob_storage.read_file(blob_entity)
        except FileNotFoundError:
            return QPixmap()

        source = QPixmap(file_path)
        if source.isNull():
            return QPixmap()

        if (
            source.width() <= self.THUMBNAIL_SKIP_THRESHOLD
            and source.height() <= self.THUMBNAIL_SKIP_THRESHOLD
        ):
            result = source
        else:
            result = source.scaled(
                self.THUMBNAIL_SIZE,
                self.THUMBNAIL_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            if disk_storage is not None:
                try:
                    disk_storage.save_pixmap(result, file_hash)
                except Exception:
                    logger.exception("保存缩略图到磁盘失败：%s", file_hash)

        return self._store_in_memory(file_hash, result)

    def clear_memory_cache(self) -> None:
        """清空内存缓存（删除磁盘缓存后由维护功能调用）。"""
        self._memory_cache.clear()

    def _load_disk_thumbnail(
        self,
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
            except Exception:
                logger.exception("删除损坏的缩略图失败：%s", file_hash)
            return None
        return pixmap

    def _store_in_memory(self, file_hash: str, pixmap: QPixmap) -> QPixmap:
        """按 LRU 规则写入内存缓存并返回。"""
        if file_hash in self._memory_cache:
            self._memory_cache.move_to_end(file_hash)
        else:
            if len(self._memory_cache) >= self._max_cache_size:
                self._memory_cache.popitem(last=False)
            self._memory_cache[file_hash] = pixmap
        return pixmap
