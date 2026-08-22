# coding=utf-8
"""设置字段绑定适配表。

按 WidgetKind 查表，为单个配置项完成控件的构建、取值、赋值和变更信号
接线。本模块只依赖 PyQt6 Widgets 与 config_manager 的 schema 数据结构，
可在 offscreen 环境下单独测试；设置对话框的自动装配层通过这里暴露的
四个函数操作控件，不感知具体控件类型。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
    QWidget,
)

from config_manager import ConfigField, FieldUI, WidgetKind


class SettingsBindingError(ValueError):
    """设置控件装配或读写失败（schema 声明与控件组合非法）。"""


def _build_spin_box(field: ConfigField) -> QWidget:
    ui = field.ui
    widget = QSpinBox()
    if ui.minimum is not None:
        widget.setMinimum(int(ui.minimum))
    if ui.maximum is not None:
        widget.setMaximum(int(ui.maximum))
    if ui.step is not None:
        widget.setSingleStep(int(ui.step))
    if ui.suffix:
        widget.setSuffix(ui.suffix)
    return widget


def _build_spin_box_2p(field: ConfigField) -> QWidget:
    ui = field.ui
    widget = QDoubleSpinBox()
    widget.setDecimals(2)
    if ui.minimum is not None:
        widget.setMinimum(ui.minimum)
    if ui.maximum is not None:
        widget.setMaximum(ui.maximum)
    if ui.step is not None:
        widget.setSingleStep(ui.step)
    if ui.suffix:
        widget.setSuffix(ui.suffix)
    return widget


def _build_combo_box(field: ConfigField) -> QWidget:
    ui = field.ui
    if not ui.choices:
        raise SettingsBindingError(
            f"配置项 {field.key} 声明为 COMBO_BOX，但未提供 choices"
        )
    widget = QComboBox()
    for text, data in ui.choices:
        widget.addItem(text, data)
    return widget


def _build_check_box(field: ConfigField) -> QWidget:
    return QCheckBox()


def _read_spin_box(widget: QWidget) -> int:
    return widget.value()


def _read_spin_box_2p(widget: QWidget) -> float:
    return widget.value()


def _read_combo_box(widget: QWidget) -> Any:
    return widget.currentData()


def _read_check_box(widget: QWidget) -> bool:
    return widget.isChecked()


def _write_spin_box(field: ConfigField, widget: QWidget, value: Any) -> None:
    widget.setValue(int(value))


def _write_spin_box_2p(
    field: ConfigField, widget: QWidget, value: Any
) -> None:
    widget.setValue(float(value))


def _write_combo_box(field: ConfigField, widget: QWidget, value: Any) -> None:
    index = widget.findData(value)
    if index < 0:
        raise SettingsBindingError(
            f"配置项 {field.key} 的值 {value!r} 不在 choices 中"
        )
    widget.setCurrentIndex(index)


def _write_check_box(field: ConfigField, widget: QWidget, value: Any) -> None:
    widget.setChecked(bool(value))


def _connect_value_changed(widget: QWidget, callback: Callable) -> None:
    widget.valueChanged.connect(callback)


def _connect_current_index_changed(
    widget: QWidget, callback: Callable
) -> None:
    widget.currentIndexChanged.connect(callback)


def _connect_toggled(widget: QWidget, callback: Callable) -> None:
    widget.toggled.connect(callback)


@dataclass(frozen=True)
class _Binding:
    """一种 WidgetKind 的构建/读/写/信号接线能力集合。"""

    build: Callable[[ConfigField], QWidget]
    read: Callable[[QWidget], Any]
    write: Callable[[ConfigField, QWidget, Any], None]
    connect: Callable[[QWidget, Callable], None]


_BINDINGS: dict[WidgetKind, _Binding] = {
    WidgetKind.SPIN_BOX: _Binding(
        build=_build_spin_box,
        read=_read_spin_box,
        write=_write_spin_box,
        connect=_connect_value_changed,
    ),
    WidgetKind.SPIN_BOX_2P: _Binding(
        build=_build_spin_box_2p,
        read=_read_spin_box_2p,
        write=_write_spin_box_2p,
        connect=_connect_value_changed,
    ),
    WidgetKind.COMBO_BOX: _Binding(
        build=_build_combo_box,
        read=_read_combo_box,
        write=_write_combo_box,
        connect=_connect_current_index_changed,
    ),
    WidgetKind.CHECK_BOX: _Binding(
        build=_build_check_box,
        read=_read_check_box,
        write=_write_check_box,
        connect=_connect_toggled,
    ),
}


def _binding_for(field: ConfigField) -> _Binding:
    ui = field.ui
    if ui is None:
        raise SettingsBindingError(
            f"配置项 {field.key} 没有界面描述（ui=None），无法装配到设置界面"
        )
    try:
        return _BINDINGS[ui.kind]
    except KeyError:
        raise SettingsBindingError(
            f"配置项 {field.key} 的控件类型 {ui.kind} 不受支持"
        ) from None


def build_field_widget(field: ConfigField) -> QWidget:
    """按 schema 字段声明构建控件（objectName 与 tooltip 一并设置）。"""
    binding = _binding_for(field)
    widget = binding.build(field)
    widget.setObjectName(f"field_{field.key}")
    widget.setToolTip(field.comment)
    return widget


def field_label_text(field: ConfigField) -> str:
    """返回表单行标签文本；未标注时回退使用配置键名。"""
    ui = field.ui
    return (ui.label or field.key) if ui else field.key


def read_field_value(field: ConfigField, widget: QWidget) -> Any:
    """从控件读取可写入配置的值。"""
    return _binding_for(field).read(widget)


def write_field_value(field: ConfigField, widget: QWidget, value: Any) -> None:
    """把配置值写入控件。"""
    _binding_for(field).write(field, widget, value)


def connect_field_signal(
    field: ConfigField, widget: QWidget, callback: Callable
) -> None:
    """把控件的变更信号连接到回调。"""
    _binding_for(field).connect(widget, callback)
