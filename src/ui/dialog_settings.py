# coding=utf-8
"""设置对话框：由 SETTINGS_SCHEMA 自动装配。

页面、分组与控件全部来自配置 schema（单一事实来源）：
- 带 `ui.page` 的字段按出现顺序装配进对应页面；
- 无 `ui` 或 `page=None` 的字段只存在于配置文件，不进入界面与保存流程；
- 特殊页面通过 CUSTOM_PAGES 静态注册表接入，自行管理内容，
  仅通过 changed / reload_settings / save_settings 参与脏标记与保存。

保存只操作 ConfigManager 与配置文件；失败时回滚管理器并重载特殊页面。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from PyQt6 import uic
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QListWidgetItem,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import apppath
from config_manager import ConfigField, ConfigManager
from services.settings import PAGE_TITLES, create_settings_manager
from ui.settings_field_bindings import (
    build_field_widget,
    connect_field_signal,
    field_label_text,
    read_field_value,
    write_field_value,
)
from ui.settings_page_color_preset_manager import ColorPresetManagerWidget

logger = logging.getLogger(__name__)

PAGE_HEADING_POINT_SIZE = 13
GROUP_TITLE_POINT_SIZE = 11


@dataclass(frozen=True)
class PageSpec:
    """自定义页面的静态注册信息。

    factory 契约：构造参数为 ``(parent, config_manager=...)``，暴露
    ``changed`` 信号并提供 ``reload_settings()`` / ``save_settings()``。
    attribute 可选；给出时实例会以该名称挂到对话框上。
    """

    page_id: str
    title: str
    factory: Callable[..., QWidget]
    attribute: str | None = None


CUSTOM_PAGES: tuple[PageSpec, ...] = (
    PageSpec(
        page_id="color_presets",
        title="颜色预设",
        factory=ColorPresetManagerWidget,
        attribute="colorPresetManager",
    ),
)


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
        self._visible_fields: list[tuple[ConfigField, QWidget]] = []
        self._field_widgets: dict[str, QWidget] = {}
        self._custom_widgets: list[Any] = []

        self._assemble_pages()

        self._load_settings()
        self._connect_signals()

        self.listWidget.setCurrentRow(0)
        self.splitter.setCollapsible(0, False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([180, 600])
        self._apply_button.setEnabled(False)

    # --- 装配 ---

    def _assemble_pages(self) -> None:
        """按 schema 与 CUSTOM_PAGES 生成列表项、页面和控件。"""
        for page_id, fields in self._group_fields_by_page().items():
            self._add_list_entry(PAGE_TITLES.get(page_id, page_id))
            self.stackedWidget.addWidget(self._create_schema_page(fields))

        for spec in CUSTOM_PAGES:
            self._add_list_entry(spec.title)
            self.stackedWidget.addWidget(self._create_custom_page(spec))

    def _group_fields_by_page(self) -> dict[str, list[ConfigField]]:
        """收集可见字段并按 page 分组，保持 schema 首次出现顺序。"""
        pages: dict[str, list[ConfigField]] = {}
        for field in self._config_manager.schema.fields:
            ui = field.ui
            if ui is None or ui.page is None:
                continue
            pages.setdefault(ui.page, []).append(field)
        return pages

    def _add_list_entry(self, title: str) -> None:
        self.listWidget.addItem(QListWidgetItem(title))

    def _create_schema_page(
        self, fields: list[ConfigField]
    ) -> QWidget:
        page = QWidget(self)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)

        scroll_area = QScrollArea(page)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidgetResizable(True)

        content = QWidget(scroll_area)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(16)

        first_field = fields[0]
        heading = QLabel(self._page_heading(first_field), content)
        heading_font = heading.font()
        heading_font.setPointSize(PAGE_HEADING_POINT_SIZE)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        content_layout.addWidget(heading)

        # 基准字体需带显式属性（resolve mask），否则 setFont 对子控件
        # 不生效，行控件会继续继承分组标题的 11pt 粗体
        base_font = self.font()
        base_font.setPointSize(self.font().pointSize())
        base_font.setBold(False)
        forms: dict[str, QFormLayout] = {}
        page_form: QFormLayout | None = None

        for field in fields:
            widget = build_field_widget(field)
            self._register_field(field, widget)

            group = field.ui.group
            if not group:
                if page_form is None:
                    page_form = self._make_form()
                    content_layout.addLayout(page_form)
                self._add_form_row(page_form, field, widget, base_font)
                continue

            form = forms.get(group)
            if form is None:
                box = self._make_group_box(group, content)
                form = self._make_form()
                box.setLayout(form)
                content_layout.addWidget(box)
                forms[group] = form
            self._add_form_row(form, field, widget, base_font)

        content_layout.addStretch(1)
        scroll_area.setWidget(content)
        page_layout.addWidget(scroll_area)
        return page

    @staticmethod
    def _page_heading(field: ConfigField) -> str:
        ui = field.ui
        page = ui.page if ui else None
        if page is None:
            return ""
        return PAGE_TITLES.get(page, page)

    @staticmethod
    def _make_form() -> QFormLayout:
        form = QFormLayout()
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        form.setVerticalSpacing(12)
        return form

    @staticmethod
    def _make_group_box(title: str, parent: QWidget) -> QGroupBox:
        """分组框；标题的加大加粗通过控件自身字体实现。

        原生样式（如 Windows 的 windowsvista/windows11）绘制标题时忽略
        样式表里 ::title 子控件的字体属性，但始终使用控件字体，因此这里
        直接改控件字体，组内行控件则显式重置为基准字体避免连带加粗。
        """
        box = QGroupBox(title, parent)
        font = box.font()
        font.setPointSize(GROUP_TITLE_POINT_SIZE)
        font.setBold(True)
        box.setFont(font)
        return box

    def _add_form_row(
        self,
        form: QFormLayout,
        field: ConfigField,
        widget: QWidget,
        base_font: QFont,
    ) -> None:
        """添加一行表单；显式指定基准字体，避免继承分组标题的加粗。"""
        label = QLabel(field_label_text(field))
        label.setBuddy(widget)
        label.setFont(base_font)
        widget.setFont(base_font)
        form.addRow(label, widget)

    def _register_field(self, field: ConfigField, widget: QWidget) -> None:
        self._visible_fields.append((field, widget))
        self._field_widgets[field.key] = widget

    def _create_custom_page(self, spec: PageSpec) -> QWidget:
        page = QWidget(self)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)

        widget = spec.factory(page, config_manager=self._config_manager)
        page_layout.addWidget(widget)
        self._custom_widgets.append(widget)
        if spec.attribute:
            setattr(self, spec.attribute, widget)
        widget.changed.connect(self._mark_dirty)
        return page

    def field_widget(self, key: str) -> QWidget:
        """按配置键访问自动生成的控件（供测试与调试使用）。"""
        try:
            return self._field_widgets[key]
        except KeyError:
            raise KeyError(f"设置界面没有配置项 {key} 对应的控件") from None

    # --- 加载 / 保存 ---

    def _load_settings(self) -> None:
        for field, widget in self._visible_fields:
            write_field_value(
                field, widget, self._config_manager.get(field.key)
            )

    def _connect_signals(self) -> None:
        self.listWidget.currentRowChanged.connect(
            self.stackedWidget.setCurrentIndex
        )
        self.buttonBox.accepted.connect(self._accept_settings)
        self.buttonBox.rejected.connect(self.reject)
        self._apply_button.clicked.connect(self.apply_settings)

        for field, widget in self._visible_fields:
            connect_field_signal(field, widget, self._mark_dirty)

    def _mark_dirty(self, _value=None) -> None:
        self._apply_button.setEnabled(True)

    def apply_settings(self) -> bool:
        previous_values = self._config_manager.get_all()
        try:
            for field, widget in self._visible_fields:
                self._config_manager.set(
                    field.key, read_field_value(field, widget)
                )
            for widget in self._custom_widgets:
                widget.save_settings()
            self._config_manager.save()
        except Exception as exc:
            logger.exception("保存设置失败")
            self._restore_manager(previous_values)
            for widget in self._custom_widgets:
                widget.reload_settings()
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
