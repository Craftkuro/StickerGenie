# coding=utf-8
"""选择颜色预设对话框。

窗体主体为 QListWidget，展示现有的颜色预设；底部为“新建预设”分组，
可输入名称、选择颜色并添加，添加后写入应用程序共享的配置文件
（config.toml，通过 config_manager 读写，不写入数据库）。
确定后可通过 selected_preset()/selected_rgb() 获取所选预设。
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from config_manager import ConfigManager
from services.settings import create_settings_manager

logger = logging.getLogger(__name__)

PRESET_DATA_ROLE = Qt.ItemDataRole.UserRole
DEFAULT_PRESET_COLOR = "#2196F3"
SWATCH_SIZE = 16
COLOR_PRESETS_KEY = "color_presets"


class ColorPresetDialog(QDialog):
    """选择颜色预设的对话框。"""

    def __init__(
        self,
        parent=None,
        config_manager: Optional[ConfigManager] = None,
    ) -> None:
        super().__init__(parent)
        self._config_manager = config_manager or create_settings_manager()
        self._current_color = QColor(DEFAULT_PRESET_COLOR)

        self._build_ui()
        self._connect_signals()
        self._reload_presets()
        self._update_color_button()
        self._update_ok_button()

    def selected_preset(self) -> Optional[Dict[str, str]]:
        """返回当前选中的预设 {name, rgb}；未选择时返回 None。"""
        item = self.listWidgetPresets.currentItem()
        if item is None:
            return None
        return item.data(PRESET_DATA_ROLE)

    def selected_rgb(self) -> Optional[str]:
        """返回当前选中预设的 RGB 颜色；未选择时返回 None。"""
        preset = self.selected_preset()
        return preset["rgb"] if preset is not None else None

    # --- 预设读写 ---

    def _load_presets(self) -> List[Dict[str, str]]:
        presets = self._config_manager.get(COLOR_PRESETS_KEY, []) or []
        return [
            preset
            for preset in presets
            if (
                isinstance(preset, dict)
                and isinstance(preset.get("name"), str)
                and isinstance(preset.get("rgb"), str)
            )
        ]

    def _save_presets(self, presets: List[Dict[str, str]]) -> None:
        self._config_manager.set(COLOR_PRESETS_KEY, presets)
        self._config_manager.save()

    # --- UI 构建 ---

    def _build_ui(self) -> None:
        self.setWindowTitle("选择颜色预设")
        self.setMinimumSize(420, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.listWidgetPresets = QListWidget(self)
        self.listWidgetPresets.setObjectName("listWidgetPresets")
        layout.addWidget(self.listWidgetPresets, 1)

        self.groupBoxNewPreset = QGroupBox("新建预设", self)
        self.groupBoxNewPreset.setObjectName("groupBoxNewPreset")
        group_layout = QHBoxLayout(self.groupBoxNewPreset)
        group_layout.setContentsMargins(12, 12, 12, 12)
        group_layout.setSpacing(8)

        self.lineEditPresetName = QLineEdit(self.groupBoxNewPreset)
        self.lineEditPresetName.setObjectName("lineEditPresetName")
        self.lineEditPresetName.setPlaceholderText("预设名称，如：作者")
        group_layout.addWidget(self.lineEditPresetName, 1)

        self.pushButtonPresetColor = QPushButton(self.groupBoxNewPreset)
        self.pushButtonPresetColor.setObjectName("pushButtonPresetColor")
        self.pushButtonPresetColor.setMinimumWidth(120)
        group_layout.addWidget(self.pushButtonPresetColor)

        self.pushButtonAddPreset = QPushButton("添加", self.groupBoxNewPreset)
        self.pushButtonAddPreset.setObjectName("pushButtonAddPreset")
        group_layout.addWidget(self.pushButtonAddPreset)

        layout.addWidget(self.groupBoxNewPreset)

        self.buttonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.buttonBox.setObjectName("buttonBox")
        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        self.buttonBox.button(
            QDialogButtonBox.StandardButton.Cancel
        ).setText("取消")
        layout.addWidget(self.buttonBox)

    def _connect_signals(self) -> None:
        self.pushButtonPresetColor.clicked.connect(self._choose_color)
        self.pushButtonAddPreset.clicked.connect(self._add_preset)
        self.lineEditPresetName.returnPressed.connect(self._add_preset)
        self.listWidgetPresets.currentItemChanged.connect(
            self._on_current_item_changed
        )
        self.listWidgetPresets.itemDoubleClicked.connect(
            lambda _item: self.accept()
        )
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

    # --- 预设列表 ---

    def _reload_presets(self, select_row: int = 0) -> None:
        try:
            presets = self._load_presets()
        except Exception as exc:
            logger.exception("加载颜色预设失败")
            QMessageBox.critical(self, "加载失败", str(exc))
            presets = []

        self.listWidgetPresets.blockSignals(True)
        try:
            self.listWidgetPresets.clear()
            for preset in presets:
                item = QListWidgetItem(
                    self._preset_icon(preset["rgb"]), preset["name"]
                )
                item.setData(PRESET_DATA_ROLE, preset)
                item.setToolTip(preset["rgb"])
                self.listWidgetPresets.addItem(item)
            if self.listWidgetPresets.count() > 0:
                self.listWidgetPresets.setCurrentRow(
                    min(select_row, self.listWidgetPresets.count() - 1)
                )
        finally:
            self.listWidgetPresets.blockSignals(False)
        self._update_ok_button()

    def _preset_icon(self, rgb: str) -> QIcon:
        pixmap = QPixmap(SWATCH_SIZE, SWATCH_SIZE)
        pixmap.fill(QColor(rgb))
        return QIcon(pixmap)

    def _on_current_item_changed(self, _current, _previous) -> None:
        self._update_ok_button()

    def _update_ok_button(self) -> None:
        ok_button = self.buttonBox.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setEnabled(self.selected_preset() is not None)

    def _select_preset_by_name(self, name: str) -> None:
        for row in range(self.listWidgetPresets.count()):
            preset = self.listWidgetPresets.item(row).data(PRESET_DATA_ROLE)
            if preset["name"] == name:
                self.listWidgetPresets.setCurrentRow(row)
                return

    # --- 新建预设 ---

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(self._current_color, self, "选择预设颜色")
        if color.isValid():
            self._current_color = color
            self._update_color_button()

    def _update_color_button(self) -> None:
        rgb = self._current_color.name(QColor.NameFormat.HexRgb).upper()
        text_color = (
            "#000000" if self._current_color.lightness() >= 128 else "#FFFFFF"
        )
        self.pushButtonPresetColor.setText(rgb)
        self.pushButtonPresetColor.setStyleSheet(
            "QPushButton {"
            f"background-color: {rgb}; color: {text_color};"
            "}"
        )

    def _add_preset(self) -> None:
        name = self.lineEditPresetName.text().strip()
        if not name:
            QMessageBox.warning(self, "无法添加", "预设名称不能为空。")
            self.lineEditPresetName.setFocus()
            return

        presets = self._load_presets()
        if any(preset["name"] == name for preset in presets):
            QMessageBox.warning(
                self, "无法添加", f"已经存在同名预设“{name}”。"
            )
            self.lineEditPresetName.setFocus()
            return

        preset = {
            "name": name,
            "rgb": self._current_color.name(QColor.NameFormat.HexRgb).upper(),
        }
        presets.append(preset)
        try:
            self._save_presets(presets)
        except Exception as exc:
            logger.exception("保存颜色预设失败")
            QMessageBox.critical(self, "添加失败", str(exc))
            return

        self._reload_presets()
        self._select_preset_by_name(name)
        self.lineEditPresetName.clear()
        self.lineEditPresetName.setFocus()
