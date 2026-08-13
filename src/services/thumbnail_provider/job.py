# coding=utf-8
"""异步缩略图生成任务。"""

import logging

from blob_storage import BlobFileEntity, BlobStorage
from PyQt6.QtCore import QRunnable, QSize
from PyQt6.QtGui import QImage, QImageReader

from thumbnail_disk_storage import ThumbnailDiskStorage
from utils.safe_image_reader import (
    SafeImageReadError,
    generate_thumbnail_safe,
    pil_to_qimage,
)

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

        image, _source_size, save_disk = self._read_image(file_hash, file_path)
        if image is None:
            logger.warning("无法读取图片：%s", file_hash)
            self._provider._thumbnail_failed.emit(file_hash)
            return

        if save_disk and self._disk_storage is not None:
            if not self._save_to_disk(image, file_hash):
                # 正常路径保存失败（例如 Qt 把损坏的 ICC profile 带进 PNG 输出）：
                # 改用 safe_image_reader 兜底读取（sRGB、无色彩空间数据）再保存一次。
                fallback_image, _fallback_size, _fallback_save = self._read_fallback(
                    file_hash, file_path
                )
                if (
                    fallback_image is None
                    or not self._save_to_disk(fallback_image, file_hash)
                ):
                    self._provider._thumbnail_failed.emit(file_hash)
                    return
                image = fallback_image

        self._provider._thumbnail_generated.emit(file_hash, image)

    def _read_image(
        self,
        file_hash: str,
        file_path: str,
    ) -> tuple[QImage | None, QSize | None, bool]:
        """读取并生成缩略图。

        优先走 Qt 正常路径；Qt 无法读取时用 utils.safe_image_reader 兜底。
        正常读取的数据原样保存，不做色彩空间转换，避免偏色；sRGB 只在兜底
        读取时作为自定义参数使用。

        Returns:
            (缩略图, 源图尺寸, 是否应写磁盘缓存)。读取失败时返回 (None, None, False)。
        """
        reader = QImageReader(file_path)
        reader.setAutoTransform(True)
        # 从文件内容识别真实格式，而不是依赖扩展名
        reader.setDecideFormatFromContent(True)
        source_size = reader.size()
        if not source_size.isValid():
            return self._read_fallback(file_hash, file_path)

        image = self._read_scaled(reader, source_size)
        if image.isNull():
            return self._read_fallback(file_hash, file_path)

        save_disk = not (
            source_size.width() <= self._skip_threshold
            and source_size.height() <= self._skip_threshold
        )
        return image, source_size, save_disk

    def _read_scaled(self, reader: QImageReader, source_size: QSize) -> QImage:
        """按 Qt 的缩放读取策略解码原图（小图原样读，大图先按比例缩小）。"""
        if (
            source_size.width() <= self._skip_threshold
            and source_size.height() <= self._skip_threshold
        ):
            return reader.read()
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
        return reader.read()

    def _read_fallback(
        self,
        file_hash: str,
        file_path: str,
    ) -> tuple[QImage | None, QSize | None, bool]:
        """Qt 正常路径失败时，用 safe_image_reader 兜底读取并生成缩略图。

        兜底结果统一写磁盘缓存，避免每次缓存淘汰后重复走慢速兜底路径。
        """
        try:
            result = generate_thumbnail_safe(file_path, self._thumbnail_size)
        except SafeImageReadError as exc:
            logger.warning("安全读取图片失败：%s（%s）", file_hash, exc)
            return None, None, False
        image = pil_to_qimage(result.image)
        if image.isNull():
            logger.warning("安全读取的图片无法转换为 QImage：%s", file_hash)
            return None, None, False
        if result.used_fallback:
            logger.info(
                "图片已通过兜底参数读取：%s（%s）",
                file_hash,
                "；".join(result.warnings),
            )
        return image, QSize(image.width(), image.height()), True

    def _save_to_disk(self, image: QImage, file_hash: str) -> bool:
        """保存缩略图到磁盘；失败时清理半成品文件并返回 False。"""
        if self._disk_storage is None:
            return True
        try:
            self._disk_storage.save_image(image, file_hash)
            return True
        except Exception:
            logger.exception("保存缩略图到磁盘失败：%s", file_hash)
            try:
                self._disk_storage.delete_file(file_hash)
            except FileNotFoundError:
                pass
            except Exception:
                logger.exception("删除缩略图半成品失败：%s", file_hash)
            return False