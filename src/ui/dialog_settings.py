# coding=utf-8
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6 import uic
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import apppath
from config_manager import ConfigField, ConfigManager, ConfigType

logger = logging.getLogger(__name__)

SETTINGS_VERSION = "1.0.0"
SETTINGS_SCHEMA = [
    ConfigField(
        "restore_last_session",
        ConfigType.BOOL,
        True,
        "启动时恢复上次会话",
    ),
    ConfigField(
        "confirm_before_delete",
        ConfigType.BOOL,
        True,
        "删除贴纸前要求确认",
    ),
    ConfigField(
        "recent_search_limit",
        ConfigType.INT,
        10,
        "保留的最近搜索数量",
    ),
    ConfigField(
        "default_view",
        ConfigType.STRING,
        "grid",
        "默认内容视图",
    ),
    ConfigField(
        "theme",
        ConfigType.STRING,
        "system",
        "界面主题",
    ),
    ConfigField(
        "thumbnail_size",
        ConfigType.INT,
        144,
        "缩略图尺寸",
    ),
    ConfigField(
        "show_tag_counts",
        ConfigType.BOOL,
        True,
        "显示标签包含的贴纸数量",
    ),
]


def create_settings_manager(
    config_path: str | Path | None = None,
) -> ConfigManager:
    """Create the application settings manager for the configured data path."""
    if config_path is None:
        config_path = apppath.main_config_file_path
    if config_path is None:
        raise RuntimeError("应用程序数据路径尚未初始化")

    return ConfigManager(config_path, SETTINGS_SCHEMA, SETTINGS_VERSION)


class SettingsDialog(QDialog):
    """Edit application settings without applying them to current features."""

    def __init__(
        self,
        parent=None,
        config_manager: ConfigManager | None = None,
    ):
        super().__init__(parent)

        ui_file_path = apppath.app_path / "ui" / "dialog_settings.ui"
        uic.loadUi(ui_file_path, self)

        self._config_manager = config_manager or create_settings_manager()
        self._apply_button = self.buttonBox.button(
            QDialogButtonBox.StandardButton.Apply
        )

        self._build_pages()
        self._load_settings()
        self._connect_signals()

        self.listWidget.setCurrentRow(0)
        self.setMinimumSize(700, 500)
        self.splitter.setCollapsible(0, False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([180, 600])
        self._apply_button.setEnabled(False)

    def _build_pages(self) -> None:
        self.listWidget.clear()
        self.listWidget.addItems(["常规", "外观"])

        self._clear_page(self.scrollAreaWidgetContents)
        self._clear_page(self.scrollAreaWidgetContents_2)

        self.checkBoxRestoreLastSession = QCheckBox("启动时恢复上次会话")
        self.checkBoxRestoreLastSession.setObjectName(
            "checkBoxRestoreLastSession"
        )
        self.checkBoxConfirmBeforeDelete = QCheckBox("删除贴纸前要求确认")
        self.checkBoxConfirmBeforeDelete.setObjectName(
            "checkBoxConfirmBeforeDelete"
        )

        self.spinBoxRecentSearchLimit = QSpinBox()
        self.spinBoxRecentSearchLimit.setObjectName("spinBoxRecentSearchLimit")
        self.spinBoxRecentSearchLimit.setRange(0, 100)
        self.spinBoxRecentSearchLimit.setSuffix(" 项")

        self.comboBoxDefaultView = QComboBox()
        self.comboBoxDefaultView.setObjectName("comboBoxDefaultView")
        self.comboBoxDefaultView.addItem("网格", "grid")
        self.comboBoxDefaultView.addItem("列表", "list")

        general_layout = QVBoxLayout(self.scrollAreaWidgetContents)
        general_layout.setContentsMargins(16, 16, 16, 16)
        general_layout.setSpacing(16)
        general_layout.addWidget(self._make_heading("常规"))
        general_layout.addWidget(
            self._make_group(
                "启动与操作",
                [
                    (None, self.checkBoxRestoreLastSession),
                    (None, self.checkBoxConfirmBeforeDelete),
                ],
            )
        )
        general_layout.addWidget(
            self._make_group(
                "浏览与搜索",
                [
                    ("最近搜索", self.spinBoxRecentSearchLimit),
                    ("默认视图", self.comboBoxDefaultView),
                ],
            )
        )
        general_layout.addStretch()

        self.comboBoxTheme = QComboBox()
        self.comboBoxTheme.setObjectName("comboBoxTheme")
        self.comboBoxTheme.addItem("跟随系统", "system")
        self.comboBoxTheme.addItem("浅色", "light")
        self.comboBoxTheme.addItem("深色", "dark")

        self.spinBoxThumbnailSize = QSpinBox()
        self.spinBoxThumbnailSize.setObjectName("spinBoxThumbnailSize")
        self.spinBoxThumbnailSize.setRange(64, 320)
        self.spinBoxThumbnailSize.setSingleStep(16)
        self.spinBoxThumbnailSize.setSuffix(" px")

        self.checkBoxShowTagCounts = QCheckBox("显示标签中的贴纸数量")
        self.checkBoxShowTagCounts.setObjectName("checkBoxShowTagCounts")

        appearance_layout = QVBoxLayout(self.scrollAreaWidgetContents_2)
        appearance_layout.setContentsMargins(16, 16, 16, 16)
        appearance_layout.setSpacing(16)
        appearance_layout.addWidget(self._make_heading("外观"))
        appearance_layout.addWidget(
            self._make_group(
                "界面",
                [
                    ("主题", self.comboBoxTheme),
                    ("缩略图大小", self.spinBoxThumbnailSize),
                ],
            )
        )
        appearance_layout.addWidget(
            self._make_group(
                "内容显示",
                [(None, self.checkBoxShowTagCounts)],
            )
        )
        appearance_layout.addStretch()

    @staticmethod
    def _clear_page(page: QWidget) -> None:
        existing_layout = page.layout()
        if existing_layout is not None:
            QWidget().setLayout(existing_layout)

    @staticmethod
    def _make_heading(text: str) -> QLabel:
        label = QLabel(text)
        font = label.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        label.setFont(font)
        return label

    @staticmethod
    def _make_group(
        title: str,
        rows: list[tuple[str | None, QWidget]],
    ) -> QGroupBox:
        group = QGroupBox(title)
        layout = QFormLayout(group)
        layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        layout.setVerticalSpacing(12)
        for label, widget in rows:
            if label is None:
                layout.addRow(widget)
            else:
                layout.addRow(label, widget)
        return group

    def _load_settings(self) -> None:
        self.checkBoxRestoreLastSession.setChecked(
            self._config_manager.get("restore_last_session")
        )
        self.checkBoxConfirmBeforeDelete.setChecked(
            self._config_manager.get("confirm_before_delete")
        )
        self.spinBoxRecentSearchLimit.setValue(
            self._config_manager.get("recent_search_limit")
        )
        self._set_combo_value(
            self.comboBoxDefaultView,
            self._config_manager.get("default_view"),
        )
        self._set_combo_value(
            self.comboBoxTheme,
            self._config_manager.get("theme"),
        )
        self.spinBoxThumbnailSize.setValue(
            self._config_manager.get("thumbnail_size")
        )
        self.checkBoxShowTagCounts.setChecked(
            self._config_manager.get("show_tag_counts")
        )

    @staticmethod
    def _set_combo_value(combo_box: QComboBox, value: str) -> None:
        index = combo_box.findData(value)
        combo_box.setCurrentIndex(index if index >= 0 else 0)

    def _connect_signals(self) -> None:
        self.listWidget.currentRowChanged.connect(
            self.stackedWidget.setCurrentIndex
        )
        self.buttonBox.accepted.connect(self._accept_settings)
        self.buttonBox.rejected.connect(self.reject)
        self._apply_button.clicked.connect(self.apply_settings)

        self.checkBoxRestoreLastSession.toggled.connect(self._mark_dirty)
        self.checkBoxConfirmBeforeDelete.toggled.connect(self._mark_dirty)
        self.spinBoxRecentSearchLimit.valueChanged.connect(self._mark_dirty)
        self.comboBoxDefaultView.currentIndexChanged.connect(self._mark_dirty)
        self.comboBoxTheme.currentIndexChanged.connect(self._mark_dirty)
        self.spinBoxThumbnailSize.valueChanged.connect(self._mark_dirty)
        self.checkBoxShowTagCounts.toggled.connect(self._mark_dirty)

    def _mark_dirty(self, _value=None) -> None:
        self._apply_button.setEnabled(True)

    def _values_from_controls(self) -> dict[str, str | int | bool]:
        return {
            "restore_last_session": (
                self.checkBoxRestoreLastSession.isChecked()
            ),
            "confirm_before_delete": (
                self.checkBoxConfirmBeforeDelete.isChecked()
            ),
            "recent_search_limit": self.spinBoxRecentSearchLimit.value(),
            "default_view": self.comboBoxDefaultView.currentData(),
            "theme": self.comboBoxTheme.currentData(),
            "thumbnail_size": self.spinBoxThumbnailSize.value(),
            "show_tag_counts": self.checkBoxShowTagCounts.isChecked(),
        }

    def apply_settings(self) -> bool:
        previous_values = self._config_manager.get_all()
        try:
            for key, value in self._values_from_controls().items():
                self._config_manager.set(key, value)
            self._config_manager.save()
        except Exception as exc:
            logger.exception("保存设置失败")
            self._restore_manager(previous_values)
            QMessageBox.critical(self, "保存设置失败", str(exc))
            return False

        self._apply_button.setEnabled(False)
        return True

    def _restore_manager(self, previous_values: dict[str, object]) -> None:
        try:
            self._config_manager.reload()
        except Exception:
            logger.exception("重新加载设置失败")
            for key, value in previous_values.items():
                self._config_manager.set(key, value)

    def _accept_settings(self) -> None:
        if not self._apply_button.isEnabled() or self.apply_settings():
            self.accept()
