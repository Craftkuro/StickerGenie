# coding=utf-8
import logging

from PyQt6.QtCore import QModelIndex, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QStandardItemModel
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

import commons.constants
#import commons.classes

logger = logging.getLogger(__name__)


class StickerItemDelegate(QStyledItemDelegate):
    """Draw a centered thumbnail without reserving space for item text."""

    PADDING = 8
    ITEM_SIZE = 160

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        painter.save()
        try:
            highlight_color = option.palette.highlight().color()
            if option.state & QStyle.StateFlag.State_Selected:
                fill_color = QColor(highlight_color)
                fill_color.setAlpha(36)
                painter.fillRect(option.rect, fill_color)
                painter.setPen(QPen(highlight_color, 2))
                painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
            elif option.state & QStyle.StateFlag.State_MouseOver:
                fill_color = QColor(highlight_color)
                fill_color.setAlpha(18)
                painter.fillRect(option.rect, fill_color)

            icon_data = index.data(Qt.ItemDataRole.DecorationRole)
            icon = icon_data if isinstance(icon_data, QIcon) else QIcon()

            icon_rect = option.rect.adjusted(
                self.PADDING,
                self.PADDING,
                -self.PADDING,
                -self.PADDING,
            )
            mode = (
                QIcon.Mode.Normal
                if option.state & QStyle.StateFlag.State_Enabled
                else QIcon.Mode.Disabled
            )
            pixmap = icon.pixmap(icon_rect.size(), mode, QIcon.State.Off)
            pixmap = pixmap.scaled(
                icon_rect.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pixmap_rect = pixmap.rect()
            pixmap_rect.moveCenter(icon_rect.center())
            painter.drawPixmap(pixmap_rect, pixmap)
        finally:
            painter.restore()

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QSize:
        return QSize(self.ITEM_SIZE, self.ITEM_SIZE)


class StickerListView(QListView):
    """
    通用的表情包列表视图
    """

    THUMBNAIL_SIZE = 144
    ITEM_SIZE = StickerItemDelegate.ITEM_SIZE

    def __init__(
        self,
        model: QStandardItemModel | QWidget | None = None,
        parent: QWidget | None = None,
    ):
        # uic creates custom widgets with the parent as the first positional argument.
        if isinstance(model, QWidget):
            parent = model
            model = None

        super().__init__(parent)

        self.display_mode = commons.constants.LIST_DISPLAY_MODE_ICON
        self.sort_mode = commons.constants.SORT_BY_DATE
        self.reverse_sort = False

        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.setIconSize(QSize(self.THUMBNAIL_SIZE, self.THUMBNAIL_SIZE))
        self.setGridSize(QSize(self.ITEM_SIZE, self.ITEM_SIZE))
        self.setSpacing(8)
        self.setUniformItemSizes(True)
        self.setWordWrap(False)
        self.setItemDelegate(StickerItemDelegate(self))

        if model is not None:
            self.setModel(model)
