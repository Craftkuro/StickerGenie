# coding=utf-8
"""缩略图占位图构建。"""

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap

from utils.resource_path import resolve_resource_path

logger = logging.getLogger(__name__)


def build_placeholder(size: int) -> QPixmap:
    """绘制浅灰圆角背景 + Windows 图片文件图标的占位图。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0xEC, 0xEC, 0xEC))
        painter.drawRoundedRect(0, 0, size, size, 12, 12)

        icon = load_placeholder_icon()
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


def load_placeholder_icon() -> QPixmap:
    """从应用资源加载 Windows 图片文件图标；失败时返回空 QPixmap。"""
    try:
        pixmap = QPixmap(str(resolve_resource_path("thumbnail_placeholder.png")))
        if not pixmap.isNull():
            return pixmap
    except Exception:
        logger.exception("加载占位图资源失败")
    return QPixmap()
