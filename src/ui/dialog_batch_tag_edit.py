# coding=utf-8
"""批量编辑图片标签对话框。"""

from __future__ import annotations

from typing import Sequence

from PyQt6 import uic
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QDialog, QMessageBox

import apppath
import services.global_instances
from commons.dto import StickerImage, Tag
from ui.dialog_tag_selector import TagSelectorDialog
from ui.widgets.custom_tag_widget import (
    TAG_ACCENT_COLOR_ROLE,
    CustomTagWidget,
)


TAG_DATA_ROLE = Qt.ItemDataRole.UserRole


class BatchTagEditDialog(QDialog):
    """选择批量标签操作及标签集合，并执行操作。"""

    tags_updated = pyqtSignal(list)

    def __init__(
        self,
        stickers: Sequence[StickerImage],
        parent=None,
        database=None,
    ) -> None:
        super().__init__(parent)

        ui_file_path = apppath.app_path / "ui" / "dialog_batch_tag_edit.ui"
        uic.loadUi(ui_file_path, self)

        self._database = (
            database
            if database is not None
            else services.global_instances.current_library_db
        )
        if self._database is None:
            raise RuntimeError("仓库数据库尚未初始化。")

        self._stickers = [
            sticker
            for sticker in stickers
            if getattr(sticker, "id", None) is not None
        ]
        self._tag_model = QStandardItemModel(self)
        self._tag_widget = CustomTagWidget(
            self._tag_model,
            self.widgetTagList,
        )
        self.widgetTagList.layout().addWidget(self._tag_widget)

        self.radioButtonAddTags.toggled.connect(self._update_action_label)
        self._tag_widget.add_action.triggered.connect(self._add_tags)
        self._tag_widget.delete_action.triggered.connect(self._delete_selected_tags)
        self.pushButtonConfirm.clicked.connect(self._confirm)
        self.pushButtonCancel.clicked.connect(self.reject)
        self._update_action_label(self.radioButtonAddTags.isChecked())

    def selected_tag_ids(self) -> list[int]:
        """返回当前待操作的标签 ID。"""
        tag_ids = []
        for row in range(self._tag_model.rowCount()):
            tag = self._tag_model.index(row, 0).data(TAG_DATA_ROLE)
            if tag is not None and tag.id is not None:
                tag_ids.append(tag.id)
        return tag_ids

    def selected_tags(self) -> list[Tag]:
        """返回当前待操作的标签。"""
        tags = []
        for row in range(self._tag_model.rowCount()):
            tag = self._tag_model.index(row, 0).data(TAG_DATA_ROLE)
            if tag is not None:
                tags.append(tag)
        return tags

    def _update_action_label(self, add: bool) -> None:
        action = "增加" if add else "删除"
        self.labelTagList.setText(f"将要{action}的标签列表：")

    def _add_tags(self) -> None:
        dialog = TagSelectorDialog(
            database=self._database,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        existing_ids = set(self.selected_tag_ids())
        for tag in dialog.selected_tags():
            if tag.id is None or tag.id in existing_ids:
                continue
            item = QStandardItem(tag.name)
            item.setEditable(False)
            item.setData(tag, TAG_DATA_ROLE)
            item.setData(tag.color_rgb, TAG_ACCENT_COLOR_ROLE)
            self._tag_model.appendRow(item)
            existing_ids.add(tag.id)

    def _delete_selected_tags(self) -> None:
        rows = sorted(
            {
                index.row()
                for index in self._tag_widget.selectedIndexes()
                if index.isValid()
            },
            reverse=True,
        )
        for row in rows:
            self._tag_model.removeRow(row)

    def _confirm(self) -> None:
        tag_ids = self.selected_tag_ids()
        if not tag_ids:
            QMessageBox.warning(
                self,
                "无法编辑标签",
                "请至少选择一个标签。",
            )
            return

        try:
            modified_count, updated_stickers = (
                self._database.batch_edit_sticker_tags(
                    (sticker.id for sticker in self._stickers),
                    tag_ids,
                    add=self.radioButtonAddTags.isChecked(),
                )
            )
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return

        self.tags_updated.emit(updated_stickers)
        QMessageBox.information(
            self,
            "批量编辑标签",
            f"已完成操作，共修改{modified_count}张图片。",
        )
        self.accept()
