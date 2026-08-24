# coding=utf-8
"""新建标签对话框。

与 TagSelectorWidget 集成：在标签选择组件中点击“新建标签”后弹出，
填写标签属性并保存。保存成功后返回新标签 id，父组件据此刷新标签列表。
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

import services.global_instances
from commons.dto import Tag
from ui.dialog_color_preset import ColorPresetDialog

logger = logging.getLogger(__name__)

DEFAULT_TAG_COLOR = "#2196F3"


class NewTagDialog(QDialog):
    """新建标签对话框，属性与标签管理器的标签编辑区一致。

    保存成功后发出 tag_created 信号并接受对话框；调用方可通过
    new_tag_id 属性或该信号拿到新标签 id，然后刷新自己的标签列表。
    """

    tag_created = pyqtSignal(int)

    def __init__(self, parent=None, database=None):
        super().__init__(parent)
        self._database = (
            database
            if database is not None
            else services.global_instances.current_library_db
        )
        if self._database is None:
            raise RuntimeError("仓库数据库尚未初始化。")

        self._new_tag_id: Optional[int] = None
        self._tag_color = QColor(DEFAULT_TAG_COLOR)

        self._build_ui()
        self._connect_signals()
        self._set_tag_color(self._tag_color)

    @property
    def new_tag_id(self) -> Optional[int]:
        """保存成功后返回新标签 id；未保存时为 None。"""
        return self._new_tag_id

    def _build_ui(self) -> None:
        self.setWindowTitle("新建标签")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        form = QFormLayout()
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        form.setVerticalSpacing(12)

        self.lineEditTagName = QLineEdit(self)
        form.addRow("名称", self.lineEditTagName)

        self.plainTextEditTagDescription = QPlainTextEdit(self)
        self.plainTextEditTagDescription.setMaximumHeight(120)
        self.plainTextEditTagDescription.setTabChangesFocus(True)
        form.addRow("描述", self.plainTextEditTagDescription)

        self.pushButtonTagColor = QPushButton(self)
        self.pushButtonTagColor.setMinimumWidth(120)
        form.addRow("颜色", self.pushButtonTagColor)

        self.checkBoxTagEnabled = QCheckBox("启用", self)
        self.checkBoxTagEnabled.setChecked(True)
        form.addRow("状态", self.checkBoxTagEnabled)

        self.spinBoxTagOrder = QSpinBox(self)
        self.spinBoxTagOrder.setMaximum(999999)
        form.addRow("排序", self.spinBoxTagOrder)

        layout.addLayout(form)

        self.buttonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.buttonBox.setObjectName("buttonBox")
        self.buttonBox.button(
            QDialogButtonBox.StandardButton.Save
        ).setDefault(True)
        layout.addWidget(self.buttonBox)

    def _connect_signals(self) -> None:
        self.pushButtonTagColor.clicked.connect(self._choose_tag_color)
        self.buttonBox.accepted.connect(self._save_tag)
        self.buttonBox.rejected.connect(self.reject)

    def _choose_tag_color(self) -> None:
        dialog = ColorPresetDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        rgb = dialog.selected_rgb()
        if rgb:
            self._set_tag_color(QColor(rgb))

    def _set_tag_color(self, color: QColor) -> None:
        if not color.isValid():
            color = QColor(DEFAULT_TAG_COLOR)
        self._tag_color = QColor(color)
        color_name = color.name(QColor.NameFormat.HexRgb).upper()
        text_color = "#000000" if color.lightness() >= 128 else "#FFFFFF"
        self.pushButtonTagColor.setText(color_name)
        self.pushButtonTagColor.setStyleSheet(
            "QPushButton {"
            f"background-color: {color_name}; color: {text_color};"
            "}"
        )

    def _save_tag(self) -> None:
        name = self.lineEditTagName.text().strip()
        if not name:
            QMessageBox.warning(self, "无法保存", "标签名称不能为空。")
            self.lineEditTagName.setFocus()
            return

        try:
            tags = self._database.list_tags()
        except Exception as exc:
            logger.exception("加载标签失败")
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        if any(tag.name == name for tag in tags):
            QMessageBox.warning(self, "无法保存", "已经存在同名标签。")
            self.lineEditTagName.setFocus()
            return

        tag = Tag()
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

        self._new_tag_id = saved_tag.id
        self.tag_created.emit(saved_tag.id)
        self.accept()
