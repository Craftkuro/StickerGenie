# coding=utf-8
import logging

from PyQt6.QtCore import QModelIndex, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFontMetrics,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QStandardItemModel,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

import commons.constants
from commons.roles import ROLE_BLOB_ENTITY, ROLE_SIMILARITY
import services.global_instances
from services.thumbnail_provider import ThumbnailProvider

logger = logging.getLogger(__name__)


class StickerItemDelegate(QStyledItemDelegate):
    """Draw a centered thumbnail without reserving space for item text."""

    PADDING = 8
    ITEM_SIZE = 160
    SIMILARITY_BADGE_MARGIN = 1
    SIMILARITY_BADGE_PADDING_X = 4
    SIMILARITY_BADGE_PADDING_Y = 2
    SIMILARITY_BADGE_FONT_POINT_SIZE = 7
    SIMILARITY_BADGE_BACKGROUND = QColor("#FFD400")
    SIMILARITY_BADGE_FOREGROUND = QColor("#000000")

    def __init__(
        self,
        parent: QWidget | None = None,
        thumbnail_provider: ThumbnailProvider | None = None,
    ):
        super().__init__(parent)
        self._item_size = self.ITEM_SIZE
        self._thumbnail_provider = (
            thumbnail_provider
            or services.global_instances.current_thumbnail_provider
            or ThumbnailProvider()
        )

    def set_thumbnail_provider(self, thumbnail_provider: ThumbnailProvider) -> None:
        self._thumbnail_provider = thumbnail_provider

    def set_item_size(self, size: int) -> None:
        """设置 item 外框边长，尺寸变化后由视图统一触发重新布局。"""
        self._item_size = max(1, int(size))

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

            # 对于有相似度数据的图，在右上角画一个相似度的角标
            similarity = index.data(ROLE_SIMILARITY)
            if similarity is not None:
                self._draw_similarity_badge(
                    painter,
                    pixmap_rect,
                    float(similarity),
                )
        finally:
            painter.restore()

    def _draw_similarity_badge(
        self,
        painter: QPainter,
        thumbnail_rect: QRect,
        similarity: float,
    ) -> None:
        """在缩略图右上角绘制固定大小的相似度角标。"""
        text = f"{similarity:.1%}"
        font = painter.font()
        font.setPointSize(self.SIMILARITY_BADGE_FONT_POINT_SIZE)
        font.setBold(True)
        painter.setFont(font)

        metrics = QFontMetrics(font)
        badge_width = (
            metrics.horizontalAdvance(text)
            + 2 * self.SIMILARITY_BADGE_PADDING_X
        )
        badge_height = (
            metrics.height() + 2 * self.SIMILARITY_BADGE_PADDING_Y
        )
        badge_rect = QRect(
            thumbnail_rect.right() - badge_width - self.SIMILARITY_BADGE_MARGIN,
            thumbnail_rect.top() + self.SIMILARITY_BADGE_MARGIN,
            badge_width,
            badge_height,
        )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.SIMILARITY_BADGE_BACKGROUND)
        painter.drawRoundedRect(badge_rect, 3, 3)
        painter.setPen(self.SIMILARITY_BADGE_FOREGROUND)
        painter.drawText(
            badge_rect,
            Qt.AlignmentFlag.AlignCenter,
            text,
        )

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QSize:
        return QSize(self._item_size, self._item_size)


class StickerListView(QListView):
    """
    通用的表情包列表视图
    """

    # 即将滚动到列表底部时发出，由无限集合标签页负责响应。
    load_more_requested = pyqtSignal()

    THUMBNAIL_SIZE = commons.constants.THUMBNAIL_SIZE
    ITEM_SIZE = StickerItemDelegate.ITEM_SIZE
    LOAD_MORE_THRESHOLD = 64

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
        self._item_size = self.ITEM_SIZE

        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.setIconSize(QSize(self.THUMBNAIL_SIZE, self.THUMBNAIL_SIZE))
        self.setGridSize(QSize(self._item_size, self._item_size))
        self.setSpacing(8)
        self.setUniformItemSizes(True)
        self.setWordWrap(False)

        self._load_more_timer = QTimer(self)
        self._load_more_timer.setSingleShot(True)
        self._load_more_timer.setInterval(0)
        self._load_more_timer.timeout.connect(self.check_load_more)
        self.verticalScrollBar().valueChanged.connect(
            self._on_vertical_scrollbar_changed
        )
        self.set_thumbnail_provider(
            thumbnail_provider
            or services.global_instances.current_thumbnail_provider
            or ThumbnailProvider()
        )

        if model is not None:
            self.setModel(model)

    def setModel(self, model) -> None:
        """安装模型并跟踪行数变化，以便在首屏不足一屏时也能触发加载更多。"""
        previous_model = self.model()
        if previous_model is not None:
            try:
                previous_model.rowsInserted.disconnect(
                    self._on_model_rows_inserted
                )
            except TypeError:
                pass

        super().setModel(model)
        if model is not None:
            model.rowsInserted.connect(self._on_model_rows_inserted)
            self._load_more_timer.start()

    def _on_model_rows_inserted(self, _parent, _start, _end) -> None:
        self._load_more_timer.start()

    def _on_vertical_scrollbar_changed(self, _value: int) -> None:
        self.check_load_more()

    def check_load_more(self) -> None:
        """在即将滚动到底部（或内容不足一屏）时发出加载更多请求。"""
        model = self.model()
        if model is None or model.rowCount() <= 0:
            return

        scrollbar = self.verticalScrollBar()
        if scrollbar.maximum() > 0:
            if (
                scrollbar.value()
                < scrollbar.maximum() - self.LOAD_MORE_THRESHOLD
            ):
                return
        self.load_more_requested.emit()

    def item_size(self) -> int:
        """返回当前 item 外框边长。"""
        return self._item_size

    def set_display_size(self, size: int) -> None:
        """调整图片显示大小（类似 Windows 7 资源管理器的滑块）。"""
        size = max(32, min(int(size), 512))
        self._item_size = size
        delegate = self.itemDelegate()
        if isinstance(delegate, StickerItemDelegate):
            delegate.set_item_size(size)
        self.setGridSize(QSize(size, size))

    def set_thumbnail_provider(self, thumbnail_provider: ThumbnailProvider) -> None:
        if self._thumbnail_provider is not None:
            try:
                self._thumbnail_provider.thumbnail_ready.disconnect(
                    self._on_thumbnail_ready
                )
            except TypeError:
                pass
        self._thumbnail_provider = thumbnail_provider
        delegate = StickerItemDelegate(self, thumbnail_provider)
        delegate.set_item_size(self._item_size)
        self.setItemDelegate(delegate)
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
