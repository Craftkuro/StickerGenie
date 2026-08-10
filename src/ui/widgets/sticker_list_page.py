#coding=utf-8
import logging

from PyQt6 import uic
from PyQt6.QtCore import pyqtSignal, QModelIndex, QPoint, Qt
from PyQt6.QtGui import QAction, QStandardItemModel
from PyQt6.QtWidgets import QMenu, QMessageBox, QWidget

import apppath
import commons.constants
#import commons.classes
from commons.roles import ROLE_FILE_PATH, ROLE_STICKER_IMAGE
import services.image_clipboard_service
import services.sticker_library_viewer_service

from ..dialog_image_viewer import ImageViewerDialog

logger = logging.getLogger(__name__)


class StickerListPage(QWidget):
    """含工具栏和 StickerListView 的表情包标签页基类。"""

    signal_refresh_content = pyqtSignal()

    def __init__(self, *, ui_file_name: str, auto_refresh: bool = True):
        super().__init__()
        self._auto_refresh = auto_refresh

        # 加载基础 UI
        ui_file_path = apppath.app_path / 'ui' / ui_file_name
        uic.loadUi(ui_file_path, self)

        # 工具栏：目前没有功能按钮，但必须支持任意自定义 widget。
        self.toolbar = self.toolbarStickerList
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        # 双击图片时打开图片查看器
        self.listViewStickerList.doubleClicked.connect(self._on_sticker_double_clicked)
        self.listViewStickerList.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.listViewStickerList.customContextMenuRequested.connect(
            self._show_sticker_context_menu
        )

    def add_toolbar_widget(self, widget: QWidget) -> QAction:
        """把任意自定义 widget（例如滑块）加入标签页工具栏。"""
        return self.toolbar.addWidget(widget)

    def add_toolbar_action(self, action: QAction) -> QAction:
        return self.toolbar.addAction(action)

    def refresh_content(self, model: QStandardItemModel):
        previous_model = self.listViewStickerList.model()
        if model.parent() is None:
            model.setParent(self.listViewStickerList)
        self.listViewStickerList.setModel(model)
        if (
            previous_model is not None
            and previous_model is not model
            and previous_model.parent() is self.listViewStickerList
        ):
            previous_model.deleteLater()

    def _on_sticker_double_clicked(self, index: QModelIndex):
        if not index.isValid():
            return

        file_path = index.data(ROLE_FILE_PATH)
        if not file_path:
            return

        dialog = ImageViewerDialog(self)
        sticker = index.data(ROLE_STICKER_IMAGE)
        dialog.load_image(file_path, index.data(), sticker)
        dialog.exec()

    def _show_sticker_context_menu(self, position: QPoint):
        index = self.listViewStickerList.indexAt(position)
        if not index.isValid():
            return

        self.listViewStickerList.setCurrentIndex(index)
        menu = QMenu(self)
        copy_action = menu.addAction("复制到剪贴板")
        menu.addSeparator()
        find_similar_action = menu.addAction("查找相似图片")
        menu.addSeparator()
        delete_action = menu.addAction("删除图片")
        selected_action = menu.exec(
            self.listViewStickerList.viewport().mapToGlobal(position)
        )

        if selected_action is copy_action:
            self._copy_sticker_for_index(index)
        elif selected_action is find_similar_action:
            self._find_similar_for_index(index)
        elif selected_action is delete_action:
            self._delete_sticker_for_index(index)

    def _copy_sticker_for_index(self, index: QModelIndex):
        file_path = index.data(ROLE_FILE_PATH)
        sticker = index.data(ROLE_STICKER_IMAGE)
        if not file_path or sticker is None:
            return

        try:
            services.image_clipboard_service.copy_image_to_clipboard(
                file_path,
                sticker.original_file_name,
            )
        except Exception as exc:
            logger.exception("复制图片到剪贴板失败")
            QMessageBox.warning(self, "复制失败", str(exc))

    def _find_similar_for_index(self, index: QModelIndex):
        sticker = index.data(ROLE_STICKER_IMAGE)
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
        sticker = index.data(ROLE_STICKER_IMAGE)
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
