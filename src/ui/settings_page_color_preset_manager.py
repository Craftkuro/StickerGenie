# coding=utf-8
"""设置窗口中的“颜色预设”管理页。

页面主体是一个带小工具栏的预设列表（工具栏提供添加/编辑/删除）。点击添加或
编辑时弹出对话框输入预设名称并选择颜色（Qt 取色器）。本页只维护内存中的待
保存预设，不立即写配置文件；设置窗口点击“应用”或“确定”时，由
ColorPresetManagerWidget.save_settings() 把内容写入配置管理器并统一落盘。
"""

from __future__ import annotations

import logging
from typing import Dict, List

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config_manager import ConfigManager
from services.settings import create_settings_manager
from utils.resource_path import resolve_resource_path

logger = logging.getLogger(__name__)

PRESET_DATA_ROLE = Qt.ItemDataRole.UserRole
DEFAULT_PRESET_COLOR = "#2196F3"
SWATCH_SIZE = 16
COLOR_PRESETS_KEY = "color_presets"


class PresetInputDialog(QDialog):
    """新建/编辑颜色预设对话框：输入名称 + 选择颜色。"""

    def __init__(
        self,
        parent=None,
        existing_names: List[str] | None = None,
        initial_color: str = DEFAULT_PRESET_COLOR,
        initial_name: str = "",
    ) -> None:
        super().__init__(parent)
        self._existing_names = set(existing_names or [])
        self._color = QColor(initial_color)
        if not self._color.isValid():
            self._color = QColor(DEFAULT_PRESET_COLOR)

        self.setWindowTitle(
            "编辑颜色预设" if initial_name else "新建颜色预设"
        )
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)
        self.lineEditPresetName = QLineEdit(self)
        self.lineEditPresetName.setPlaceholderText("预设名称，如：作者")
        self.lineEditPresetName.setClearButtonEnabled(True)
        self.lineEditPresetName.setText(initial_name)
        form.addRow("名称", self.lineEditPresetName)
        self.pushButtonColor = QPushButton(self)
        self.pushButtonColor.setMinimumWidth(120)
        form.addRow("颜色", self.pushButtonColor)
        layout.addLayout(form)

        self.buttonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        self.buttonBox.button(
            QDialogButtonBox.StandardButton.Cancel
        ).setText("取消")
        layout.addWidget(self.buttonBox)

        self._update_color_button()
        self._connect_signals()

    def preset_name(self) -> str:
        """返回去除首尾空白后的预设名称。"""
        return self.lineEditPresetName.text().strip()

    def preset_rgb(self) -> str:
        """返回当前选择的颜色（#RRGGBB）。"""
        return self._color.name(QColor.NameFormat.HexRgb).upper()

    def _connect_signals(self) -> None:
        self.pushButtonColor.clicked.connect(self._choose_color)
        self.lineEditPresetName.returnPressed.connect(self._on_accept)
        self.buttonBox.accepted.connect(self._on_accept)
        self.buttonBox.rejected.connect(self.reject)

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(self._color, self, "选择预设颜色")
        if color.isValid():
            self._color = color
            self._update_color_button()

    def _update_color_button(self) -> None:
        rgb = self.preset_rgb()
        text_color = "#000000" if self._color.lightness() >= 128 else "#FFFFFF"
        self.pushButtonColor.setText(rgb)
        self.pushButtonColor.setStyleSheet(
            "QPushButton {"
            f"background-color: {rgb}; color: {text_color};"
            "}"
        )

    def _on_accept(self) -> None:
        name = self.preset_name()
        if not name:
            QMessageBox.warning(self, "无法添加", "预设名称不能为空。")
            self.lineEditPresetName.setFocus()
            return
        if name in self._existing_names:
            QMessageBox.warning(
                self, "无法添加", f"已经存在同名预设“{name}”。"
            )
            self.lineEditPresetName.setFocus()
            return
        self.accept()


class ColorPresetManagerWidget(QWidget):
    """设置窗口中的颜色预设管理页。

    信号:
        changed: 待保存预设发生变化（添加/编辑/删除）时发出，用于让设置窗口
            启用“应用”按钮。
    """

    changed = pyqtSignal()

    def __init__(self, parent=None, config_manager: ConfigManager | None = None):
        super().__init__(parent)
        self._config_manager = config_manager or create_settings_manager()
        self._pending_presets: List[Dict[str, str]] = []

        self._build_ui()
        self._connect_signals()
        self.reload_settings()

    # --- UI 构建 ---

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        heading = QLabel("颜色预设", self)
        heading.setStyleSheet("font-size: 13pt; font-weight: bold;")
        layout.addWidget(heading)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        self.toolButtonAddPreset = self._make_tool_button(
            "plus.svg", "新增预设"
        )
        self.toolButtonEditPreset = self._make_tool_button(
            "pencil.svg", "编辑预设"
        )
        self.toolButtonEditPreset.setEnabled(False)
        self.toolButtonDeletePreset = self._make_tool_button(
            "trash.svg", "删除预设"
        )
        self.toolButtonDeletePreset.setEnabled(False)
        toolbar.addWidget(self.toolButtonAddPreset)
        toolbar.addWidget(self.toolButtonEditPreset)
        toolbar.addWidget(self.toolButtonDeletePreset)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.listWidgetPresets = QListWidget(self)
        self.listWidgetPresets.setAlternatingRowColors(True)
        self.listWidgetPresets.setSelectionMode(
            QListWidget.SelectionMode.SingleSelection
        )
        layout.addWidget(self.listWidgetPresets, 1)

    def _make_tool_button(self, icon_name: str, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(QIcon(str(resolve_resource_path(icon_name))))
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        return button

    def _connect_signals(self) -> None:
        self.toolButtonAddPreset.clicked.connect(self._add_preset)
        self.toolButtonEditPreset.clicked.connect(
            self._edit_selected_preset
        )
        self.toolButtonDeletePreset.clicked.connect(
            self._delete_selected_preset
        )
        self.listWidgetPresets.currentItemChanged.connect(
            self._on_current_item_changed
        )

    # --- 预设读写 ---

    def reload_settings(self) -> None:
        """从配置管理器重新载入预设（丢弃未保存的修改）。"""
        self._pending_presets = self._load_presets()
        self._reload_list()

    def save_settings(self) -> None:
        """把本页待保存的预设写入配置管理器。

        不直接调用 save()，由设置窗口在“应用/确定”时统一落盘，保证整次
        设置保存原子完成。
        """
        self._config_manager.set(COLOR_PRESETS_KEY, self._pending_presets)

    def _load_presets(self) -> List[Dict[str, str]]:
        presets = self._config_manager.get(COLOR_PRESETS_KEY, []) or []
        return [
            dict(preset)
            for preset in presets
            if (
                isinstance(preset, dict)
                and isinstance(preset.get("name"), str)
                and isinstance(preset.get("rgb"), str)
            )
        ]

    # --- 预设列表 ---

    def _reload_list(self, select_row: int = 0) -> None:
        self.listWidgetPresets.blockSignals(True)
        try:
            self.listWidgetPresets.clear()
            for preset in self._pending_presets:
                item = QListWidgetItem(
                    self._preset_icon(preset["rgb"]), preset["name"]
                )
                item.setData(PRESET_DATA_ROLE, dict(preset))
                item.setToolTip(preset["rgb"])
                self.listWidgetPresets.addItem(item)
            if self.listWidgetPresets.count() > 0:
                self.listWidgetPresets.setCurrentRow(
                    min(select_row, self.listWidgetPresets.count() - 1)
                )
        finally:
            self.listWidgetPresets.blockSignals(False)
        self._update_toolbar_state()

    @staticmethod
    def _preset_icon(rgb: str) -> QIcon:
        pixmap = QPixmap(SWATCH_SIZE, SWATCH_SIZE)
        pixmap.fill(QColor(rgb))
        return QIcon(pixmap)

    def _on_current_item_changed(self, _current, _previous) -> None:
        self._update_toolbar_state()

    def _update_toolbar_state(self) -> None:
        has_selection = self.listWidgetPresets.currentRow() >= 0
        self.toolButtonEditPreset.setEnabled(has_selection)
        self.toolButtonDeletePreset.setEnabled(has_selection)

    # --- 添加 / 编辑 / 删除 ---

    def _add_preset(self) -> None:
        dialog = PresetInputDialog(
            self,
            existing_names=[p["name"] for p in self._pending_presets],
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._pending_presets.append(
            {"name": dialog.preset_name(), "rgb": dialog.preset_rgb()}
        )
        self._reload_list(select_row=self.listWidgetPresets.count())
        self.changed.emit()

    def _edit_selected_preset(self) -> None:
        row = self.listWidgetPresets.currentRow()
        if row < 0 or row >= len(self._pending_presets):
            return
        preset = self._pending_presets[row]
        dialog = PresetInputDialog(
            self,
            existing_names=[
                p["name"]
                for p in self._pending_presets
                if p["name"] != preset["name"]
            ],
            initial_color=preset["rgb"],
            initial_name=preset["name"],
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._pending_presets[row] = {
            "name": dialog.preset_name(),
            "rgb": dialog.preset_rgb(),
        }
        self._reload_list(select_row=row)
        self.changed.emit()

    def _delete_selected_preset(self) -> None:
        row = self.listWidgetPresets.currentRow()
        if row < 0 or row >= len(self._pending_presets):
            return
        name = self._pending_presets[row]["name"]
        answer = QMessageBox.question(
            self,
            "删除预设",
            f"确定删除颜色预设“{name}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self._pending_presets[row]
        self._reload_list(select_row=row)
        self.changed.emit()
