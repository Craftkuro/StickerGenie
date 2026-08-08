#coding=utf-8
import logging

from PyQt6 import uic
from PyQt6.QtCore import pyqtSignal, QModelIndex, QPoint, Qt
from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtWidgets import QMenu, QMessageBox, QWidget

import apppath
import commons.constants
#import commons.classes
import services.sticker_library_viewer_service

from .dialog_image_viewer import ImageViewerDialog

logger = logging.getLogger(__name__)

class StickerLibraryViewPage(QWidget):
    signal_refresh_content = pyqtSignal()

    def __init__(self, *, auto_refresh: bool = True):
        super().__init__()
        self._auto_refresh = auto_refresh

        # 加载基础 UI
        ui_file_path = apppath.app_path / 'ui' / 'page_sticker_library_view.ui'
        uic.loadUi(ui_file_path, self)

        # 双击图片时打开图片查看器
        self.listViewStickerList.doubleClicked.connect(self._on_sticker_double_clicked)
        self.listViewStickerList.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.listViewStickerList.customContextMenuRequested.connect(
            self._show_sticker_context_menu
        )

        # 信号
        if self._auto_refresh:
            self.signal_refresh_content.connect(
                services.sticker_library_viewer_service.wiring.slot_refresh_content
            )
            services.sticker_library_viewer_service.wiring.signal_refresh_library_content_result.connect(
                self.refresh_content
            )
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
        sticker = index.data(services.sticker_library_viewer_service.ROLE_STICKER_IMAGE)
        dialog.load_image(file_path, index.data(), sticker)
        dialog.exec()

    def _show_sticker_context_menu(self, position: QPoint):
        index = self.listViewStickerList.indexAt(position)
        if not index.isValid():
            return

        self.listViewStickerList.setCurrentIndex(index)
        menu = QMenu(self)
        find_similar_action = menu.addAction("查找相似图片")
        menu.addSeparator()
        delete_action = menu.addAction("删除图片")
        selected_action = menu.exec(
            self.listViewStickerList.viewport().mapToGlobal(position)
        )

        if selected_action is find_similar_action:
            self._find_similar_for_index(index)
        elif selected_action is delete_action:
            self._delete_sticker_for_index(index)

    def _find_similar_for_index(self, index: QModelIndex):
        sticker = index.data(
            services.sticker_library_viewer_service.ROLE_STICKER_IMAGE
        )
        if sticker is None:
            return

        try:
            services.sticker_library_viewer_service.open_similar_stickers_tab(
                sticker
            )
        except Exception as exc:
            logger.exception("查找相似图片失败")
            QMessageBox.warning(self, "无法查找相似图片", str(exc))

    def _delete_sticker_for_index(self, index: QModelIndex):
        sticker = index.data(
            services.sticker_library_viewer_service.ROLE_STICKER_IMAGE
        )
        if sticker is None:
            return

        answer = QMessageBox.question(
            self,
            "删除图片",
            f'确定删除“{sticker.original_file_name}”吗？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            cleanup_errors = services.sticker_library_viewer_service.delete_sticker(
                sticker
            )
        except Exception as exc:
            logger.exception("删除图片失败")
            QMessageBox.critical(self, "删除失败", str(exc))
            return

        model = self.listViewStickerList.model()
        if model is not None:
            model.removeRow(index.row())
        services.sticker_library_viewer_service.wiring.slot_refresh_content()

        if cleanup_errors:
            QMessageBox.warning(
                self,
                "图片已删除",
                "\n".join(cleanup_errors),
            )
