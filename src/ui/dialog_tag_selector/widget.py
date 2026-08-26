"""标签多选组件 TagSelectorWidget。

从数据库加载全部标签（按 order、id 升序排列）；左侧列表显示可选标签并支持
搜索过滤，右侧列表显示已选标签。通过“添加 → / ← 移除”按钮或双击条目在两个
列表之间移动标签；点击“确定”时通过 ok_clicked 信号返回选中的标签 id 列表。
点击“新建标签”会打开 NewTagDialog，保存后自动刷新列表并选中新标签。
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

import services.global_instances
from commons.dto import Tag

from .dialog_new_tag import NewTagDialog
from .matcher import SubstringTagSearchMatcher, TagSearchMatcher

logger = logging.getLogger(__name__)


TAG_DATA_ROLE = Qt.ItemDataRole.UserRole
TAG_ID_ROLE = Qt.ItemDataRole.UserRole + 1
TAG_ACCENT_COLOR_ROLE = Qt.ItemDataRole.UserRole + 2


class TagSelectorItemDelegate(QStyledItemDelegate):
    """在原生图标槽位上自绘标签 color_rgb 对应的强调色圆点。

    item 携带全透明图标让样式按原生图标布局排版（背景、选中、焦点、
    行高全部由样式负责，任何风格下都是整行正确绘制），delegate 只在
    SE_ItemViewItemDecoration 槽位上以设备分辨率补画彩色圆点。
    """

    SWATCH_DIAMETER = 12
    FALLBACK_COLOR = "#2196F3"

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index,
    ) -> None:
        init_option = QStyleOptionViewItem(option)
        self.initStyleOption(init_option, index)
        super().paint(painter, init_option, index)

        widget = init_option.widget
        style = widget.style() if widget is not None else QApplication.style()
        deco_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemDecoration,
            init_option,
            widget,
        )
        if deco_rect.isEmpty():
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._accent_color(index))
        painter.drawEllipse(deco_rect)
        painter.restore()

    @staticmethod
    def transparent_icon() -> QIcon:
        pixmap = QPixmap(
            TagSelectorItemDelegate.SWATCH_DIAMETER,
            TagSelectorItemDelegate.SWATCH_DIAMETER,
        )
        pixmap.fill(Qt.GlobalColor.transparent)
        return QIcon(pixmap)

    def _accent_color(self, index) -> QColor:
        color = QColor(index.data(TAG_ACCENT_COLOR_ROLE))
        if not color.isValid():
            color = QColor(self.FALLBACK_COLOR)
        return color


class TagSelectorWidget(QWidget):
    """可搜索的双列表标签选择组件：左侧可选、右侧已选。"""

    ok_clicked = pyqtSignal(list)

    def __init__(
        self,
        database=None,
        *,
        selected_tag_ids: Iterable[int] = (),
        matcher: Optional[TagSearchMatcher] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._database = (
            database
            if database is not None
            else services.global_instances.current_library_db
        )
        if self._database is None:
            raise RuntimeError("仓库数据库尚未初始化。")
        self._matcher = matcher or SubstringTagSearchMatcher()
        self._selected_ids: set[int] = set(selected_tag_ids)
        self._all_tags: List[Tag] = []

        self._build_ui()
        self._connect_signals()
        self.reload_tags()

    # -- public API -------------------------------------------------------

    def reload_tags(self) -> None:
        """重新从数据库加载全部标签并重建左右两个列表。"""
        try:
            tags = self._database.list_tags()
        except Exception:
            logger.exception("加载标签失败")
            tags = []
        self._all_tags = sorted(tags, key=lambda tag: (tag.order, tag.id))
        self._rebuild_available()
        self._rebuild_selected()

    def selected_tag_ids(self) -> List[int]:
        """按右侧列表显示顺序返回当前选中的标签 id。"""
        return [
            self.selected_list_widget.item(row).data(TAG_ID_ROLE)
            for row in range(self.selected_list_widget.count())
        ]

    def selected_tags(self) -> List[Tag]:
        """按右侧列表显示顺序返回当前选中的标签。"""
        return [
            self.selected_list_widget.item(row).data(TAG_DATA_ROLE)
            for row in range(self.selected_list_widget.count())
        ]

    def set_selected_tag_ids(self, tag_ids: Iterable[int]) -> None:
        """设置已选标签；数据库中不存在的 id 会被忽略。"""
        self._selected_ids = set(tag_ids)
        self._rebuild_selected()

    # -- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        self.search_box = QLineEdit(self)
        self.search_box.setPlaceholderText("搜索标签…")
        self.search_box.setClearButtonEnabled(True)
        layout.addWidget(self.search_box)

        lists_row = QHBoxLayout()
        lists_row.setSpacing(8)

        self.available_list_widget = self._make_list()
        lists_row.addWidget(self.available_list_widget, 1)

        button_column = QVBoxLayout()
        button_column.addStretch()
        self.add_button = QPushButton("添加 →", self)
        button_column.addWidget(self.add_button)
        self.remove_button = QPushButton("← 移除", self)
        button_column.addWidget(self.remove_button)
        button_column.addStretch()
        lists_row.addLayout(button_column)

        self.selected_list_widget = self._make_list()
        lists_row.addWidget(self.selected_list_widget, 1)

        layout.addLayout(lists_row, 1)

        button_row = QHBoxLayout()
        self.new_tag_button = QPushButton("新建标签", self)
        button_row.addWidget(self.new_tag_button)
        button_row.addStretch()
        self.ok_button = QPushButton("确定", self)
        self.ok_button.setDefault(True)
        button_row.addWidget(self.ok_button)
        layout.addLayout(button_row)

    def _make_list(self) -> QListWidget:
        list_widget = QListWidget(self)
        list_widget.setItemDelegate(TagSelectorItemDelegate(list_widget))
        list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        return list_widget

    def _connect_signals(self) -> None:
        self.search_box.textChanged.connect(self._apply_search)
        self.add_button.clicked.connect(self._add_selected_tags)
        self.remove_button.clicked.connect(self._remove_selected_tags)
        self.available_list_widget.itemDoubleClicked.connect(
            self._add_single_tag
        )
        self.selected_list_widget.itemDoubleClicked.connect(
            self._remove_single_tag
        )
        self.new_tag_button.clicked.connect(self._create_new_tag)
        self.ok_button.clicked.connect(self._on_ok_clicked)

    # -- items ------------------------------------------------------------

    def _rebuild_available(self) -> None:
        self.available_list_widget.blockSignals(True)
        try:
            self.available_list_widget.clear()
            for tag in self._all_tags:
                self.available_list_widget.addItem(self._make_item(tag))
        finally:
            self.available_list_widget.blockSignals(False)
        self._apply_search(self.search_box.text())

    def _rebuild_selected(self) -> None:
        self.selected_list_widget.blockSignals(True)
        try:
            self.selected_list_widget.clear()
            for tag in self._all_tags:
                if tag.id in self._selected_ids:
                    self.selected_list_widget.addItem(self._make_item(tag))
        finally:
            self.selected_list_widget.blockSignals(False)

    def _make_item(self, tag: Tag) -> QListWidgetItem:
        item = QListWidgetItem(tag.name)
        item.setData(TAG_DATA_ROLE, tag)
        item.setData(TAG_ID_ROLE, tag.id)
        item.setData(TAG_ACCENT_COLOR_ROLE, tag.color_rgb)
        item.setIcon(TagSelectorItemDelegate.transparent_icon())
        if not tag.enabled:
            disabled_color = self.palette().color(
                QPalette.ColorGroup.Disabled,
                QPalette.ColorRole.Text,
            )
            item.setForeground(disabled_color)
            item.setToolTip("已禁用")
        return item

    # -- handlers ---------------------------------------------------------

    def _add_selected_tags(self) -> None:
        """把左侧选中的标签加入右侧，已存在的会自动去重。"""
        tag_ids = [
            item.data(TAG_ID_ROLE)
            for item in self.available_list_widget.selectedItems()
        ]
        if not tag_ids:
            return
        self._selected_ids.update(tag_ids)
        self._rebuild_selected()

    def _remove_selected_tags(self) -> None:
        """把右侧选中的标签移回左侧。"""
        tag_ids = [
            item.data(TAG_ID_ROLE)
            for item in self.selected_list_widget.selectedItems()
        ]
        if not tag_ids:
            return
        for tag_id in tag_ids:
            self._selected_ids.discard(tag_id)
        self._rebuild_selected()

    def _add_single_tag(self, item: QListWidgetItem) -> None:
        self._selected_ids.add(item.data(TAG_ID_ROLE))
        self._rebuild_selected()

    def _remove_single_tag(self, item: QListWidgetItem) -> None:
        self._selected_ids.discard(item.data(TAG_ID_ROLE))
        self._rebuild_selected()

    def _apply_search(self, text: str) -> None:
        visible_ids = {
            tag.id for tag in self._matcher.filter_tags(self._all_tags, text)
        }
        for row in range(self.available_list_widget.count()):
            item = self.available_list_widget.item(row)
            item.setHidden(item.data(TAG_ID_ROLE) not in visible_ids)

    def _create_new_tag(self) -> None:
        """打开新建标签对话框；保存成功后刷新标签列表并选中新标签。"""
        dialog = NewTagDialog(self, database=self._database)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.reload_tags()
            self._select_available_tag(dialog.new_tag_id)

    def _select_available_tag(self, tag_id: Optional[int]) -> None:
        """在左侧列表选中指定标签，并清除搜索框确保其可见。"""
        if tag_id is None:
            return
        self.search_box.clear()
        for row in range(self.available_list_widget.count()):
            item = self.available_list_widget.item(row)
            if item.data(TAG_ID_ROLE) == tag_id:
                self.available_list_widget.setCurrentItem(item)
                self.available_list_widget.scrollToItem(item)
                break

    def _on_ok_clicked(self) -> None:
        self.ok_clicked.emit(self.selected_tag_ids())
