# coding=utf-8
"""缩略图生成服务：内存 LRU 缓存 + 磁盘分桶缓存 + 异步按需生成。"""

import logging
import os
from collections import OrderedDict

import apppath
from blob_storage import BlobFileEntity, BlobStorage
from PyQt6.QtCore import (
    QObject,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QColor, QImage, QImageReader, QPainter, QPixmap

import commons.constants
import services.global_instances
from thumbnail_disk_storage import ThumbnailDiskStorage

logger = logging.getLogger(__name__)


class _ThumbnailGenerationJob(QRunnable):
    """在 QThreadPool 中生成一张缩略图，完成后通过信号回传。"""

    def __init__(
        self,
        provider: "ThumbnailProvider",
        blob_entity: BlobFileEntity,
        blob_storage: BlobStorage,
        disk_storage: ThumbnailDiskStorage | None,
        thumbnail_size: int,
        skip_threshold: int,
    ):
        super().__init__()
        self._provider = provider
        self._blob_entity = blob_entity
        self._blob_storage = blob_storage
        self._disk_storage = disk_storage
        self._thumbnail_size = thumbnail_size
        self._skip_threshold = skip_threshold

    def run(self) -> None:
        file_hash = self._blob_entity.hash
        try:
            file_path = self._blob_storage.read_file(self._blob_entity)
        except Exception:
            logger.exception("异步读取 Blob 失败：%s", file_hash)
            self._provider._thumbnail_failed.emit(file_hash)
            return

        reader = QImageReader(file_path)
        reader.setAutoTransform(True)
        source_size = reader.size()
        if not source_size.isValid():
            logger.warning("无法读取图片尺寸：%s", file_hash)
            self._provider._thumbnail_failed.emit(file_hash)
            return

        save_disk = False
        if (
            source_size.width() <= self._skip_threshold
            and source_size.height() <= self._skip_threshold
        ):
            image = reader.read()
        else:
            scale = min(
                self._thumbnail_size / source_size.width(),
                self._thumbnail_size / source_size.height(),
            )
            reader.setScaledSize(
                QSize(
                    max(1, int(source_size.width() * scale)),
                    max(1, int(source_size.height() * scale)),
                )
            )
            image = reader.read()
            save_disk = True

        if image.isNull():
            logger.warning("缩略图解码失败：%s", file_hash)
            self._provider._thumbnail_failed.emit(file_hash)
            return

        if save_disk and self._disk_storage is not None:
            try:
                self._disk_storage.save_image(image, file_hash)
            except Exception:
                logger.exception("异步保存缩略图到磁盘失败：%s", file_hash)

        self._provider._thumbnail_generated.emit(file_hash, image)


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
        self._max_cache_size = max(1, max_cache_size)
        self._memory_cache: OrderedDict[str, QPixmap] = OrderedDict()
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

        disk_pixmap = self._load_disk_thumbnail(disk_storage, file_hash)
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

        disk_pixmap = self._load_disk_thumbnail(disk_storage, file_hash)
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
            except OSError:
                # 文件可能正被工作线程写入或由其他进程占用，稍后会被覆盖。
                logger.warning("删除损坏的缩略图失败（文件可能正被占用）：%s", file_hash)
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

    def _get_placeholder(self) -> QPixmap:
        """返回共享占位图（首次使用时构建并缓存）。"""
        if self._placeholder is None:
            self._placeholder = self._build_placeholder()
        return self._placeholder

    def _build_placeholder(self) -> QPixmap:
        """绘制浅灰圆角背景 + Windows 图片文件图标的占位图。"""
        size = self.THUMBNAIL_SIZE
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0xEC, 0xEC, 0xEC))
            painter.drawRoundedRect(0, 0, size, size, 12, 12)

            icon = self._load_placeholder_icon()
            if not icon.isNull():
                icon_size = int(size * 0.5)
                scaled_icon = icon.scaled(
                    icon_size,
                    icon_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                icon_rect = scaled_icon.rect()
                icon_rect.moveCenter(pixmap.rect().center())
                painter.drawPixmap(icon_rect, scaled_icon)
        finally:
            painter.end()
        return pixmap

    def _load_placeholder_icon(self) -> QPixmap:
        """从应用资源加载 Windows 图片文件图标；失败时返回空 QPixmap。"""
        try:
            if apppath.app_path is not None:
                icon_path = (
                    apppath.app_path / "resources" / "thumbnail_placeholder.png"
                )
                pixmap = QPixmap(str(icon_path))
                if not pixmap.isNull():
                    return pixmap
        except Exception:
            logger.exception("加载占位图资源失败")
        return QPixmap()
