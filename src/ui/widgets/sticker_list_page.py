#coding=utf-8
import logging

from PyQt6 import uic
from PyQt6.QtCore import (
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QAction, QStandardItemModel
from PyQt6.QtWidgets import QMenu, QMessageBox, QSizePolicy, QSlider, QWidget

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

    def _setup_display_size_slider(self) -> None:
        """在工具栏右侧加入显示大小滑块（类似 Windows 7 资源管理器）。"""
        spacer = QWidget(self)
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.add_toolbar_widget(spacer)

        slider = QSlider(Qt.Orientation.Horizontal, self)
        slider.setObjectName("displaySizeSlider")
        slider.setRange(48, commons.constants.THUMBNAIL_SIZE)
        slider.setSingleStep(8)
        slider.setPageStep(16)
        slider.setValue(self.listViewStickerList.item_size())
        slider.setFixedWidth(120)
        slider.setToolTip("调整图片显示大小")
        slider.setAccessibleName("图片显示大小")
        slider.valueChanged.connect(self.listViewStickerList.set_display_size)
        self.add_toolbar_widget(slider)
        self.display_size_slider = slider

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
        view = self.listViewStickerList
        index = view.indexAt(position)
        if not index.isValid():
            return

        selection_model = view.selectionModel()
        if selection_model is None:
            return
        # 右键已选中的项时保留整个多选；右键未选中的项则收缩为单选。
        if selection_model.isSelected(index):
            selection_model.setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
        else:
            selection_model.setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect,
            )

        selected_indexes = self._selected_indexes()
        menu = QMenu(self)
        # 复制和查找相似图片只适用于单选；多选时只保留删除入口。
        if len(selected_indexes) == 1:
            selected_index = selected_indexes[0]
            copy_action = menu.addAction("复制到剪贴板")
            menu.addSeparator()
            find_similar_action = menu.addAction("查找相似图片")
            menu.addSeparator()
            copy_action.triggered.connect(
                lambda _checked=False: self._copy_sticker_for_index(
                    selected_index
                )
            )
            find_similar_action.triggered.connect(
                lambda _checked=False: self._find_similar_for_index(
                    selected_index
                )
            )
        more_menu = menu.addMenu("更多")
        delete_action = more_menu.addAction("删除图片")
        if len(selected_indexes) == 1:
            delete_action.triggered.connect(
                lambda _checked=False: self._delete_sticker_for_index(
                    selected_indexes[0]
                )
            )
        else:
            delete_action.triggered.connect(
                lambda _checked=False: self._delete_stickers_for_indexes(
                    selected_indexes
                )
            )
        menu.exec(view.viewport().mapToGlobal(position))

    def _selected_indexes(self) -> list[QModelIndex]:
        """返回当前选中的行索引（按行号排序并去重）。"""
        view = self.listViewStickerList
        selection_model = view.selectionModel()
        model = view.model()
        if selection_model is None or model is None:
            return []
        rows = sorted(
            {
                index.row()
                for index in selection_model.selectedIndexes()
                if index.isValid()
            }
        )
        return [model.index(row, 0) for row in rows]

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
        self._delete_stickers_for_indexes([index])

    def _delete_stickers_for_indexes(self, indexes: list[QModelIndex]):
        stickers = []
        for index in indexes:
            if not index.isValid():
                continue
            sticker = index.data(ROLE_STICKER_IMAGE)
            if sticker is not None:
                stickers.append(sticker)
        if not stickers:
            return

        if len(stickers) == 1:
            message = f'确定删除“{stickers[0].original_file_name}”吗？'
        else:
            # 多选时用数量确认，避免弹出一长串文件名。
            message = f"确定删除选中的 {len(stickers)} 张图片吗？"
        answer = QMessageBox.question(
            self,
            "删除图片",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            cleanup_errors = (
                services.sticker_library_viewer_service.delete_stickers(
                    stickers
                )
            )
        except Exception as exc:
            logger.exception("删除图片失败")
            QMessageBox.critical(self, "删除失败", str(exc))
            return

        model = self.listViewStickerList.model()
        if model is not None:
            # 从大到小删除行，避免前面行移除后导致后续行号失效。
            rows = sorted(
                {
                    index.row()
                    for index in indexes
                    if index.isValid()
                },
                reverse=True,
            )
            for row in rows:
                model.removeRow(row)
        services.sticker_library_viewer_service.wiring.slot_refresh_content()

        if cleanup_errors:
            QMessageBox.warning(
                self,
                "图片已删除",
                "\n".join(cleanup_errors),
            )
