#coding=utf-8
import logging

from PyQt6 import uic
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtWidgets import QWidget, QListView

import apppath
import commons.constants
#import commons.classes
import services.sticker_library_viewer_service

logger = logging.getLogger(__name__)

class StickerLibraryViewPage(QWidget):
    signal_refresh_content = pyqtSignal()

    def __init__(self):
        super().__init__()

        # 加载基础 UI
        ui_file_path = apppath.app_path / 'ui' / 'page_sticker_library_view.ui'
        uic.loadUi(ui_file_path, self)

        # 信号
        self.signal_refresh_content.connect(services.sticker_library_viewer_service.wiring.slot_refresh_content)
        services.sticker_library_viewer_service.wiring.signal_refresh_library_content_result.connect(self.refresh_content)

        self.signal_refresh_content.emit()

    def refresh_content(self, model: QStandardItemModel):
        self.listViewStickerList.setModel(model)
