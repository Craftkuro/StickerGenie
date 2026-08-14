# coding=utf-8
from __future__ import annotations

import logging

from PyQt6 import uic
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QMessageBox,
)

import apppath
from config_manager import ConfigManager
from services.settings import create_settings_manager
from ui.settings_page_color_preset_manager import ColorPresetManagerWidget

logger = logging.getLogger(__name__)


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
        self.colorPresetManager = ColorPresetManagerWidget(
            self.pageColorPresets, config_manager=self._config_manager
        )
        self.pageColorPresets.layout().addWidget(self.colorPresetManager)

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
        self.doubleSpinBoxSimilarImageTargetDropRatio.setValue(
            float(self._config_manager.get("similar_image_target_drop_ratio"))
        )
        self.spinBoxSimilarImageMinKeep.setValue(
            self._config_manager.get("similar_image_min_keep")
        )
        self.doubleSpinBoxSimilarImageMinSimilarity.setValue(
            float(self._config_manager.get("similar_image_min_similarity"))
        )
        self.spinBoxSimilarImageMaxResults.setValue(
            self._config_manager.get("similar_image_max_results")
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
        self.doubleSpinBoxSimilarImageTargetDropRatio.valueChanged.connect(
            self._mark_dirty
        )
        self.spinBoxSimilarImageMinKeep.valueChanged.connect(self._mark_dirty)
        self.doubleSpinBoxSimilarImageMinSimilarity.valueChanged.connect(
            self._mark_dirty
        )
        self.spinBoxSimilarImageMaxResults.valueChanged.connect(self._mark_dirty)
        self.colorPresetManager.changed.connect(self._mark_dirty)

    def _mark_dirty(self, _value=None) -> None:
        self._apply_button.setEnabled(True)

    def _values_from_controls(self) -> dict[str, int | str]:
        return {
            "recent_search_limit": self.spinBoxRecentSearchLimit.value(),
            "tag_suggestion_limit": self.spinBoxTagSuggestionLimit.value(),
            "similar_image_target_drop_ratio": (
                f"{self.doubleSpinBoxSimilarImageTargetDropRatio.value():.2f}"
            ),
            "similar_image_min_keep": self.spinBoxSimilarImageMinKeep.value(),
            "similar_image_min_similarity": (
                f"{self.doubleSpinBoxSimilarImageMinSimilarity.value():.2f}"
            ),
            "similar_image_max_results": self.spinBoxSimilarImageMaxResults.value(),
        }

    def apply_settings(self) -> bool:
        previous_values = self._config_manager.get_all()
        try:
            for key, value in self._values_from_controls().items():
                self._config_manager.set(key, value)
            self.colorPresetManager.save_settings()
            self._config_manager.save()
        except Exception as exc:
            logger.exception("保存设置失败")
            self._restore_manager(previous_values)
            self.colorPresetManager.reload_presets()
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
