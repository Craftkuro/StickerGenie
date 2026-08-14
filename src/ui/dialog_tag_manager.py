# coding=utf-8
from __future__ import annotations

import logging

from PyQt6 import uic
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPalette, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QListWidgetItem,
    QMessageBox,
    QStyle,
)

import apppath
import services.global_instances
from commons.dto import Tag
from ui.dialog_color_preset import ColorPresetDialog
from utils.resource_path import resolve_resource_path

logger = logging.getLogger(__name__)

TAG_DATA_ROLE = Qt.ItemDataRole.UserRole


class TagManagerDialog(QDialog):
    """Manage tags in the currently open sticker library."""

    def __init__(self, parent=None, database=None):
        super().__init__(parent)

        ui_file_path = apppath.app_path / "ui" / "dialog_tag_manager.ui"
        uic.loadUi(ui_file_path, self)

        self._database = (
            database
            if database is not None
            else services.global_instances.current_library_db
        )
        if self._database is None:
            raise RuntimeError("仓库数据库尚未初始化。")

        self._selected_tag: Tag | None = None
        self._is_new_tag = False
        self._loading_editor = False
        self._editor_baseline: tuple[object, ...] | None = None
        self._tag_color = QColor("#2196F3")

        self._configure_ui()
        self._connect_signals()
        self._reload_tags(select_first=True)

    def _configure_ui(self) -> None:
        self.splitterTags.setCollapsible(0, False)
        self.splitterTags.setCollapsible(1, False)
        self.splitterTags.setStretchFactor(0, 0)
        self.splitterTags.setStretchFactor(1, 1)
        self.splitterTags.setSizes([260, 560])

        self.toolButtonAddTag.setIcon(
            QIcon(str(resolve_resource_path("plus.svg")))
        )
        self.toolButtonDeleteTag.setIcon(
            QIcon(str(resolve_resource_path("trash.svg")))
        )
        self.pushButtonSaveTag.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton)
        )

    def _connect_signals(self) -> None:
        self.lineEditTagFilter.textChanged.connect(self._filter_tags)
        self.listWidgetTags.currentItemChanged.connect(
            self._on_current_item_changed
        )
        self.toolButtonAddTag.clicked.connect(self._start_new_tag)
        self.toolButtonDeleteTag.clicked.connect(self._delete_current_tag)
        self.pushButtonTagColor.clicked.connect(self._choose_tag_color)
        self.pushButtonSaveTag.clicked.connect(self._save_current_tag)

        self.lineEditTagName.textChanged.connect(self._update_dirty_state)
        self.plainTextEditTagDescription.textChanged.connect(
            self._update_dirty_state
        )
        self.checkBoxTagEnabled.toggled.connect(self._update_dirty_state)
        self.spinBoxTagOrder.valueChanged.connect(self._update_dirty_state)

    def _reload_tags(
        self,
        *,
        selected_tag_id: int | None = None,
        select_first: bool = False,
    ) -> None:
        try:
            tags = self._database.list_tags()
        except Exception as exc:
            logger.exception("加载标签失败")
            QMessageBox.critical(self, "加载失败", str(exc))
            self.listWidgetTags.clear()
            self._show_no_selection()
            return

        self.listWidgetTags.blockSignals(True)
        try:
            self.listWidgetTags.clear()
            item_to_select = None
            for tag in tags:
                item = self._make_tag_item(tag)
                self.listWidgetTags.addItem(item)
                if tag.id == selected_tag_id:
                    item_to_select = item

            self._filter_tags(self.lineEditTagFilter.text())
            if item_to_select is None and select_first:
                item_to_select = self._first_visible_item()
            if item_to_select is not None and not item_to_select.isHidden():
                self.listWidgetTags.setCurrentItem(item_to_select)
        finally:
            self.listWidgetTags.blockSignals(False)

        current_item = self.listWidgetTags.currentItem()
        if current_item is None:
            self._show_no_selection()
        else:
            self._load_tag(current_item.data(TAG_DATA_ROLE))

    def _make_tag_item(self, tag: Tag) -> QListWidgetItem:
        item = QListWidgetItem(tag.name)
        item.setData(TAG_DATA_ROLE, tag)
        item.setIcon(self._color_icon(tag.color_rgb))
        if not tag.enabled:
            disabled_text = self.palette().color(
                QPalette.ColorGroup.Disabled,
                QPalette.ColorRole.Text,
            )
            item.setForeground(disabled_text)
            item.setToolTip("已禁用")
        return item

    @staticmethod
    def _color_icon(color_value: str) -> QIcon:
        color = QColor(color_value)
        if not color.isValid():
            color = QColor("#2196F3")
        pixmap = QPixmap(14, 14)
        pixmap.fill(color)
        return QIcon(pixmap)

    def _first_visible_item(self) -> QListWidgetItem | None:
        for row in range(self.listWidgetTags.count()):
            item = self.listWidgetTags.item(row)
            if not item.isHidden():
                return item
        return None

    def _filter_tags(self, text: str) -> None:
        normalized_query = text.strip().casefold()
        current_item = self.listWidgetTags.currentItem()
        current_hidden = False
        for row in range(self.listWidgetTags.count()):
            item = self.listWidgetTags.item(row)
            tag = item.data(TAG_DATA_ROLE)
            hidden = normalized_query not in tag.name.casefold()
            item.setHidden(hidden)
            if item is current_item:
                current_hidden = hidden

        if current_hidden:
            self.listWidgetTags.setCurrentItem(None)
            self._show_no_selection()

    def _on_current_item_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            self._show_no_selection()
            return
        self._load_tag(current.data(TAG_DATA_ROLE))

    def _load_tag(self, tag: Tag) -> None:
        self._selected_tag = tag
        self._is_new_tag = False
        self.toolButtonDeleteTag.setEnabled(True)
        self.spinBoxTagOrder.setEnabled(True)
        self._load_editor_values(
            tag.name,
            tag.description or "",
            tag.enabled,
            tag.color_rgb,
            tag.order,
        )

    def _start_new_tag(self) -> None:
        self.listWidgetTags.setCurrentItem(None)
        self._selected_tag = None
        self._is_new_tag = True
        self.toolButtonDeleteTag.setEnabled(True)
        self.spinBoxTagOrder.setEnabled(False)
        next_order = max(
            (
                self.listWidgetTags.item(row).data(TAG_DATA_ROLE).order
                for row in range(self.listWidgetTags.count())
            ),
            default=0,
        )
        self._load_editor_values("", "", True, "#2196F3", next_order)
        self._editor_baseline = None
        self.pushButtonSaveTag.setEnabled(True)
        self.lineEditTagName.setFocus()

    def _load_editor_values(
        self,
        name: str,
        description: str,
        enabled: bool,
        color: str,
        order: int,
    ) -> None:
        self._loading_editor = True
        try:
            self.lineEditTagName.setText(name)
            self.plainTextEditTagDescription.setPlainText(description)
            self.checkBoxTagEnabled.setChecked(enabled)
            self.spinBoxTagOrder.setValue(order)
            self._set_tag_color(QColor(color))
            self.stackedWidgetTagEditor.setCurrentWidget(self.pageTagEditor)
            self._editor_baseline = self._editor_values()
            self.pushButtonSaveTag.setEnabled(False)
        finally:
            self._loading_editor = False

    def _show_no_selection(self) -> None:
        self._selected_tag = None
        self._is_new_tag = False
        self._editor_baseline = None
        self.toolButtonDeleteTag.setEnabled(False)
        self.pushButtonSaveTag.setEnabled(False)
        self.stackedWidgetTagEditor.setCurrentWidget(self.pageNoTagSelected)

    def _editor_values(self) -> tuple[object, ...]:
        return (
            self.lineEditTagName.text(),
            self.plainTextEditTagDescription.toPlainText(),
            self.checkBoxTagEnabled.isChecked(),
            self._tag_color.name(QColor.NameFormat.HexRgb).upper(),
            self.spinBoxTagOrder.value(),
        )

    def _update_dirty_state(self, _value=None) -> None:
        if self._loading_editor:
            return
        is_editing = self._selected_tag is not None or self._is_new_tag
        is_dirty = self._editor_baseline != self._editor_values()
        self.pushButtonSaveTag.setEnabled(is_editing and is_dirty)

    def _choose_tag_color(self) -> None:
        dialog = ColorPresetDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        rgb = dialog.selected_rgb()
        if rgb:
            self._set_tag_color(QColor(rgb))
            self._update_dirty_state()

    def _set_tag_color(self, color: QColor) -> None:
        if not color.isValid():
            color = QColor("#2196F3")
        self._tag_color = QColor(color)
        color_name = color.name(QColor.NameFormat.HexRgb).upper()
        text_color = "#000000" if color.lightness() >= 128 else "#FFFFFF"
        self.pushButtonTagColor.setText(color_name)
        self.pushButtonTagColor.setStyleSheet(
            "QPushButton {"
            f"background-color: {color_name}; color: {text_color};"
            "}"
        )

    def _save_current_tag(self) -> None:
        if self._selected_tag is None and not self._is_new_tag:
            return

        name = self.lineEditTagName.text().strip()
        if not name:
            QMessageBox.warning(self, "无法保存", "标签名称不能为空。")
            self.lineEditTagName.setFocus()
            return

        duplicate = self._find_tag_by_name(name)
        current_id = self._selected_tag.id if self._selected_tag else None
        if duplicate is not None and duplicate.id != current_id:
            QMessageBox.warning(self, "无法保存", "已经存在同名标签。")
            self.lineEditTagName.setFocus()
            return

        tag = Tag()
        tag.id = current_id
        tag.name = name
        tag.description = (
            self.plainTextEditTagDescription.toPlainText().strip() or None
        )
        tag.enabled = self.checkBoxTagEnabled.isChecked()
        tag.color_rgb = self._tag_color.name(QColor.NameFormat.HexRgb).upper()
        tag.order = self.spinBoxTagOrder.value()

        try:
            saved_tag = self._database.add_or_modify_tag(tag)
        except Exception as exc:
            logger.exception("保存标签失败")
            QMessageBox.critical(self, "保存失败", str(exc))
            return

        self._is_new_tag = False
        self._reload_tags(selected_tag_id=saved_tag.id)

    def _find_tag_by_name(self, name: str) -> Tag | None:
        for row in range(self.listWidgetTags.count()):
            tag = self.listWidgetTags.item(row).data(TAG_DATA_ROLE)
            if tag.name == name:
                return tag
        return None

    def _delete_current_tag(self) -> None:
        if self._is_new_tag:
            self._show_no_selection()
            return
        if self._selected_tag is None:
            return

        answer = QMessageBox.question(
            self,
            "删除标签",
            f"确定删除标签“{self._selected_tag.name}”吗？\n"
            "这会解除它与所有图片的关联。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        deleted_tag = self._selected_tag
        try:
            self._database.delete_tag(deleted_tag)
        except Exception as exc:
            logger.exception("删除标签失败")
            QMessageBox.critical(self, "删除失败", str(exc))
            return

        self._reload_tags(select_first=True)
