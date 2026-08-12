# coding=utf-8
"""Popup widget for adjusting similarity result filter parameters.

Hosted inside a QToolButton popup menu on the SimilarImagesPage toolbar.
Emits *filter_applied* when the user clicks the apply button; the receiver
is responsible for re-running the filter and rebuilding the model.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from services.similarity_result_filter import SimilarityFilterConfig


class SimilarityFilterPopupWidget(QWidget):
    """Compact panel with a checkbox, two sliders, two spinboxes, and Apply."""

    filter_applied = pyqtSignal(bool, object)

    def __init__(
        self,
        initial_config: SimilarityFilterConfig,
        initial_enabled: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._build_ui(initial_config, initial_enabled)
        self._connect_signals()

    # -- public -----------------------------------------------------------

    def update_state(
        self,
        enabled: bool,
        config: SimilarityFilterConfig,
    ) -> None:
        """Sync widget controls to the given filter state."""
        self._enabled_checkbox.setChecked(enabled)
        self._drop_ratio_slider.setValue(int(config.target_drop_ratio * 100))
        self._min_sim_slider.setValue(int(config.min_similarity * 100))
        self._min_keep_spin.setValue(config.min_keep)
        self._max_results_spin.setValue(config.max_results)
        self._set_controls_enabled(enabled)
        self._update_drop_ratio_label(self._drop_ratio_slider.value())
        self._update_min_sim_label(self._min_sim_slider.value())

    # -- layout -----------------------------------------------------------

    def _build_ui(
        self,
        config: SimilarityFilterConfig,
        enabled: bool,
    ) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(8)

        self._enabled_checkbox = QCheckBox("启用过滤", self)
        self._enabled_checkbox.setChecked(enabled)
        outer.addWidget(self._enabled_checkbox)

        self._drop_ratio_slider = self._make_slider(1, 99)
        self._drop_ratio_slider.setValue(int(config.target_drop_ratio * 100))
        self._drop_ratio_label = QLabel(f"{config.target_drop_ratio:.2f}", self)
        self._drop_ratio_label.setFixedWidth(36)
        outer.addLayout(
            self._make_slider_row(
                "累计下降比例", self._drop_ratio_slider, self._drop_ratio_label
            )
        )

        self._min_sim_slider = self._make_slider(0, 100)
        self._min_sim_slider.setValue(int(config.min_similarity * 100))
        self._min_sim_label = QLabel(f"{config.min_similarity:.2f}", self)
        self._min_sim_label.setFixedWidth(36)
        outer.addLayout(
            self._make_slider_row(
                "最低相似度", self._min_sim_slider, self._min_sim_label
            )
        )

        self._min_keep_spin = QSpinBox(self)
        self._min_keep_spin.setRange(0, 999)
        self._min_keep_spin.setValue(config.min_keep)
        self._max_results_spin = QSpinBox(self)
        self._max_results_spin.setRange(1, 9999)
        self._max_results_spin.setValue(config.max_results)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("最少保留条数", self._min_keep_spin)
        form.addRow("最多返回条数", self._max_results_spin)
        outer.addLayout(form)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self._apply_button = QPushButton("应用", self)
        button_row.addWidget(self._apply_button)
        outer.addLayout(button_row)

        self._set_controls_enabled(enabled)

    def _make_slider(self, minimum: int, maximum: int) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal, self)
        slider.setRange(minimum, maximum)
        slider.setSingleStep(1)
        slider.setFixedWidth(160)
        return slider

    @staticmethod
    def _make_slider_row(
        label_text: str,
        slider: QSlider,
        value_label: QLabel,
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel(label_text)
        label.setFixedWidth(84)
        row.addWidget(label)
        row.addWidget(slider)
        row.addWidget(value_label)
        return row

    # -- signals ----------------------------------------------------------

    def _connect_signals(self) -> None:
        self._enabled_checkbox.toggled.connect(self._set_controls_enabled)
        self._drop_ratio_slider.valueChanged.connect(
            self._update_drop_ratio_label
        )
        self._min_sim_slider.valueChanged.connect(self._update_min_sim_label)
        self._apply_button.clicked.connect(self._emit_filter)

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self._drop_ratio_slider,
            self._drop_ratio_label,
            self._min_sim_slider,
            self._min_sim_label,
            self._min_keep_spin,
            self._max_results_spin,
        ):
            widget.setEnabled(enabled)

    def _update_drop_ratio_label(self, value: int) -> None:
        self._drop_ratio_label.setText(f"{value / 100.0:.2f}")

    def _update_min_sim_label(self, value: int) -> None:
        self._min_sim_label.setText(f"{value / 100.0:.2f}")

    def _emit_filter(self) -> None:
        config = SimilarityFilterConfig(
            target_drop_ratio=self._drop_ratio_slider.value() / 100.0,
            min_keep=self._min_keep_spin.value(),
            min_similarity=self._min_sim_slider.value() / 100.0,
            max_results=self._max_results_spin.value(),
        )
        self.filter_applied.emit(
            self._enabled_checkbox.isChecked(),
            config,
        )
