# coding=utf-8
import logging
from dataclasses import dataclass, field

from PyQt6.QtCore import QModelIndex, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPalette,
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
from commons.roles import ROLE_BLOB_ENTITY, ROLE_SIMILARITY, ROLE_STICKER_IMAGE
import services.global_instances
from services.thumbnail_provider import ThumbnailProvider

logger = logging.getLogger(__name__)


# 详细信息模式标签圆角片的样式常量，配色参照 custom_tag_widget.TagItemDelegate。
TAG_CHIP_PAD_X = 8
TAG_CHIP_PAD_Y = 3
TAG_CHIP_GAP = 6
TAG_CHIP_CORNER_RADIUS = 5
TAG_CHIP_BACKGROUND = QColor("#E3F2FD")
TAG_CHIP_TEXT = QColor("#1565C0")
TAG_CHIP_BORDER = QColor("#2196F3")
TAG_CHIP_ACCENT_ALPHA = 35
MORE_BADGE_BACKGROUND = QColor("#9E9E9E")
MORE_BADGE_FOREGROUND = QColor("#FFFFFF")
MORE_BADGE_PAD_X = 4


@dataclass(frozen=True)
class TagChipLayout:
    """一行内标签圆角片的布局结果。"""

    chips: list[tuple[QRect, str]] = field(default_factory=list)
    hidden_count: int = 0


def layout_tag_chips(
    text_rect: QRect,
    tags: list,
    metrics: QFontMetrics,
) -> TagChipLayout:
    """计算标签圆角片在一行内的布局，放不下的折叠进 hidden_count。

    输入顺序即展示顺序（与图片查看器标签编辑框一致），本函数不做重排。
    """
    chip_height = metrics.height() + 2 * TAG_CHIP_PAD_Y
    top = text_rect.center().y() - chip_height // 2
    limit = text_rect.right() + 1

    chips: list[tuple[QRect, str]] = []
    x = text_rect.left()
    for tag in tags:
        width = metrics.horizontalAdvance(tag.name) + 2 * TAG_CHIP_PAD_X
        if x + width > limit:
            break
        chips.append((QRect(x, top, width, chip_height), tag.name))
        x += width + TAG_CHIP_GAP
    hidden_count = len(tags) - len(chips)

    if hidden_count > 0:
        # 行尾预留 "+N" 徽标位置；与最后一个圆角片重叠时继续折叠。
        badge_text = f"+{hidden_count}"
        badge_width = (
            metrics.horizontalAdvance(badge_text) + 2 * MORE_BADGE_PAD_X
        )
        badge_left = limit - badge_width
        while (
            chips
            and chips[-1][0].right() + 1 + TAG_CHIP_GAP > badge_left
        ):
            chips.pop()
            hidden_count += 1
            badge_text = f"+{hidden_count}"
            badge_width = (
                metrics.horizontalAdvance(badge_text) + 2 * MORE_BADGE_PAD_X
            )
            badge_left = limit - badge_width

    return TagChipLayout(chips=chips, hidden_count=hidden_count)


class StickerItemDelegate(QStyledItemDelegate):
    """Draw a centered thumbnail without reserving space for item text."""

    PADDING = 8
    ITEM_SIZE = 160
    BADGE_MARGIN = 1
    BADGE_PADDING_X = 4
    BADGE_PADDING_Y = 2
    BADGE_FONT_POINT_SIZE = 7
    BADGE_CORNER_RADIUS = 3
    SIMILARITY_BADGE_BACKGROUND = QColor("#FFD400")
    SIMILARITY_BADGE_FOREGROUND = QColor("#000000")
    GIF_BADGE_BACKGROUND = QColor(255, 102, 154)
    GIF_BADGE_FOREGROUND = QColor("#FFFFFF")
    GIF_EXTENSION = ".gif"
    DETAIL_TEXT_GAP = 12
    DETAIL_TAG_GAP = 16
    DETAIL_FILENAME_RATIO = 0.35

    def __init__(
        self,
        parent: QWidget | None = None,
        thumbnail_provider: ThumbnailProvider | None = None,
    ):
        super().__init__(parent)
        self._item_size = self.ITEM_SIZE
        self._display_mode = commons.constants.LIST_DISPLAY_MODE_ICON
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

    def set_display_mode(self, mode: int) -> None:
        """切换绘制形态：图标网格（LIST_DISPLAY_MODE_ICON）或详细信息行。"""
        self._display_mode = mode

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

            if self._display_mode == commons.constants.LIST_DISPLAY_MODE_LIST:
                self._paint_detail(painter, option, index)
            else:
                self._paint_icon(painter, option, index)
        finally:
            painter.restore()

    def _paint_icon(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
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

        # 对于 GIF 图片，在左上角画一个 GIF 角标
        blob_entity = index.data(ROLE_BLOB_ENTITY)
        if (
            blob_entity is not None
            and blob_entity.extension.casefold() == self.GIF_EXTENSION
        ):
            self._draw_badge(
                painter,
                pixmap_rect,
                "GIF",
                self.GIF_BADGE_BACKGROUND,
                self.GIF_BADGE_FOREGROUND,
                align_left=True,
            )

        # 对于有相似度数据的图，在右上角画一个相似度的角标
        similarity = index.data(ROLE_SIMILARITY)
        if similarity is not None:
            self._draw_similarity_badge(
                painter,
                pixmap_rect,
                float(similarity),
            )

    def _paint_detail(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """详细信息模式：左侧小缩略图 + 文件名 + 标签圆角片。"""
        rect = option.rect
        thumb_side = max(1, rect.height() - 2 * self.PADDING)
        thumb_rect = QRect(
            rect.left() + self.PADDING,
            rect.top() + self.PADDING,
            thumb_side,
            thumb_side,
        )
        mode = (
            QIcon.Mode.Normal
            if option.state & QStyle.StateFlag.State_Enabled
            else QIcon.Mode.Disabled
        )
        pixmap = self._pixmap_for_index(index, thumb_rect.size(), mode)
        if not pixmap.isNull():
            pixmap = pixmap.scaled(
                thumb_rect.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pixmap_rect = pixmap.rect()
            pixmap_rect.moveCenter(thumb_rect.center())
            painter.drawPixmap(pixmap_rect, pixmap)

            # 角标与图标模式共用，只是基准换成小缩略图矩形。
            blob_entity = index.data(ROLE_BLOB_ENTITY)
            if (
                blob_entity is not None
                and blob_entity.extension.casefold() == self.GIF_EXTENSION
            ):
                self._draw_badge(
                    painter,
                    pixmap_rect,
                    "GIF",
                    self.GIF_BADGE_BACKGROUND,
                    self.GIF_BADGE_FOREGROUND,
                    align_left=True,
                )
            similarity = index.data(ROLE_SIMILARITY)
            if similarity is not None:
                self._draw_similarity_badge(
                    painter,
                    pixmap_rect,
                    float(similarity),
                )

        text_left = thumb_rect.right() + 1 + self.DETAIL_TEXT_GAP
        text_right = rect.right() - self.PADDING
        if text_right < text_left:
            return
        text_rect = QRect(
            text_left,
            rect.top(),
            text_right - text_left + 1,
            rect.height(),
        )
        metrics = QFontMetrics(option.font)

        sticker = index.data(ROLE_STICKER_IMAGE)
        if sticker is not None:
            filename = sticker.original_file_name or ""
        else:
            # debug 服务等构造的模型没有 ROLE_STICKER_IMAGE，退回 DisplayRole。
            filename = index.data(Qt.ItemDataRole.DisplayRole) or ""

        name_limit = max(0, int(text_rect.width() * self.DETAIL_FILENAME_RATIO))
        elided_name = ""
        if name_limit > 0:
            elided_name = metrics.elidedText(
                filename,
                Qt.TextElideMode.ElideRight,
                name_limit,
            )
        name_rect = QRect(
            text_rect.left(),
            text_rect.top(),
            name_limit,
            text_rect.height(),
        )
        # 显式回到 option.font，避免缩略图角标等前置绘制污染画笔字体。
        painter.setFont(option.font)
        painter.setPen(option.palette.text().color())
        painter.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided_name,
        )

        tags_left = name_rect.right() + 1 + self.DETAIL_TAG_GAP
        if tags_left > text_rect.right():
            return
        tags_rect = QRect(
            tags_left,
            text_rect.top(),
            text_rect.right() - tags_left + 1,
            text_rect.height(),
        )
        tags = sticker.tags if sticker is not None else []
        if not tags:
            return
        layout = layout_tag_chips(tags_rect, tags, metrics)
        self._draw_tag_chips(painter, option, tags_rect, layout, tags)

    def _draw_tag_chips(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        clip_rect: QRect,
        layout: TagChipLayout,
        tags: list,
    ) -> None:
        """按布局绘制彩色标签圆角片；样式对齐 TagItemDelegate。

        不设裁剪：layout_tag_chips 保证圆角片不越界，而裁剪区左边界
        与首片左边框重合，在高 DPI 缩放下会把整列边框裁掉。
        """
        # 抗锯齿让描边以路径为中心对称渲染；高 DPI（如 125%）下
        # 不开 AA 时四个角的圆弧各自取整会不对称。
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for position, (chip_rect, label) in enumerate(layout.chips):
            accent_value = getattr(tags[position], "color_rgb", "")
            accent_color = QColor(accent_value) if accent_value else QColor()
            has_accent = accent_color.isValid()
            border = accent_color if has_accent else TAG_CHIP_BORDER
            if has_accent:
                background = QColor(border)
                background.setAlpha(TAG_CHIP_ACCENT_ALPHA)
                text_color = option.palette.text().color()
            else:
                background = TAG_CHIP_BACKGROUND
                text_color = TAG_CHIP_TEXT

            painter.setBrush(background)
            painter.setPen(QPen(border, 1))
            painter.drawRoundedRect(
                chip_rect,
                TAG_CHIP_CORNER_RADIUS,
                TAG_CHIP_CORNER_RADIUS,
            )
            painter.setPen(text_color)
            painter.drawText(
                chip_rect,
                Qt.AlignmentFlag.AlignCenter,
                label,
            )

        if layout.hidden_count > 0:
            self._draw_badge(
                painter,
                clip_rect,
                f"+{layout.hidden_count}",
                MORE_BADGE_BACKGROUND,
                MORE_BADGE_FOREGROUND,
                vertical_center=True,
            )

    def _draw_similarity_badge(
        self,
        painter: QPainter,
        thumbnail_rect: QRect,
        similarity: float,
    ) -> None:
        """在缩略图右上角绘制固定大小的相似度角标。"""
        self._draw_badge(
            painter,
            thumbnail_rect,
            f"{similarity:.1%}",
            self.SIMILARITY_BADGE_BACKGROUND,
            self.SIMILARITY_BADGE_FOREGROUND,
        )

    def _draw_badge(
        self,
        painter: QPainter,
        thumbnail_rect: QRect,
        text: str,
        background: QColor,
        foreground: QColor,
        *,
        align_left: bool = False,
        vertical_center: bool = False,
    ) -> None:
        """在缩略图角落绘制固定大小的角标，与相似度角标同一样式。

        save/restore 保证不污染调用方后续绘制的画笔状态（字体等）。
        """
        painter.save()
        try:
            # 高 DPI（如 125%）下不开抗锯齿时，圆角弧线按设备像素取整
            # 左右舍入方向不一致，会导致四个角形状不对称。
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            font = painter.font()
            font.setPointSize(self.BADGE_FONT_POINT_SIZE)
            font.setBold(True)
            painter.setFont(font)

            metrics = QFontMetrics(font)
            badge_width = (
                metrics.horizontalAdvance(text) + 2 * self.BADGE_PADDING_X
            )
            badge_height = (
                metrics.height() + 2 * self.BADGE_PADDING_Y
            )
            if align_left:
                badge_left = thumbnail_rect.left() + self.BADGE_MARGIN
            else:
                badge_left = (
                    thumbnail_rect.right()
                    - badge_width
                    - self.BADGE_MARGIN
                )
            if vertical_center:
                badge_top = (
                    thumbnail_rect.center().y() - badge_height // 2
                )
            else:
                badge_top = thumbnail_rect.top() + self.BADGE_MARGIN
            badge_rect = QRect(
                badge_left,
                badge_top,
                badge_width,
                badge_height,
            )

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(
                badge_rect,
                self.BADGE_CORNER_RADIUS,
                self.BADGE_CORNER_RADIUS,
            )
            painter.setPen(foreground)
            painter.drawText(
                badge_rect,
                Qt.AlignmentFlag.AlignCenter,
                text,
            )
        finally:
            painter.restore()

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QSize:
        if self._display_mode == commons.constants.LIST_DISPLAY_MODE_LIST:
            # 宽度仅作兜底；实际 cell 尺寸由 gridSize 决定。
            return QSize(self._item_size * 4, self._item_size)
        return QSize(self._item_size, self._item_size)


class StickerListView(QListView):
    """
    通用的表情包列表视图
    """

    # 即将滚动到列表底部时发出，由无限集合标签页负责响应。
    load_more_requested = pyqtSignal()
    # Ctrl+滚轮调整尺寸后通知工具栏滑块同步。
    display_size_changed = pyqtSignal(int)

    THUMBNAIL_SIZE = commons.constants.THUMBNAIL_SIZE
    ITEM_SIZE = StickerItemDelegate.ITEM_SIZE
    LOAD_MORE_THRESHOLD = 64
    DETAIL_ROW_HEIGHT_DEFAULT = 72
    DETAIL_ROW_HEIGHT_MIN = 48
    DETAIL_ROW_HEIGHT_MAX = 128
    DISPLAY_SIZE_MIN = 48
    ICON_DISPLAY_SIZE_MAX = 256
    DEFAULT_ICON_SIZE = 120
    DEFAULT_EMPTY_TEXT = "列表空空如也"

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

        self._display_mode = commons.constants.LIST_DISPLAY_MODE_ICON
        self.sort_mode = commons.constants.SORT_BY_DATE
        self.reverse_sort = False
        self._thumbnail_provider: ThumbnailProvider | None = None
        # 两种模式的尺寸互相独立设置（类似 Windows 资源管理器）。
        settings_manager = services.global_instances.current_settings_manager
        default_icon_size = self.DEFAULT_ICON_SIZE
        if settings_manager is not None:
            default_icon_size = int(
                settings_manager.get("default_icon_size")
            )
        self._icon_item_size = max(
            self.DISPLAY_SIZE_MIN,
            min(default_icon_size, self.ICON_DISPLAY_SIZE_MAX),
        )
        self._detail_row_height = self.DETAIL_ROW_HEIGHT_DEFAULT
        # 空态占位文案；_empty_state_active 记录上次绘制的空态，用于检测翻转。
        self._empty_text = self.DEFAULT_EMPTY_TEXT
        self._empty_state_active = True

        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.setIconSize(QSize(self.THUMBNAIL_SIZE, self.THUMBNAIL_SIZE))
        self.setGridSize(
            QSize(self._icon_item_size, self._icon_item_size)
        )
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
        self.verticalScrollBar().rangeChanged.connect(
            self._on_vertical_scrollbar_range_changed
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
            self._disconnect_model_signals(previous_model)

        super().setModel(model)
        if model is not None:
            self._connect_model_signals(model)
            self._load_more_timer.start()
        self._update_empty_state()

    def _connect_model_signals(self, model) -> None:
        model.rowsInserted.connect(self._on_model_rows_inserted)
        model.rowsRemoved.connect(self._on_model_rows_removed)
        model.modelReset.connect(self._on_model_reset)
        model.layoutChanged.connect(self._on_model_layout_changed)

    def _disconnect_model_signals(self, model) -> None:
        for signal_name, slot in (
            ("rowsInserted", self._on_model_rows_inserted),
            ("rowsRemoved", self._on_model_rows_removed),
            ("modelReset", self._on_model_reset),
            ("layoutChanged", self._on_model_layout_changed),
        ):
            signal = getattr(model, signal_name)
            try:
                signal.disconnect(slot)
            except TypeError:
                pass

    def _on_model_rows_inserted(self, _parent, _first, _last) -> None:
        self._schedule_load_more_check()
        self._update_empty_state()

    def _on_model_rows_removed(self, _parent, _first, _last) -> None:
        self._schedule_load_more_check()
        self._update_empty_state()

    def _on_model_reset(self) -> None:
        self._schedule_load_more_check()
        self._update_empty_state()

    def _on_model_layout_changed(self, *_args) -> None:
        self._schedule_load_more_check()

    def _on_vertical_scrollbar_changed(self, _value: int) -> None:
        self._schedule_load_more_check()

    def _on_vertical_scrollbar_range_changed(
        self, _minimum: int, _maximum: int
    ) -> None:
        self._schedule_load_more_check()

    def _schedule_load_more_check(self) -> None:
        """在布局稳定后的事件循环中合并执行一次加载检查。"""
        self._load_more_timer.start()

    def check_load_more(self) -> None:
        """在即将滚动到底部（或内容不足一屏）时发出加载更多请求。"""
        model = self.model()
        if model is None or model.rowCount() <= 0:
            return

        if not self._is_near_bottom(model):
            return
        self.load_more_requested.emit()

    def _is_near_bottom(self, model) -> bool:
        """根据最后一项与视口底部的距离判断是否需要加载。"""
        viewport_rect = self.viewport().rect()
        if viewport_rect.isEmpty():
            return False

        last_index = model.index(model.rowCount() - 1, 0)
        last_rect = self.visualRect(last_index)
        if not last_rect.isValid():
            return False

        return (
            last_rect.bottom()
            <= viewport_rect.bottom() + self.LOAD_MORE_THRESHOLD
        )

    def item_size(self) -> int:
        """返回当前模式的显示尺寸（图标模式为格子边长，详细信息模式为行高）。"""
        if self._display_mode == commons.constants.LIST_DISPLAY_MODE_LIST:
            return self._detail_row_height
        return self._icon_item_size

    def set_display_mode(self, mode: int) -> None:
        """在图标模式和详细信息模式之间切换，两种模式的尺寸各自记忆。"""
        self._display_mode = mode
        delegate = self.itemDelegate()
        if isinstance(delegate, StickerItemDelegate):
            delegate.set_display_mode(mode)

        if mode == commons.constants.LIST_DISPLAY_MODE_LIST:
            self.setViewMode(QListView.ViewMode.ListMode)
            self._apply_detail_row_height()
        else:
            self.setViewMode(QListView.ViewMode.IconMode)
            if isinstance(delegate, StickerItemDelegate):
                delegate.set_item_size(self._icon_item_size)
            self._apply_icon_grid_size()
        # 立即重排，不等下一次视口事件。
        self.doItemsLayout()
        if mode != commons.constants.LIST_DISPLAY_MODE_LIST:
            # 列表布局路径会 setSingleStep(1)，令滚动条进入"应用接管"状态，
            # 此后 Qt 不再随布局应用图标模式的步长（残留为 1，滚轮每格仅几像素）。
            # setSingleStep(-1) 归还控制权并立即应用布局刚记下的首选步长
            # （委托 sizeHint 高 + spacing），与程序启动时的行为一致。
            self.verticalScrollBar().setSingleStep(-1)
        self._schedule_load_more_check()

    def set_display_size(self, size: int) -> None:
        """调整当前模式的显示大小（类似 Windows 7 资源管理器的滑块）。"""
        size = int(size)
        delegate = self.itemDelegate()
        if self._display_mode == commons.constants.LIST_DISPLAY_MODE_LIST:
            self._detail_row_height = max(
                self.DETAIL_ROW_HEIGHT_MIN,
                min(size, self.DETAIL_ROW_HEIGHT_MAX),
            )
            if isinstance(delegate, StickerItemDelegate):
                delegate.set_item_size(self._detail_row_height)
            self._sync_detail_grid_width()
        else:
            self._icon_item_size = max(
                self.DISPLAY_SIZE_MIN,
                min(size, self.ICON_DISPLAY_SIZE_MAX),
            )
            if isinstance(delegate, StickerItemDelegate):
                delegate.set_item_size(self._icon_item_size)
            self._apply_icon_grid_size()
        self._schedule_load_more_check()

    def wheelEvent(self, event) -> None:
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y()
        if delta:
            size_step = round(delta / 120 * 8)
            if size_step:
                previous_size = self.item_size()
                self.set_display_size(previous_size + size_step)
                current_size = self.item_size()
                if current_size != previous_size:
                    self.display_size_changed.emit(current_size)
        event.accept()

    def _apply_icon_grid_size(self) -> None:
        self.setGridSize(
            QSize(self._icon_item_size, self._icon_item_size)
        )

    def _apply_detail_row_height(self) -> None:
        delegate = self.itemDelegate()
        if isinstance(delegate, StickerItemDelegate):
            delegate.set_item_size(self._detail_row_height)
        self._sync_detail_grid_width()

    def _sync_detail_grid_width(self) -> None:
        """详细信息模式下格子占满视口宽度，高度为当前行高。"""
        width = max(1, self.viewport().width())
        self.setGridSize(QSize(width, self._detail_row_height))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._display_mode == commons.constants.LIST_DISPLAY_MODE_LIST:
            self._sync_detail_grid_width()
        self._schedule_load_more_check()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._schedule_load_more_check()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._is_empty():
            return
        painter = QPainter(self.viewport())
        painter.setPen(
            self.palette().color(QPalette.ColorRole.PlaceholderText)
        )
        painter.drawText(
            self.viewport().rect(),
            Qt.AlignmentFlag.AlignCenter,
            self._empty_text,
        )

    def _is_empty(self) -> bool:
        model = self.model()
        return model is None or model.rowCount() == 0

    def _update_empty_state(self) -> None:
        # 空↔非空翻转时 Qt 只局部重绘变化区域，需全量重绘避免残留旧文案；
        # 未翻转时不触发重绘，批量导入不受影响。
        empty = self._is_empty()
        if empty != self._empty_state_active:
            self._empty_state_active = empty
            self.viewport().update()

    def set_empty_text(self, text: str) -> None:
        """设置列表为空时显示的占位文案。"""
        self._empty_text = text
        if self._empty_state_active:
            self.viewport().update()

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
        delegate.set_item_size(self.item_size())
        delegate.set_display_mode(self._display_mode)
        self.setItemDelegate(delegate)
        if thumbnail_provider is not None:
            thumbnail_provider.thumbnail_ready.connect(self._on_thumbnail_ready)

    def _on_thumbnail_ready(self, file_hash, _image) -> None:
        """缩略图就绪后向模型查询 hash 对应行并重绘匹配的可见 item。"""
        row_for_hash = getattr(self.model(), "row_for_hash", None)
        if row_for_hash is None:
            # plain model（debug 服务等）：无法路由，跳过。
            return
        row = row_for_hash(file_hash)
        if row is None:
            return

        start_row, end_row = self._visible_row_range()
        if start_row <= row <= end_row:
            self._update_item(self.model().index(row, 0))

    def _visible_row_range(self) -> tuple[int, int]:
        """返回当前视口覆盖的行区间 [start, end]；视口未布局时返回全表区间。"""
        model = self.model()
        if model is None or model.rowCount() <= 0:
            return 0, -1
        row_count = model.rowCount()
        start_row = 0
        end_row = row_count - 1
        first_index = self.indexAt(self.viewport().rect().topLeft())
        last_index = self.indexAt(self.viewport().rect().bottomRight())
        if first_index.isValid():
            start_row = first_index.row()
        if last_index.isValid():
            end_row = min(end_row, last_index.row())
        return start_row, end_row

    def _update_item(self, index: QModelIndex) -> None:
        """只重绘指定 item 占据的区域（QAbstractItemView::update(index)）。"""
        self.update(index)
