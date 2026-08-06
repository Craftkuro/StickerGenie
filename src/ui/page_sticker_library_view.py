#coding=utf-8
import logging

from PyQt6 import uic
from PyQt6.QtCore import pyqtSignal, QModelIndex
from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtWidgets import QWidget, QListView

import apppath
import commons.constants
#import commons.classes
import services.sticker_library_viewer_service

from .dialog_image_viewer import ImageViewerDialog

logger = logging.getLogger(__name__)

class StickerLibraryViewPage(QWidget):
    signal_refresh_content = pyqtSignal()

    def __init__(self):
        super().__init__()

        # 加载基础 UI
        ui_file_path = apppath.app_path / 'ui' / 'page_sticker_library_view.ui'
        uic.loadUi(ui_file_path, self)

        # 双击图片时打开图片查看器
        self.listViewStickerList.doubleClicked.connect(self._on_sticker_double_clicked)

        # 信号
        self.signal_refresh_content.connect(services.sticker_library_viewer_service.wiring.slot_refresh_content)
        services.sticker_library_viewer_service.wiring.signal_refresh_library_content_result.connect(self.refresh_content)

        self.signal_refresh_content.emit()

    def refresh_content(self, model: QStandardItemModel):
        self.listViewStickerList.setModel(model)

    def _on_sticker_double_clicked(self, index: QModelIndex):
        if not index.isValid():
            return

        file_path = index.data(services.sticker_library_viewer_service.ROLE_FILE_PATH)
        if not file_path:
            return

        dialog = ImageViewerDialog(self)
        dialog.load_image(file_path, index.data())
        dialog.exec()
