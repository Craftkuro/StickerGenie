from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import apppath

from PyQt6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QRect,
    QSignalBlocker,
    QSize,
    QTimer,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPalette,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCompleter,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QWidget,
)

logger = logging.getLogger(__name__)


def _resolve_resource_path(filename: str) -> Path:
    if apppath.app_path is not None:
        return apppath.app_path / "resources" / filename
    return Path(__file__).resolve().parents[2] / "resources" / filename


TITLE_ROLE = Qt.ItemDataRole.UserRole + 1
SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 2
SEARCH_TEXT_ROLE = Qt.ItemDataRole.UserRole + 3


@dataclass(frozen=True, slots=True)
class SearchSuggestion:
    """One completion row and the text submitted when it is selected."""

    title: str
    subtitle: str = ""
    search_text: str | None = None

    @property
    def submitted_text(self) -> str:
        return self.search_text if self.search_text is not None else self.title


class StaticSearchSuggestionsProvider(QObject):
    """Temporary asynchronous provider used until search services are available."""

    suggestions_ready = pyqtSignal(int, str, object)

    DEFAULT_RECENT_SEARCHES = ("萝卜🥕", "纸巾🧻", "真棒👍")
    DEFAULT_TAGS = (
        "北京",
        "上海",
        "广州",
        "深圳",
        "成都",
        "杭州",
        "南京",
        "武汉",
        "西安",
        "重庆",
    )

    def __init__(
        self,
        recent_searches: list[str],
        parent: QObject | None = None,
        *,
        response_delay_ms: int = 0,
    ):
        super().__init__(parent)
        self._recent_searches = recent_searches
        self._response_delay_ms = max(0, response_delay_ms)

    @pyqtSlot(int, str)
    def request_suggestions(self, request_id: int, query: str) -> None:
        suggestions = self._build_suggestions(query)
        QTimer.singleShot(
            self._response_delay_ms,
            lambda: self.suggestions_ready.emit(request_id, query, suggestions),
        )

    def _build_suggestions(self, query: str) -> tuple[SearchSuggestion, ...]:
        suggestions = [
            SearchSuggestion(term, "最近搜索")
            for term in self._recent_searches
        ]
        if query:
            known_titles = {suggestion.title for suggestion in suggestions}
            suggestions.extend(
                SearchSuggestion(tag, "预设标签")
                for tag in self.DEFAULT_TAGS
                if query in tag and tag not in known_titles
            )
        return tuple(suggestions)


class RichCompleter(QCompleter):
    """QCompleter that keeps its delegate and recalculates popup width."""

    def __init__(self, width_source: QWidget, parent: QObject | None = None):
        super().__init__(parent)
        self._width_source = width_source
        self._custom_item_delegate: QStyledItemDelegate | None = None
        self.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self._configure_popup()

    def _configure_popup(self) -> None:
        popup = self.popup()
        if popup is None:
            return

        popup.setMouseTracking(True)
        popup.viewport().setMouseTracking(True)
        popup.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        popup.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        if self._custom_item_delegate is not None:
            popup.setItemDelegate(self._custom_item_delegate)

    def set_item_delegate(self, delegate: QStyledItemDelegate) -> None:
        self._custom_item_delegate = delegate
        self._configure_popup()

    def setModel(self, model) -> None:
        super().setModel(model)
        self._configure_popup()

    def complete(self, rect: QRect = QRect()) -> None:
        self._configure_popup()
        popup = self.popup()
        widget = self.widget()
        if popup is None or widget is None:
            super().complete(rect)
            return

        popup.doItemsLayout()
        completion_rect = QRect(rect) if rect.isValid() else widget.rect()
        content_width = (
            popup.sizeHintForColumn(0)
            + popup.verticalScrollBar().sizeHint().width()
            + (2 * popup.frameWidth())
        )
        screen = self._width_source.screen()
        max_width = screen.availableGeometry().width() if screen else content_width
        completion_rect.setWidth(
            min(max(self._width_source.width(), content_width), max_width)
        )
        super().complete(completion_rect)

        visible_rows = min(self.maxVisibleItems(), popup.model().rowCount())
        rows_height = sum(
            max(0, popup.sizeHintForRow(row)) for row in range(visible_rows)
        )
        popup_height = rows_height + (2 * popup.frameWidth())
        if screen is not None:
            popup_height = min(popup_height, screen.availableGeometry().height())
        if popup_height > 0:
            popup.resize(completion_rect.width(), popup_height)


class SearchToolButton(QToolButton):
    """Icon button that keeps its style-derived height and horizontal padding."""

    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        return QSize(max(size.width(), size.height()), size.height())

    def minimumSizeHint(self) -> QSize:
        size = super().minimumSizeHint()
        return QSize(max(size.width(), size.height()), size.height())


class RichSearchCompleterItemDelegate(QStyledItemDelegate):
    """Render completion rows using the current application font and metrics."""

    HORIZONTAL_PADDING = 8
    VERTICAL_PADDING = 5
    LINE_SPACING = 2

    @staticmethod
    def title_font(option: QStyleOptionViewItem) -> QFont:
        font = QFont(option.font)
        font.setBold(True)
        return font

    @staticmethod
    def subtitle_font(option: QStyleOptionViewItem) -> QFont:
        font = QFont(option.font)
        font.setItalic(True)
        return font

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        title = str(index.data(TITLE_ROLE) or "")
        subtitle = str(index.data(SUBTITLE_ROLE) or "")
        if not title:
            super().paint(painter, option, index)
            return

        painter.save()
        try:
            panel_option = QStyleOptionViewItem(option)
            panel_option.text = ""
            style = option.widget.style() if option.widget else QApplication.style()
            style.drawPrimitive(
                QStyle.PrimitiveElement.PE_PanelItemViewItem,
                panel_option,
                painter,
                option.widget,
            )

            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            title_color = option.palette.color(
                QPalette.ColorRole.HighlightedText
                if selected
                else QPalette.ColorRole.Text
            )
            subtitle_color = option.palette.color(
                QPalette.ColorRole.HighlightedText
                if selected
                else QPalette.ColorRole.PlaceholderText
            )
            content_rect = option.rect.adjusted(
                self.HORIZONTAL_PADDING,
                self.VERTICAL_PADDING,
                -self.HORIZONTAL_PADDING,
                -self.VERTICAL_PADDING,
            )

            title_font = self.title_font(option)
            title_metrics = QFontMetrics(title_font)
            if subtitle:
                subtitle_font = self.subtitle_font(option)
                subtitle_metrics = QFontMetrics(subtitle_font)
                title_rect = QRect(
                    content_rect.x(),
                    content_rect.y(),
                    content_rect.width(),
                    title_metrics.height(),
                )
                subtitle_rect = QRect(
                    content_rect.x(),
                    title_rect.bottom() + 1 + self.LINE_SPACING,
                    content_rect.width(),
                    subtitle_metrics.height(),
                )
            else:
                subtitle_font = None
                subtitle_metrics = None
                title_rect = content_rect
                subtitle_rect = QRect()

            painter.setFont(title_font)
            painter.setPen(title_color)
            painter.drawText(
                title_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                title_metrics.elidedText(
                    title,
                    Qt.TextElideMode.ElideRight,
                    title_rect.width(),
                ),
            )

            if subtitle and subtitle_font is not None and subtitle_metrics is not None:
                painter.setFont(subtitle_font)
                painter.setPen(subtitle_color)
                painter.drawText(
                    subtitle_rect,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    subtitle_metrics.elidedText(
                        subtitle,
                        Qt.TextElideMode.ElideRight,
                        subtitle_rect.width(),
                    ),
                )
        finally:
            painter.restore()

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QSize:
        title = str(index.data(TITLE_ROLE) or "")
        subtitle = str(index.data(SUBTITLE_ROLE) or "")
        title_metrics = QFontMetrics(self.title_font(option))
        subtitle_metrics = QFontMetrics(self.subtitle_font(option))

        content_height = title_metrics.height()
        content_width = title_metrics.horizontalAdvance(title)
        if subtitle:
            content_height += self.LINE_SPACING + subtitle_metrics.height()
            content_width = max(
                content_width,
                subtitle_metrics.horizontalAdvance(subtitle),
            )

        return QSize(
            content_width + (2 * self.HORIZONTAL_PADDING),
            content_height + (2 * self.VERTICAL_PADDING),
        )


class CustomSearchBox(QWidget):
    """Expanding search input with asynchronous rich suggestions."""

    DEBOUNCE_INTERVAL_MS = 300
    LAYOUT_SPACING = 4
    MAX_RECENT_SEARCHES = 10

    searched = pyqtSignal(str)
    suggestions_requested = pyqtSignal(int, str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        suggestions_provider: QObject | None = None,
        debounce_interval_ms: int = DEBOUNCE_INTERVAL_MS,
    ):
        super().__init__(parent)
        self.setObjectName("customSearchBox")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        self.recent_searches = list(
            StaticSearchSuggestionsProvider.DEFAULT_RECENT_SEARCHES
        )
        self._query_sequence = 0
        self._latest_query_id = 0
        self._latest_query_text = ""
        self._last_applied_query_id = 0
        self._completion_selection_explicit = False
        self._completion_submission_in_progress = False
        self._completion_navigation_in_progress = False
        self._submit_first_suggestion_when_unselected = False
        self._suggestions_provider: QObject | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self.LAYOUT_SPACING)

        self.line_edit = QLineEdit(self)
        self.line_edit.setObjectName("searchLineEdit")
        self.line_edit.setPlaceholderText("搜索...")
        self.line_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        self.search_button = SearchToolButton(self)
        self.search_button.setObjectName("searchButton")
        self.search_button.setToolTip("搜索")
        self.search_button.setAccessibleName("搜索")
        self.search_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_button.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        search_icon = QIcon(str(_resolve_resource_path("search.svg")))
        if search_icon.isNull():
            self.search_button.setText("🔍")
        else:
            self.search_button.setIcon(search_icon)

        # QLineEdit/QToolButton are naturally shorter than sibling QPushButtons,
        # so use a same-style push button as the height reference for the bar.
        reference_button = QPushButton()
        reference_button.setIcon(search_icon)
        reference_button.setIconSize(self.search_button.iconSize())
        #reference_button.setStyleSheet("QPushButton { padding: 3px; }")
        sibling_button_height = reference_button.sizeHint().height()
        self.line_edit.setMinimumHeight(sibling_button_height)
        self.search_button.setMinimumHeight(sibling_button_height)

        layout.addWidget(self.line_edit, 1)
        layout.addWidget(self.search_button, 0)

        self.completer_model = QStandardItemModel(self)
        self.completer = RichCompleter(self, self)
        self.completer.setModel(self.completer_model)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setWrapAround(False)

        self.completer_delegate = RichSearchCompleterItemDelegate(
            self.completer.popup()
        )
        self.completer.set_item_delegate(self.completer_delegate)
        self.line_edit.setCompleter(self.completer)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(max(0, debounce_interval_ms))
        self._debounce_timer.timeout.connect(self._emit_suggestions_request)

        self._install_completion_event_filters()
        self.search_button.clicked.connect(self._trigger_search)
        self.line_edit.returnPressed.connect(self._on_return_pressed)
        self.line_edit.textEdited.connect(self._on_search_text_edited)
        self.completer.highlighted[QModelIndex].connect(
            self._on_completion_highlighted
        )
        self.completer.popup().clicked.connect(self._on_popup_clicked)

        if suggestions_provider is None:
            suggestions_provider = StaticSearchSuggestionsProvider(
                self.recent_searches,
                self,
            )
        self.set_suggestions_provider(suggestions_provider)

    def _install_completion_event_filters(self) -> None:
        self.line_edit.installEventFilter(self)
        popup = self.completer.popup()
        popup.installEventFilter(self)
        popup.viewport().installEventFilter(self)

    def set_suggestions_provider(self, provider: QObject | None) -> None:
        """Connect a QObject implementing the suggestion request/result contract."""
        if self._suggestions_provider is not None:
            try:
                self.suggestions_requested.disconnect(
                    self._suggestions_provider.request_suggestions
                )
                self._suggestions_provider.suggestions_ready.disconnect(
                    self.apply_suggestions
                )
            except (AttributeError, TypeError, RuntimeError):
                pass

        self._suggestions_provider = provider
        if provider is None:
            return
        if not hasattr(provider, "request_suggestions") or not hasattr(
            provider, "suggestions_ready"
        ):
            raise TypeError(
                "suggestions_provider must expose request_suggestions and "
                "suggestions_ready"
            )

        self.suggestions_requested.connect(provider.request_suggestions)
        provider.suggestions_ready.connect(self.apply_suggestions)

    def set_submit_first_suggestion_when_unselected(self, enabled: bool) -> None:
        """Set whether Enter commits the first visible suggestion by default."""
        self._submit_first_suggestion_when_unselected = bool(enabled)

    def refresh_suggestions(self) -> None:
        """立即按当前文本重新请求候选。"""
        self.completer.popup().hide()
        self._reset_completion_selection()
        self._schedule_suggestions(self.line_edit.text(), 0)

    @pyqtSlot(str)
    def _on_search_text_edited(self, text: str) -> None:
        if self._completion_navigation_in_progress:
            return
        self._schedule_suggestions(text, self._debounce_timer.interval())
        self._reset_completion_selection()

    @pyqtSlot(QModelIndex)
    def _on_completion_highlighted(self, _index: QModelIndex) -> None:
        if not self._completion_submission_in_progress:
            self._restore_query_text()

    def _schedule_suggestions(self, text: str, delay_ms: int) -> None:
        self._query_sequence += 1
        self._latest_query_id = self._query_sequence
        self._latest_query_text = text
        self._debounce_timer.start(max(0, delay_ms))

    @pyqtSlot()
    def _emit_suggestions_request(self) -> None:
        self.suggestions_requested.emit(
            self._latest_query_id,
            self._latest_query_text,
        )

    @pyqtSlot(int, str, object)
    def apply_suggestions(
        self,
        request_id: int,
        query: str,
        suggestions: object,
    ) -> None:
        if (
            request_id != self._latest_query_id
            or request_id <= self._last_applied_query_id
            or query != self._latest_query_text
            or query != self.line_edit.text()
        ):
            logger.debug("忽略过期的搜索建议结果：%s", request_id)
            return

        try:
            normalized = self._normalize_suggestions(suggestions)
        except (TypeError, ValueError):
            logger.exception("搜索建议返回了无效数据")
            return

        self._last_applied_query_id = request_id
        self._reset_completion_selection()
        self._replace_suggestions(normalized)
        if normalized and self.line_edit.hasFocus():
            self.completer.setCompletionPrefix(query)
            self.completer.complete()
            self._set_popup_current_index(
                QModelIndex(),
                preserve_query_text=True,
            )
        else:
            self.completer.popup().hide()

    @staticmethod
    def _normalize_suggestions(
        suggestions: object,
    ) -> tuple[SearchSuggestion, ...]:
        if isinstance(suggestions, (str, bytes)) or not isinstance(
            suggestions, Sequence
        ):
            raise TypeError("suggestions must be a sequence")
        normalized = tuple(suggestions)
        if not all(isinstance(item, SearchSuggestion) for item in normalized):
            raise TypeError("every suggestion must be a SearchSuggestion")
        return normalized

    def _replace_suggestions(
        self,
        suggestions: Sequence[SearchSuggestion],
    ) -> None:
        self.completer_model.clear()
        for suggestion in suggestions:
            item = QStandardItem(suggestion.title)
            item.setEditable(False)
            item.setData(suggestion.title, TITLE_ROLE)
            item.setData(suggestion.subtitle, SUBTITLE_ROLE)
            item.setData(suggestion.submitted_text, SEARCH_TEXT_ROLE)
            self.completer_model.appendRow(item)

    def _completion_text_from_index(self, index: QModelIndex) -> str:
        if not index.isValid():
            return ""
        value = index.data(SEARCH_TEXT_ROLE) or index.data(TITLE_ROLE)
        return str(value).strip() if value else ""

    @pyqtSlot(QModelIndex)
    def _on_popup_clicked(self, index: QModelIndex) -> None:
        text = self._completion_text_from_index(index)
        self._completion_submission_in_progress = True
        self.completer.popup().hide()
        self._completion_submission_in_progress = False
        self._reset_completion_selection()
        self._set_committed_text(text)
        self._search_for_text(text)

    @pyqtSlot()
    def _on_return_pressed(self) -> None:
        if not self.completer.popup().isVisible():
            self._trigger_search()

    @pyqtSlot()
    def _trigger_search(self) -> None:
        self.completer.popup().hide()
        self._reset_completion_selection()
        self._search_for_text(self.line_edit.text())

    def _search_for_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return

        self._invalidate_suggestion_requests()
        if text not in self.recent_searches:
            self.recent_searches.insert(0, text)
            del self.recent_searches[self.MAX_RECENT_SEARCHES :]

        self.searched.emit(text)
        logger.info("信号已发射：搜索 '%s'", text)

    def _invalidate_suggestion_requests(self) -> None:
        self._debounce_timer.stop()
        self._query_sequence += 1
        self._latest_query_id = self._query_sequence
        self._latest_query_text = self.line_edit.text()

    def _reset_completion_selection(self) -> None:
        self._completion_selection_explicit = False
        self._set_popup_current_index(QModelIndex())

    def _restore_query_text(self) -> None:
        if self.line_edit.text() == self._latest_query_text:
            return
        blocker = QSignalBlocker(self.line_edit)
        try:
            self.line_edit.setText(self._latest_query_text)
            self.line_edit.setCursorPosition(len(self._latest_query_text))
        finally:
            del blocker

    def _set_committed_text(self, text: str) -> None:
        self.line_edit.setText(text)
        self.line_edit.setCursorPosition(len(text))

    def _set_popup_current_index(
        self,
        index: QModelIndex,
        *,
        preserve_query_text: bool = False,
    ) -> None:
        popup = self.completer.popup()
        selection_model = popup.selectionModel()
        self._completion_navigation_in_progress = True
        try:
            if selection_model is None:
                popup.setCurrentIndex(index)
            else:
                blocker = QSignalBlocker(selection_model)
                try:
                    if not index.isValid():
                        selection_model.clearSelection()
                    popup.setCurrentIndex(index)
                finally:
                    del blocker
            if preserve_query_text:
                self._restore_query_text()
            popup.viewport().update()
        finally:
            self._completion_navigation_in_progress = False

    def _cancel_completion_selection(self) -> None:
        if self._completion_selection_explicit:
            self._restore_query_text()
        self._reset_completion_selection()

    def _submit_from_popup(self) -> None:
        popup = self.completer.popup()
        index = popup.currentIndex()
        if self._completion_selection_explicit and index.isValid():
            text = self._completion_text_from_index(index)
        elif (
            self._submit_first_suggestion_when_unselected
            and popup.model().rowCount() > 0
        ):
            index = popup.model().index(0, 0)
            text = self._completion_text_from_index(index)
        else:
            text = self.line_edit.text()
        self._completion_submission_in_progress = True
        popup.hide()
        self._completion_submission_in_progress = False
        self._reset_completion_selection()
        if index.isValid():
            self._set_committed_text(text)
        self._search_for_text(text)

    def _navigate_popup(self, key: Qt.Key) -> bool:
        popup = self.completer.popup()
        model = popup.model()
        row_count = model.rowCount()
        if row_count == 0:
            return False

        current_row = (
            popup.currentIndex().row()
            if self._completion_selection_explicit
            else -1
        )
        if key == Qt.Key.Key_Down:
            target_row = 0 if current_row < 0 else min(current_row + 1, row_count - 1)
        elif key == Qt.Key.Key_Up:
            target_row = row_count - 1 if current_row < 0 else max(current_row - 1, 0)
        elif key == Qt.Key.Key_Home:
            target_row = 0
        elif key == Qt.Key.Key_End:
            target_row = row_count - 1
        else:
            first_visible = popup.indexAt(QPoint(1, 1)).row()
            last_visible = popup.indexAt(
                QPoint(1, max(1, popup.viewport().height() - 2))
            ).row()
            page_size = max(1, last_visible - max(0, first_visible))
            if key == Qt.Key.Key_PageDown:
                start_row = max(0, current_row)
                target_row = min(start_row + page_size, row_count - 1)
            elif key == Qt.Key.Key_PageUp:
                start_row = row_count - 1 if current_row < 0 else current_row
                target_row = max(start_row - page_size, 0)
            else:
                return False

        index = model.index(target_row, 0)
        self._set_popup_current_index(index, preserve_query_text=True)
        popup.scrollTo(index)
        self._completion_selection_explicit = True
        return True

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        popup = self.completer.popup()
        popup_visible = popup.isVisible()
        event_type = event.type()

        if event_type in (QEvent.Type.FocusOut, QEvent.Type.WindowDeactivate):
            self._cancel_completion_selection()
        elif (
            obj is popup
            and event_type == QEvent.Type.Hide
            and not self._completion_submission_in_progress
        ):
            self._cancel_completion_selection()

        if popup_visible and event_type == QEvent.Type.KeyPress:
            key = event.key()
            navigation_keys = {
                Qt.Key.Key_Up,
                Qt.Key.Key_Down,
                Qt.Key.Key_PageUp,
                Qt.Key.Key_PageDown,
            }
            if obj is popup:
                navigation_keys.update((Qt.Key.Key_Home, Qt.Key.Key_End))

            if key in navigation_keys:
                if self._navigate_popup(key):
                    return True
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._submit_from_popup()
                return True
            elif key == Qt.Key.Key_Escape:
                self._cancel_completion_selection()
                popup.hide()
                return True

        if obj is self.line_edit and event_type == QEvent.Type.MouseButtonPress:
            self._reset_completion_selection()

        return super().eventFilter(obj, event)
