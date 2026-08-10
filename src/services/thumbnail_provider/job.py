# coding=utf-8
"""异步缩略图生成任务。"""

import logging

from blob_storage import BlobFileEntity, BlobStorage
from PyQt6.QtCore import QRunnable, QSize
from PyQt6.QtGui import QImageReader

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
