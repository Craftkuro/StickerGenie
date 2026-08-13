# coding=utf-8
"""缩略图服务主类：内存 LRU 缓存 + 磁盘分桶缓存 + 异步按需生成。"""

import logging
import os
from collections import OrderedDict

from blob_storage import BlobFileEntity, BlobStorage
from PyQt6.QtCore import QObject, Qt, QThreadPool, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage, QPixmap

import commons.constants
import services.global_instances
from thumbnail_disk_storage import ThumbnailDiskStorage
from utils.safe_image_reader import (
    SafeImageReadError,
    generate_thumbnail_safe,
    pil_to_qimage,
)

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
    异步接口 request_thumbnail() 在内存未命中时（无论大小、无论磁盘缓存是否
    存在）先返回占位图并后台排队；磁盘缓存读取、原图解码与生成全部在后台
    任务中完成，paint 路径不再做任何同步 stat/解码。完成后通过
    thumbnail_ready 信号通知，调用方重绘即可。
    小图（长宽均不超过 THUMBNAIL_SKIP_THRESHOLD）在后台任务中直接复用原图，
    不写磁盘缓存。
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
        # 内存缓存只存 QPixmap；工作线程与 UI 线程之间传递的是 QImage，
        # 因 QPixmap 只能在UI线程使用。
        self._memory_cache = MemoryThumbnailCache(max_cache_size)
        # 线程池懒创建：没有异步任务时不应分配任何线程资源。
        self._pool: QThreadPool | None = None
        # 正在后台生成的 blob hash；重复请求直接复用占位图，避免重复排队。
        self._in_flight: set[str] = set()
        # 最近失败的 blob hash（LRU 上限）；避免坏图反复触发磁盘读取和解码。
        self._failed_hashes: OrderedDict[str, None] = OrderedDict()
        self._placeholder: QPixmap | None = None
        # 工作线程发出的信号默认排队到 Provider 所在线程（通常是 UI 线程），
        # 因此缓存与 thumbnail_ready 的更新都发生在同一线程，无需额外加锁。
        self._thumbnail_generated.connect(self._on_thumbnail_generated)
        self._thumbnail_failed.connect(self._on_thumbnail_failed)

    def get_thumbnail(self, blob_entity: BlobFileEntity) -> QPixmap:
        """同步返回指定 Blob 图片的缩略图（始终阻塞直到生成完成）。"""
        # 同步路径同样先查内存/磁盘缓存，命中就不需要碰 Blob。
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
            # Blob 不存在时返回空 QPixmap，由调用方决定如何展示。
            return QPixmap()
        # 缓存未命中且 Blob 存在：在调用线程内同步解码并生成。
        return self._generate_sync(blob_entity, file_path, disk_storage)

    def request_thumbnail(self, blob_entity: BlobFileEntity) -> QPixmap:
        """返回当前可用的缩略图；内存未命中时先返回占位图并后台排队。

        磁盘缓存读取与缩略图生成都在后台任务中完成；生成完成后
        thumbnail_ready 信号会携带 blob hash 发出，调用方据此重绘。
        """
        file_hash = blob_entity.hash

        # 内存命中：更新 LRU 位置后直接返回，避免重复解码。
        cached = self._memory_cache.get(file_hash)
        if cached is not None:
            self._memory_cache.move_to_end(file_hash)
            return cached

        # 任务进行中或已失败时不再重复排队。
        if file_hash in self._in_flight or file_hash in self._failed_hashes:
            return self._get_placeholder()

        blob_storage = self._get_blob_storage()
        if blob_storage is None:
            return QPixmap()

        # 内存未命中一律后台排队（含磁盘缓存读取）：paint 路径不再做任何
        # 同步 stat / 解码。任务内先查磁盘缓存，命中直接回传；未命中或损坏
        # 再从 Blob 读取并生成。
        self._start_job(blob_entity, blob_storage, self._get_disk_storage())
        return self._get_placeholder()

    def clear_memory_cache(self) -> None:
        """清空内存缓存与任务状态（删除磁盘缓存后由维护功能调用）。"""
        # 内存、任务状态一起清空，否则删除磁盘缓存后，旧 hash 仍会被
        # in-flight / failed 判定拦截，导致新请求拿不到重新生成的机会。
        self._memory_cache.clear()
        self._in_flight.clear()
        self._failed_hashes.clear()
        if self._pool is not None:
            # 只移除尚未开始的任务；已经运行的 worker 无法取消，
            # 它们完成后仍可能通过信号重新回填内存缓存。
            self._pool.clear()

    @pyqtSlot(str, QImage)
    def _on_thumbnail_generated(self, file_hash: str, image: QImage) -> None:
        # 先移除 in_flight，后续请求立刻可以命中刚写入的缓存。
        self._in_flight.discard(file_hash)
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            # QImage 转 QPixmap 失败时不发 ready，避免 UI 用空图重绘。
            return
        self._store_in_memory(file_hash, pixmap)
        # 信号携带原始 QImage：QImage 可跨线程传递，QPixmap 不应跨线程使用。
        self.thumbnail_ready.emit(file_hash, image)

    @pyqtSlot(str)
    def _on_thumbnail_failed(self, file_hash: str) -> None:
        self._in_flight.discard(file_hash)
        # 失败记录同样按 LRU 维护，防止坏图 hash 无限占用内存。
        self._failed_hashes[file_hash] = None
        self._failed_hashes.move_to_end(file_hash)
        if len(self._failed_hashes) > self.MAX_FAILED_COUNT:
            self._failed_hashes.popitem(last=False)

    def _get_blob_storage(self) -> BlobStorage | None:
        # 优先使用构造时注入的实例，否则回退到全局实例，便于测试与启动时装配。
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
            # 正常路径失败时用 safe_image_reader 兜底读取。
            source = self._load_via_safe_reader(file_hash, file_path)
            if source.isNull():
                return QPixmap()

        # 小图直接复用原图，不写磁盘；大图缩放后才写磁盘缓存。
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
                if not self._try_save_disk(result, disk_storage, file_hash):
                    # 正常路径保存失败（例如 Qt 把损坏的 ICC profile 带进 PNG 输出）：
                    # 改用 safe_image_reader 兜底读取（sRGB、无色彩空间数据）再保存。
                    fallback = self._load_via_safe_reader(file_hash, file_path)
                    if (
                        not fallback.isNull()
                        and self._try_save_disk(fallback, disk_storage, file_hash)
                    ):
                        result = fallback

        return self._store_in_memory(file_hash, result)

    def _load_via_safe_reader(self, file_hash: str, file_path: str) -> QPixmap:
        """用 utils.safe_image_reader 兜底读取并生成缩略图。"""
        try:
            result = generate_thumbnail_safe(file_path, self.THUMBNAIL_SIZE)
        except SafeImageReadError as exc:
            logger.warning("安全读取图片失败：%s（%s）", file_hash, exc)
            return QPixmap()
        pixmap = QPixmap.fromImage(pil_to_qimage(result.image))
        if result.used_fallback:
            logger.info(
                "图片已通过兜底参数读取：%s（%s）",
                file_hash,
                "；".join(result.warnings),
            )
        return pixmap

    def _try_save_disk(
        self,
        pixmap: QPixmap,
        disk_storage: ThumbnailDiskStorage | None,
        file_hash: str,
    ) -> bool:
        """把缩略图保存为 PNG（原样保存，不做色彩空间转换）；失败时清理半成品。"""
        if disk_storage is None:
            return True
        try:
            disk_storage.save_image(pixmap.toImage(), file_hash)
            return True
        except Exception:
            logger.exception("保存缩略图到磁盘失败：%s", file_hash)
            try:
                disk_storage.delete_file(file_hash)
            except FileNotFoundError:
                pass
            except Exception:
                logger.exception("删除缩略图半成品失败：%s", file_hash)
            return False

    def _start_job(
        self,
        blob_entity: BlobFileEntity,
        blob_storage: BlobStorage,
        disk_storage: ThumbnailDiskStorage | None,
    ) -> None:
        # 先登记 in_flight 再入队，避免同一 hash 的并发请求重复创建任务。
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
            # 线程数取“配置上限”和 CPU 核心数中较小的一个，防止图片库一次
            # 滚动加载时创建过多任务占满所有核心。
            max_workers = min(self.MAX_ASYNC_WORKERS, os.cpu_count() or 1)
            self._pool.setMaxThreadCount(max_workers)
        return self._pool

    def _store_in_memory(self, file_hash: str, pixmap: QPixmap) -> QPixmap:
        """按 LRU 规则写入内存缓存并返回。"""
        return self._memory_cache.store(file_hash, pixmap)

    def _get_placeholder(self) -> QPixmap:
        """返回共享占位图（首次使用时构建并缓存）。"""
        # 所有等待中的请求共享同一个只读 QPixmap，避免每个请求都重新绘制。
        if self._placeholder is None:
            self._placeholder = self._build_placeholder()
        return self._placeholder

    def _build_placeholder(self) -> QPixmap:
        return build_placeholder(self.THUMBNAIL_SIZE)

    def _load_placeholder_icon(self) -> QPixmap:
        return load_placeholder_icon()
