# coding=utf-8
import logging

from PyQt6 import uic
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QDialog, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

import apppath

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_TITLE = "图片查看器"


class ImageViewerDialog(QDialog):
    """
    图片查看器对话框。

    显示一张图片并随窗口大小缩放，完整展示图片内容。
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        ui_file_path = apppath.app_path / 'ui' / 'dialog_image_viewer.ui'
        uic.loadUi(ui_file_path, self)

        # 标签编辑器是后续规划的功能，本次先隐藏
        self.widgetTagEditor.hide()

        self._init_image_viewer()

    def _init_image_viewer(self):
        self._scene = QGraphicsScene(self)
        self._image_item = QGraphicsPixmapItem()
        self._scene.addItem(self._image_item)

        self._image_view = QGraphicsView(self._scene, self.widgetImageViewer)
        self._image_view.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform)
        self._image_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.widgetImageViewer.layout().addWidget(self._image_view)

    def load_image(self, file_path: str, title: str = ""):
        """
        加载并显示图片。

        :param file_path: 图片文件路径
        :param title: 窗口标题中显示的图片名称，为空时使用默认标题
        """
        pixmap = QPixmap(file_path)
        self._image_item.setPixmap(pixmap)
        if pixmap.isNull():
            logger.warning("无法加载图片: %s", file_path)

        if title:
            self.setWindowTitle(f"{title} - {DEFAULT_WINDOW_TITLE}")
        else:
            self.setWindowTitle(DEFAULT_WINDOW_TITLE)

        # 等窗口完成布局后再把图片适配到视图大小
        QTimer.singleShot(0, self._fit_image)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_image()

    def _fit_image(self):
        if self._image_item.pixmap().isNull():
            return
        self._image_view.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)
