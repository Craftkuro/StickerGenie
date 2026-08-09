# coding=utf-8
"""缩略图生成服务。当前只负责按原始宽高比缩放，不做内存或磁盘缓存。"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap


class ThumbnailProvider:
    """生成最长边为 144 像素的缩略图。"""

    THUMBNAIL_SIZE = 144

    def get_thumbnail(self, file_path: str) -> QPixmap:
        source = QPixmap(file_path)
        if source.isNull():
            return QPixmap()

        return source.scaled(
            self.THUMBNAIL_SIZE,
            self.THUMBNAIL_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
