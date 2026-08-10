# coding=utf-8
"""缩略图服务主类：内存 LRU 缓存 + 磁盘分桶缓存 + 异步按需生成。"""

import logging
import os
from collections import OrderedDict

from blob_storage import BlobFileEntity, BlobStorage
from PyQt6.QtCore import QObject, QSize, Qt, QThreadPool, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage, QImageReader, QPixmap

import commons.constants
import services.global_instances
from thumbnail_disk_storage import ThumbnailDiskStorage

from services.thumbnail_provider.cache import (
    MemoryThumbnailCache,
    load_disk_thumbnail,
)
from services.thumbnail_provider.job import _ThumbnailGenerationJob
from services.thumbnail_provider.placeholder import (
    build_placeholder,
    load_placeholder_icon,
)

logger = logging.getLogger(__name__)


class ThumbnailProvider(QObject):
    """生成最长边为 THUMBNAIL_SIZE 的缩略图。

    查找顺序：内存 LRU -> 磁盘缓存 -> 从 Blob 生成。
    原图长宽均不超过 THUMBNAIL_SKIP_THRESHOLD 时直接返回原图（只进内存缓存，
    磁盘缓存只保存真正生成的缩略图）。磁盘缓存被删除后会自动按需重新生成。

    同步接口 get_thumbnail() 保持原有行为，始终同步生成；
    异步接口 request_thumbnail() 对需要生成磁盘缩略图的大图先返回占位图并后台
    排队，完成后通过 thumbnail_ready 信号通知，调用方重绘即可。
    """

    THUMBNAIL_SIZE = commons.constants.THUMBNAIL_SIZE
    THUMBNAIL_SKIP_THRESHOLD = commons.constants.THUMBNAIL_SKIP_THRESHOLD
    MAX_ASYNC_WORKERS = 4
    MAX_FAILED_COUNT = 512

    # 内部信号：工作线程生成完成后发出，接收槽运行在 Provider 所在（UI）线程。
    _thumbnail_generated = pyqtSignal(str, QImage)
    _thumbnail_failed = pyqtSignal(str)
    # 公开信号：缩略图已生成并写入内存缓存，携带 blob hash 与 QImage。
    thumbnail_ready = pyqtSignal(str, QImage)

    def __init__(
        self,
        blob_storage: BlobStorage | None = None,
        disk_storage: ThumbnailDiskStorage | None = None,
        max_cache_size: int = commons.constants.THUMBNAIL_CACHE_MAX_COUNT,
        parent: QObject | None = None,
    ):
        """初始化缩略图服务。

        Args:
            blob_storage: Blob 存储，缺省时使用全局实例。
            disk_storage: 缩略图磁盘缓存，缺省时使用全局实例。
            max_cache_size: 内存 LRU 最大条目数。
            parent: QObject 父对象。
        """
        super().__init__(parent)
        self._blob_storage = blob_storage
        self._disk_storage = disk_storage
        self._memory_cache = MemoryThumbnailCache(max_cache_size)
        self._pool: QThreadPool | None = None
        self._in_flight: set[str] = set()
        self._failed_hashes: OrderedDict[str, None] = OrderedDict()
        self._placeholder: QPixmap | None = None
        self._thumbnail_generated.connect(self._on_thumbnail_generated)
        self._thumbnail_failed.connect(self._on_thumbnail_failed)

    def get_thumbnail(self, blob_entity: BlobFileEntity) -> QPixmap:
        """同步返回指定 Blob 图片的缩略图（始终阻塞直到生成完成）。"""
        disk_storage = self._get_disk_storage()
        cached = self._load_cached(blob_entity, disk_storage)
        if cached is not None:
            return cached

        blob_storage = self._get_blob_storage()
        if blob_storage is None:
            return QPixmap()
        try:
            file_path = blob_storage.read_file(blob_entity)
        except FileNotFoundError:
            return QPixmap()
        return self._generate_sync(blob_entity, file_path, disk_storage)

    def request_thumbnail(self, blob_entity: BlobFileEntity) -> QPixmap:
        """返回当前可用的缩略图；大图未缓存时先返回占位图并后台生成。

        生成完成后 thumbnail_ready 信号会携带 blob hash 发出，调用方据此重绘。
        """
        disk_storage = self._get_disk_storage()
        file_hash = blob_entity.hash

        cached = self._memory_cache.get(file_hash)
        if cached is not None:
            self._memory_cache.move_to_end(file_hash)
            return cached

        # 任务进行中或已失败时不再读磁盘：worker 可能正在写缓存文件，
        # 此时读盘会撞上半截文件甚至触发删除占用文件的 PermissionError。
        if file_hash in self._in_flight or file_hash in self._failed_hashes:
            return self._get_placeholder()

        disk_pixmap = load_disk_thumbnail(disk_storage, file_hash)
        if disk_pixmap is not None:
            return self._store_in_memory(file_hash, disk_pixmap)

        blob_storage = self._get_blob_storage()
        if blob_storage is None:
            return QPixmap()
        try:
            file_path = blob_storage.read_file(blob_entity)
        except FileNotFoundError:
            return QPixmap()

        reader = QImageReader(file_path)
        source_size = reader.size()
        if not source_size.isValid():
            return QPixmap()
        if (
            source_size.width() <= self.THUMBNAIL_SKIP_THRESHOLD
            and source_size.height() <= self.THUMBNAIL_SKIP_THRESHOLD
        ):
            return self._generate_sync(blob_entity, file_path, disk_storage)

        self._start_job(blob_entity, blob_storage, disk_storage)
        return self._get_placeholder()

    def clear_memory_cache(self) -> None:
        """清空内存缓存与任务状态（删除磁盘缓存后由维护功能调用）。"""
        self._memory_cache.clear()
        self._in_flight.clear()
        self._failed_hashes.clear()
        if self._pool is not None:
            self._pool.clear()

    @pyqtSlot(str, QImage)
    def _on_thumbnail_generated(self, file_hash: str, image: QImage) -> None:
        self._in_flight.discard(file_hash)
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return
        self._store_in_memory(file_hash, pixmap)
        self.thumbnail_ready.emit(file_hash, image)

    @pyqtSlot(str)
    def _on_thumbnail_failed(self, file_hash: str) -> None:
        self._in_flight.discard(file_hash)
        self._failed_hashes[file_hash] = None
        self._failed_hashes.move_to_end(file_hash)
        if len(self._failed_hashes) > self.MAX_FAILED_COUNT:
            self._failed_hashes.popitem(last=False)

    def _get_blob_storage(self) -> BlobStorage | None:
        return self._blob_storage or services.global_instances.current_blob_storage

    def _get_disk_storage(self) -> ThumbnailDiskStorage | None:
        return (
            self._disk_storage
            or services.global_instances.current_thumbnail_disk_storage
        )

    def _load_cached(
        self,
        blob_entity: BlobFileEntity,
        disk_storage: ThumbnailDiskStorage | None,
    ) -> QPixmap | None:
        """按 内存 LRU -> 磁盘缓存 的顺序查找，命中即返回。"""
        file_hash = blob_entity.hash
        cached = self._memory_cache.get(file_hash)
        if cached is not None:
            self._memory_cache.move_to_end(file_hash)
            return cached

        disk_pixmap = load_disk_thumbnail(disk_storage, file_hash)
        if disk_pixmap is not None:
            return self._store_in_memory(file_hash, disk_pixmap)
        return None

    def _generate_sync(
        self,
        blob_entity: BlobFileEntity,
        file_path: str,
        disk_storage: ThumbnailDiskStorage | None,
    ) -> QPixmap:
        """同步解码原图并生成缩略图，写磁盘缓存后存入内存缓存。"""
        file_hash = blob_entity.hash
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

    def _start_job(
        self,
        blob_entity: BlobFileEntity,
        blob_storage: BlobStorage,
        disk_storage: ThumbnailDiskStorage | None,
    ) -> None:
        self._in_flight.add(blob_entity.hash)
        job = _ThumbnailGenerationJob(
            self,
            blob_entity,
            blob_storage,
            disk_storage,
            self.THUMBNAIL_SIZE,
            self.THUMBNAIL_SKIP_THRESHOLD,
        )
        self._ensure_pool().start(job)

    def _ensure_pool(self) -> QThreadPool:
        if self._pool is None:
            self._pool = QThreadPool(self)
            max_workers = min(self.MAX_ASYNC_WORKERS, os.cpu_count() or 1)
            self._pool.setMaxThreadCount(max_workers)
        return self._pool

    def _store_in_memory(self, file_hash: str, pixmap: QPixmap) -> QPixmap:
        """按 LRU 规则写入内存缓存并返回。"""
        return self._memory_cache.store(file_hash, pixmap)

    def _get_placeholder(self) -> QPixmap:
        """返回共享占位图（首次使用时构建并缓存）。"""
        if self._placeholder is None:
            self._placeholder = self._build_placeholder()
        return self._placeholder

    def _build_placeholder(self) -> QPixmap:
        return build_placeholder(self.THUMBNAIL_SIZE)

    def _load_placeholder_icon(self) -> QPixmap:
        return load_placeholder_icon()
