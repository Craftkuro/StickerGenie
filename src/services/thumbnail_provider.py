# coding=utf-8
"""缩略图生成服务。当前只负责按原始宽高比缩放，不做内存或磁盘缓存。"""

from blob_storage import BlobFileEntity, BlobStorage
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap

import services.global_instances


class ThumbnailProvider:
    """生成最长边为 144 像素的缩略图。"""

    THUMBNAIL_SIZE = 144

    def __init__(self, blob_storage: BlobStorage | None = None):
        self._blob_storage = blob_storage

    def get_thumbnail(self, blob_entity: BlobFileEntity) -> QPixmap:
        blob_storage = (
            self._blob_storage
            or services.global_instances.current_blob_storage
        )
        if blob_storage is None:
            return QPixmap()

        try:
            file_path = blob_storage.read_file(blob_entity)
        except FileNotFoundError:
            return QPixmap()

        source = QPixmap(file_path)
        if source.isNull():
            return QPixmap()

        return source.scaled(
            self.THUMBNAIL_SIZE,
            self.THUMBNAIL_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
