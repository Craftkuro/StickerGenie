# coding=utf-8
import logging

from PyQt6.QtCore import QModelIndex, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QStandardItemModel
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

import commons.constants
from commons.roles import ROLE_BLOB_ENTITY
import services.global_instances
from services.thumbnail_provider import ThumbnailProvider

logger = logging.getLogger(__name__)


class StickerItemDelegate(QStyledItemDelegate):
    """Draw a centered thumbnail without reserving space for item text."""

    PADDING = 8
    ITEM_SIZE = 160

    def __init__(
        self,
        parent: QWidget | None = None,
        thumbnail_provider: ThumbnailProvider | None = None,
    ):
        super().__init__(parent)
        self._thumbnail_provider = (
            thumbnail_provider
            or services.global_instances.current_thumbnail_provider
            or ThumbnailProvider()
        )

    def set_thumbnail_provider(self, thumbnail_provider: ThumbnailProvider) -> None:
        self._thumbnail_provider = thumbnail_provider

    def _pixmap_for_index(
        self,
        index: QModelIndex,
        requested_size: QSize,
        mode: QIcon.Mode,
    ) -> QPixmap:
        blob_entity = index.data(ROLE_BLOB_ENTITY)
        if blob_entity is not None:
            return self._thumbnail_provider.request_thumbnail(blob_entity)

        icon_data = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon_data, QIcon):
            available_sizes = icon_data.availableSizes()
            if available_sizes:
                source_size = max(
                    available_sizes,
                    key=lambda size: size.width() * size.height(),
                )
                pixmap = icon_data.pixmap(source_size, mode, QIcon.State.Off)
            else:
                pixmap = icon_data.pixmap(
                    requested_size,
                    mode,
                    QIcon.State.Off,
                )
            if not pixmap.isNull():
                return pixmap
        return QPixmap()

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
            pixmap = self._pixmap_for_index(index, icon_rect.size(), mode)
            if pixmap.isNull():
                return
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

    THUMBNAIL_SIZE = commons.constants.THUMBNAIL_SIZE
    ITEM_SIZE = StickerItemDelegate.ITEM_SIZE

    def __init__(
        self,
        model: QStandardItemModel | QWidget | None = None,
        parent: QWidget | None = None,
        thumbnail_provider: ThumbnailProvider | None = None,
    ):
        # uic creates custom widgets with the parent as the first positional argument.
        if isinstance(model, QWidget):
            parent = model
            model = None

        super().__init__(parent)

        self.display_mode = commons.constants.LIST_DISPLAY_MODE_ICON
        self.sort_mode = commons.constants.SORT_BY_DATE
        self.reverse_sort = False
        self._thumbnail_provider: ThumbnailProvider | None = None

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
        self.set_thumbnail_provider(
            thumbnail_provider
            or services.global_instances.current_thumbnail_provider
            or ThumbnailProvider()
        )

        if model is not None:
            self.setModel(model)

    def set_thumbnail_provider(self, thumbnail_provider: ThumbnailProvider) -> None:
        if self._thumbnail_provider is not None:
            try:
                self._thumbnail_provider.thumbnail_ready.disconnect(
                    self._on_thumbnail_ready
                )
            except TypeError:
                pass
        self._thumbnail_provider = thumbnail_provider
        self.setItemDelegate(StickerItemDelegate(self, thumbnail_provider))
        if thumbnail_provider is not None:
            thumbnail_provider.thumbnail_ready.connect(self._on_thumbnail_ready)

    def _on_thumbnail_ready(self, _file_hash, _image) -> None:
        """缩略图就绪后只重绘匹配的可见 item，避免 4K 大视口整屏重绘。"""
        model = self.model()
        if model is None:
            return

        row_count = model.rowCount()
        if row_count <= 0:
            return

        # 只扫描可见行区间；滚出视口的 item 等回到可见区时自然会重绘。
        start_row = 0
        end_row = row_count - 1
        first_index = self.indexAt(self.viewport().rect().topLeft())
        last_index = self.indexAt(self.viewport().rect().bottomRight())
        if first_index.isValid():
            start_row = first_index.row()
        if last_index.isValid():
            end_row = min(end_row, last_index.row())

        for row in range(start_row, end_row + 1):
            index = model.index(row, 0)
            blob_entity = index.data(ROLE_BLOB_ENTITY)
            if blob_entity is not None and blob_entity.hash == _file_hash:
                self._update_item(index)
                return

    def _update_item(self, index: QModelIndex) -> None:
        """只重绘指定 item 占据的区域（QAbstractItemView::update(index)）。"""
        self.update(index)
