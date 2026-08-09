# coding=utf-8
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6 import uic
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QMessageBox,
)

import apppath
from config_manager import ConfigField, ConfigManager, ConfigType

logger = logging.getLogger(__name__)

SETTINGS_VERSION = "1.2.0"
SETTINGS_SCHEMA = [
    ConfigField(
        "recent_search_limit",
        ConfigType.INT,
        3,
        "显示的最近搜索候选数量",
    ),
    ConfigField(
        "tag_suggestion_limit",
        ConfigType.INT,
        10,
        "显示的标签搜索候选数量",
    ),
    ConfigField(
        "recent_searches",
        ConfigType.LIST_STR,
        [],
        "最近搜索，最新的项目在前",
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
    """Edit user-facing application settings."""

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

        self._load_settings()
        self._connect_signals()

        self.listWidget.setCurrentRow(0)
        self.splitter.setCollapsible(0, False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([180, 600])
        self._apply_button.setEnabled(False)

    def _load_settings(self) -> None:
        self.spinBoxRecentSearchLimit.setValue(
            self._config_manager.get("recent_search_limit")
        )
        self.spinBoxTagSuggestionLimit.setValue(
            self._config_manager.get("tag_suggestion_limit")
        )

    def _connect_signals(self) -> None:
        self.listWidget.currentRowChanged.connect(
            self.stackedWidget.setCurrentIndex
        )
        self.buttonBox.accepted.connect(self._accept_settings)
        self.buttonBox.rejected.connect(self.reject)
        self._apply_button.clicked.connect(self.apply_settings)

        self.spinBoxRecentSearchLimit.valueChanged.connect(self._mark_dirty)
        self.spinBoxTagSuggestionLimit.valueChanged.connect(self._mark_dirty)

    def _mark_dirty(self, _value=None) -> None:
        self._apply_button.setEnabled(True)

    def _values_from_controls(self) -> dict[str, int]:
        return {
            "recent_search_limit": self.spinBoxRecentSearchLimit.value(),
            "tag_suggestion_limit": self.spinBoxTagSuggestionLimit.value(),
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
