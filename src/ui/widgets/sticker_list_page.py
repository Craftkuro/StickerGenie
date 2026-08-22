#coding=utf-8
import logging
from pathlib import Path

from PyQt6 import uic
from PyQt6.QtCore import (
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    QSize,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QAction, QStandardItemModel
from PyQt6.QtWidgets import (
    QFileDialog,
    QMenu,
    QMessageBox,
    QSlider,
    QWidget,
)

import apppath
import commons.constants
#import commons.classes
from commons.roles import (
    ROLE_BLOB_ENTITY,
    ROLE_FILE_PATH,
    ROLE_STICKER_IMAGE,
)
import services.image_clipboard_service
import services.sticker_library_viewer_service
from utils.save_as_files import has_duplicate_original_file_names, save_as_files

from ..dialog_batch_tag_edit import BatchTagEditDialog
from ..dialog_image_viewer import ImageViewerDialog
from .toolbar_spacer import ToolbarSpacer

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
        self.toolbar.setIconSize(QSize(16, 16))

        # 弹性 spacer 由 _ensure_toolbar_spacer 懒创建，
        # 创建前工具栏内容保持左对齐（例如全库页的刷新按钮）。
        self.toolbar_spacer: ToolbarSpacer | None = None
        self._toolbar_spacer_action: QAction | None = None

        # 双击图片时打开图片查看器
        self.listViewStickerList.doubleClicked.connect(self._on_sticker_double_clicked)
        self.listViewStickerList.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.listViewStickerList.customContextMenuRequested.connect(
            self._show_sticker_context_menu
        )

    def add_toolbar_widget(self, widget: QWidget) -> QAction:
        """把任意自定义 widget（例如滑块）加入标签页工具栏末尾。"""
        return self.toolbar.addWidget(widget)

    def add_toolbar_action(self, action: QAction) -> QAction:
        return self.toolbar.addAction(action)

    def _ensure_toolbar_spacer(self) -> QAction:
        """确保工具栏存在一个弹性 spacer（初始状态在末尾），返回其 action。"""
        if self._toolbar_spacer_action is not None:
            return self._toolbar_spacer_action
        spacer = ToolbarSpacer(self)
        self.toolbar_spacer = spacer
        self._toolbar_spacer_action = self.toolbar.addWidget(spacer)
        return self._toolbar_spacer_action

    def insert_toolbar_widget_left_of_spacer(self, widget: QWidget) -> QAction:
        """在弹性 spacer 左侧插入自定义 widget（与工具栏左侧内容相邻）。"""
        spacer_action = self._ensure_toolbar_spacer()
        return self.toolbar.insertWidget(spacer_action, widget)

    def insert_toolbar_widget_right_of_spacer(self, widget: QWidget) -> QAction:
        """在弹性 spacer 右侧插入自定义 widget（位于滑块等右端控件之前）。"""
        spacer_action = self._ensure_toolbar_spacer()
        actions = self.toolbar.actions()
        index = actions.index(spacer_action)
        if index + 1 < len(actions):
            return self.toolbar.insertWidget(actions[index + 1], widget)
        return self.add_toolbar_widget(widget)

    def _setup_display_size_slider(self) -> None:
        """在工具栏右侧加入显示大小滑块（类似 Windows 7 资源管理器）。"""
        self._ensure_toolbar_spacer()

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
        self._open_image_viewer_for_index(index)

    def _open_image_viewer_for_index(self, index: QModelIndex):
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
        # 另存为对单选和多选都可用；复制、查找相似图片和图片属性只适用于单选。
        if len(selected_indexes) == 1:
            selected_index = selected_indexes[0]
            is_gif = self._is_gif_index(selected_index)
            copy_action = menu.addAction("复制到剪贴板")
            if is_gif:
                copy_first_frame_action = menu.addAction("复制首帧到剪贴板")
            menu.addSeparator()
            find_similar_action = menu.addAction("查找相似图片")
            save_as_action = menu.addAction("另存为")
            image_properties_action = menu.addAction("图片属性")
            menu.addSeparator()
            copy_action.triggered.connect(
                lambda _checked=False: self._copy_sticker_for_index(
                    selected_index
                )
            )
            if is_gif:
                copy_first_frame_action.triggered.connect(
                    lambda _checked=False: self._copy_sticker_for_index(
                        selected_index,
                        anim_as_static_image=True,
                    )
                )
            find_similar_action.triggered.connect(
                lambda _checked=False: self._find_similar_for_index(
                    selected_index
                )
            )
            save_as_action.triggered.connect(
                lambda _checked=False: self._save_as_for_indexes(
                    selected_indexes
                )
            )
            image_properties_action.triggered.connect(
                lambda _checked=False: self._open_image_viewer_for_index(
                    selected_index
                )
            )
        else:
            save_as_action = menu.addAction("另存为")
            save_as_action.triggered.connect(
                lambda _checked=False: self._save_as_for_indexes(
                    selected_indexes
                )
            )
            batch_tag_action = menu.addAction("批量编辑标签")
            batch_tag_action.triggered.connect(
                lambda _checked=False: self._batch_edit_tags_for_indexes(
                    selected_indexes
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

    def _batch_edit_tags_for_indexes(self, indexes: list[QModelIndex]) -> None:
        stickers = [
            index.data(ROLE_STICKER_IMAGE)
            for index in indexes
            if index.isValid() and index.data(ROLE_STICKER_IMAGE) is not None
        ]
        if len(stickers) < 2:
            return

        try:
            dialog = BatchTagEditDialog(stickers, parent=self)
        except RuntimeError as exc:
            QMessageBox.warning(self, "无法打开", str(exc))
            return

        dialog.tags_updated.connect(self._update_sticker_dtos)
        dialog.exec()

    def _update_sticker_dtos(self, updated_stickers: list) -> None:
        """更新当前模型中 DTO 的标签，同时保留 DTO 对象引用。"""
        updated_by_id = {
            sticker.id: sticker
            for sticker in updated_stickers
            if getattr(sticker, "id", None) is not None
        }
        model = self.listViewStickerList.model()
        if model is None or not updated_by_id:
            return

        for row in range(model.rowCount()):
            index = model.index(row, 0)
            sticker = index.data(ROLE_STICKER_IMAGE)
            if sticker is None:
                continue
            updated = updated_by_id.get(getattr(sticker, "id", None))
            if updated is None:
                continue
            sticker.tags = list(updated.tags)
            model.dataChanged.emit(index, index, [ROLE_STICKER_IMAGE])

    def _is_gif_index(self, index: QModelIndex) -> bool:
        blob_entity = index.data(ROLE_BLOB_ENTITY)
        if blob_entity is not None:
            if blob_entity.extension.casefold() == ".gif":
                return True

        file_path = index.data(ROLE_FILE_PATH)
        if file_path:
            if Path(file_path).suffix.casefold() == ".gif":
                return True

        sticker = index.data(ROLE_STICKER_IMAGE)
        if sticker is not None:
            return getattr(sticker, "extension", "").casefold() == ".gif"
        return False

    def _copy_sticker_for_index(
        self,
        index: QModelIndex,
        *,
        anim_as_static_image: bool = False,
    ):
        file_path = index.data(ROLE_FILE_PATH)
        sticker = index.data(ROLE_STICKER_IMAGE)
        if not file_path or sticker is None:
            return

        try:
            services.image_clipboard_service.copy_image_to_clipboard(
                file_path,
                sticker.original_file_name,
                anim_as_static_image=anim_as_static_image,
            )
        except Exception as exc:
            logger.exception("复制图片到剪贴板失败")
            QMessageBox.warning(self, "复制失败", str(exc))

    def _save_as_for_indexes(self, indexes: list[QModelIndex]):
        records = []
        for index in indexes:
            if not index.isValid():
                continue
            sticker = index.data(ROLE_STICKER_IMAGE)
            file_path = index.data(ROLE_FILE_PATH)
            if sticker is None or not file_path:
                continue
            records.append((sticker, Path(file_path)))
        if not records:
            return

        source_files = [
            (source_path, sticker.original_file_name)
            for sticker, source_path in records
        ]
        is_multi_selection = (
            sum(1 for index in indexes if index.isValid()) > 1
        )
        if not is_multi_selection:
            sticker = records[0][0]
            destination, _ = QFileDialog.getSaveFileName(
                self,
                "另存为",
                sticker.original_file_name,
            )
            if not destination:
                return
            target_path = Path(destination)
            succeeded, failed = save_as_files(
                source_files,
                target_path.parent,
                target_names=[target_path.name],
            )
        else:
            if has_duplicate_original_file_names(
                [original_file_name for _, original_file_name in source_files]
            ):
                QMessageBox.warning(
                    self,
                    "无法另存为",
                    "您所选的文件名的原始文件名有重复，"
                    "请少选一些或使用图库导出的功能。",
                )
                return
            destination = QFileDialog.getExistingDirectory(
                self,
                "选择保存目录",
            )
            if not destination:
                return
            succeeded, failed = save_as_files(
                source_files,
                destination,
            )

        if failed:
            QMessageBox.information(
                self,
                "导出完成",
                f"已导出{succeeded}张图片，{failed}张导出失败。",
            )
        else:
            QMessageBox.information(
                self,
                "导出完成",
                f"已导出{succeeded}张图片。",
            )

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
