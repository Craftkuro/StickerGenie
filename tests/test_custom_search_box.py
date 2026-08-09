import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QModelIndex, QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QFont, QStandardItem
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QSizePolicy, QStyleOptionViewItem

from ui.widgets.custom_search_box import (
    SEARCH_TEXT_ROLE,
    SUBTITLE_ROLE,
    TITLE_ROLE,
    CustomSearchBox,
    RichSearchCompleterItemDelegate,
    SearchSuggestion,
)


class ControlledSuggestionsProvider(QObject):
    suggestions_ready = pyqtSignal(int, str, object)

    def __init__(self):
        super().__init__()
        self.requests = []

    @pyqtSlot(int, str)
    def request_suggestions(self, request_id: int, query: str) -> None:
        self.requests.append((request_id, query))


class SearchReceiver(QObject):
    def __init__(self):
        super().__init__()
        self.received = []

    @pyqtSlot(str)
    def receive(self, query: str) -> None:
        self.received.append(query)


class CustomSearchBoxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.provider = ControlledSuggestionsProvider()
        self.search_box = CustomSearchBox(
            suggestions_provider=self.provider,
            debounce_interval_ms=20,
        )

    def tearDown(self):
        self.search_box.completer.popup().hide()
        self.search_box.close()
        QApplication.processEvents()

    def _show_and_focus(self) -> None:
        self.search_box.resize(360, self.search_box.sizeHint().height())
        self.search_box.show()
        self.search_box.line_edit.setFocus()
        QApplication.processEvents()
        QTest.qWait(5)

    def _emit_pending_suggestions_request(self) -> None:
        self.assertTrue(self.search_box._debounce_timer.isActive())
        self.search_box._debounce_timer.stop()
        self.search_box._emit_suggestions_request()
        QApplication.processEvents()

    def _prepare_suggestions(self, query: str = "raw query") -> None:
        self._show_and_focus()
        self.provider.requests.clear()
        QTest.keyClicks(self.search_box.line_edit, query)
        self._emit_pending_suggestions_request()
        request_id, requested_query = self.provider.requests[-1]
        self.provider.suggestions_ready.emit(
            request_id,
            requested_query,
            (
                SearchSuggestion("First", "recent", "first-value"),
                SearchSuggestion("Second", "tag", "second-value"),
            ),
        )
        QApplication.processEvents()
        self.assertTrue(self.search_box.completer.popup().isVisible())
        self.assertFalse(
            self.search_box.completer.popup().currentIndex().isValid()
        )

    def test_layout_expands_input_and_keeps_button_fixed(self):
        self._show_and_focus()
        layout = self.search_box.layout()
        self.assertEqual(4, layout.spacing())
        self.assertEqual(
            QSizePolicy.Policy.Expanding,
            self.search_box.sizePolicy().horizontalPolicy(),
        )
        self.assertEqual(
            QSizePolicy.Policy.Expanding,
            self.search_box.line_edit.sizePolicy().horizontalPolicy(),
        )
        self.assertEqual(
            QSizePolicy.Policy.Fixed,
            self.search_box.search_button.sizePolicy().horizontalPolicy(),
        )

        initial_input_width = self.search_box.line_edit.width()
        initial_button_width = self.search_box.search_button.width()
        self.search_box.resize(640, self.search_box.height())
        QApplication.processEvents()

        self.assertGreater(self.search_box.line_edit.width(), initial_input_width)
        self.assertEqual(initial_button_width, self.search_box.search_button.width())
        self.assertEqual(
            self.search_box.search_button.width(),
            self.search_box.search_button.height(),
        )

    def test_default_debounce_interval_is_300_ms(self):
        search_box = CustomSearchBox(suggestions_provider=self.provider)
        try:
            self.assertEqual(300, search_box._debounce_timer.interval())
        finally:
            search_box.close()

    def test_debounce_collapses_a_typing_burst_into_one_request(self):
        self._show_and_focus()
        self.provider.requests.clear()
        self.search_box._debounce_timer.setInterval(80)

        QTest.keyClicks(self.search_box.line_edit, "abc")
        self.assertEqual([], self.provider.requests)
        self.assertEqual("abc", self.search_box._latest_query_text)
        self._emit_pending_suggestions_request()

        self.assertEqual(1, len(self.provider.requests))
        self.assertEqual("abc", self.provider.requests[0][1])

    def test_focus_alone_does_not_request_suggestions(self):
        self._show_and_focus()

        self.assertEqual([], self.provider.requests)
        self.assertFalse(self.search_box._debounce_timer.isActive())

    def test_result_is_rejected_as_soon_as_new_text_is_entered(self):
        self._show_and_focus()
        self.provider.requests.clear()

        QTest.keyClicks(self.search_box.line_edit, "a")
        self._emit_pending_suggestions_request()
        old_request_id, old_query = self.provider.requests[-1]

        QTest.keyClicks(self.search_box.line_edit, "b")
        self.provider.suggestions_ready.emit(
            old_request_id,
            old_query,
            (SearchSuggestion("stale"),),
        )
        QApplication.processEvents()
        self.assertEqual(0, self.search_box.completer_model.rowCount())

        self._emit_pending_suggestions_request()
        new_request_id, new_query = self.provider.requests[-1]
        self.provider.suggestions_ready.emit(
            new_request_id,
            new_query,
            (SearchSuggestion("fresh"),),
        )
        QApplication.processEvents()
        self.assertEqual("fresh", self.search_box.completer_model.item(0).text())

    def test_refresh_suggestions_requests_current_text_immediately(self):
        self._show_and_focus()
        self.provider.requests.clear()
        self.search_box.line_edit.setText("current query")

        self.search_box.refresh_suggestions()
        self._emit_pending_suggestions_request()

        self.assertEqual("current query", self.provider.requests[-1][1])

    def test_search_submission_cancels_pending_and_inflight_suggestions(self):
        self._show_and_focus()
        self.provider.requests.clear()
        spy = QSignalSpy(self.search_box.searched)

        QTest.keyClicks(self.search_box.line_edit, "query")
        self._emit_pending_suggestions_request()
        request_id, requested_query = self.provider.requests[-1]
        QTest.keyClick(self.search_box.line_edit, Qt.Key.Key_Return)
        self.provider.suggestions_ready.emit(
            request_id,
            requested_query,
            (SearchSuggestion("late result"),),
        )
        QApplication.processEvents()

        self.assertEqual(1, len(spy))
        self.assertEqual(0, self.search_box.completer_model.rowCount())
        self.assertFalse(self.search_box.completer.popup().isVisible())

        self.provider.requests.clear()
        self.search_box.line_edit.clear()
        QTest.keyClicks(self.search_box.line_edit, "pending")
        QTest.keyClick(self.search_box.line_edit, Qt.Key.Key_Return)
        self.assertEqual([], self.provider.requests)

    def test_default_provider_displays_nonempty_recent_searches(self):
        search_box = CustomSearchBox(debounce_interval_ms=0)
        try:
            search_box.show()
            search_box.line_edit.setFocus()
            search_box.refresh_suggestions()
            search_box._debounce_timer.stop()
            request_id = search_box._latest_query_id
            query = search_box._latest_query_text
            suggestions = (
                search_box._suggestions_provider._build_suggestions(query)
            )
            search_box.apply_suggestions(request_id, query, suggestions)
            self.assertGreater(search_box.completer_model.rowCount(), 0)
            self.assertEqual("最近搜索", search_box.completer_model.item(0).data(SUBTITLE_ROLE))
        finally:
            search_box.close()

    def test_direct_return_uses_raw_text_without_explicit_selection(self):
        self._prepare_suggestions()
        spy = QSignalSpy(self.search_box.searched)

        QTest.keyClick(self.search_box.line_edit, Qt.Key.Key_Return)

        self.assertEqual(1, len(spy))
        self.assertEqual("raw query", spy[0][0])

    def test_keyboard_navigation_then_return_uses_selected_suggestion(self):
        self._prepare_suggestions()
        spy = QSignalSpy(self.search_box.searched)
        request_count = len(self.provider.requests)

        QTest.keyClick(self.search_box.line_edit, Qt.Key.Key_Down)
        QApplication.processEvents()
        selected_index = self.search_box.completer.popup().currentIndex()
        self.assertTrue(selected_index.isValid())
        expected = selected_index.data(SEARCH_TEXT_ROLE)
        self.assertEqual("raw query", self.search_box.line_edit.text())
        self.assertEqual(request_count, len(self.provider.requests))
        QTest.keyClick(self.search_box.line_edit, Qt.Key.Key_Return)

        self.assertEqual(1, len(spy))
        self.assertEqual(expected, spy[0][0])
        self.assertEqual(expected, self.search_box.line_edit.text())

    def test_tag_policy_submits_first_suggestion_without_selection(self):
        self._prepare_suggestions()
        self.search_box.set_submit_first_suggestion_when_unselected(True)
        spy = QSignalSpy(self.search_box.searched)

        QTest.keyClick(self.search_box.line_edit, Qt.Key.Key_Return)

        self.assertEqual(1, len(spy))
        self.assertEqual("first-value", spy[0][0])
        self.assertEqual("first-value", self.search_box.line_edit.text())

    def test_tag_policy_uses_raw_text_when_there_are_no_suggestions(self):
        self._show_and_focus()
        self.search_box.set_submit_first_suggestion_when_unselected(True)
        self.search_box.line_edit.setText("raw tag query")
        spy = QSignalSpy(self.search_box.searched)

        QTest.keyClick(self.search_box.line_edit, Qt.Key.Key_Return)

        self.assertEqual(1, len(spy))
        self.assertEqual("raw tag query", spy[0][0])

    def test_escape_resets_keyboard_selection(self):
        self._prepare_suggestions()
        spy = QSignalSpy(self.search_box.searched)

        QTest.keyClick(self.search_box.line_edit, Qt.Key.Key_Down)
        QTest.keyClick(self.search_box.line_edit, Qt.Key.Key_Escape)
        QTest.keyClick(self.search_box.line_edit, Qt.Key.Key_Return)

        self.assertEqual(1, len(spy))
        self.assertEqual("raw query", spy[0][0])

    def test_popup_hiding_keeps_raw_text_and_clears_selection(self):
        self._prepare_suggestions()

        QTest.keyClick(self.search_box.line_edit, Qt.Key.Key_Down)
        self.assertEqual("raw query", self.search_box.line_edit.text())
        self.search_box.completer.popup().hide()
        QApplication.processEvents()

        self.assertEqual("raw query", self.search_box.line_edit.text())
        self.assertFalse(self.search_box._completion_selection_explicit)

    def test_search_button_ignores_first_suggestion_policy(self):
        self._prepare_suggestions()
        self.search_box.set_submit_first_suggestion_when_unselected(True)
        spy = QSignalSpy(self.search_box.searched)

        QTest.mouseClick(
            self.search_box.search_button,
            Qt.MouseButton.LeftButton,
        )

        self.assertEqual(1, len(spy))
        self.assertEqual("raw query", spy[0][0])

    def test_mouse_candidate_submission_emits_once(self):
        self._prepare_suggestions()
        spy = QSignalSpy(self.search_box.searched)
        popup = self.search_box.completer.popup()
        index = popup.model().index(1, 0)

        QTest.mouseClick(
            popup.viewport(),
            Qt.MouseButton.LeftButton,
            pos=popup.visualRect(index).center(),
        )

        self.assertEqual(1, len(spy))
        self.assertEqual("second-value", spy[0][0])

    def test_button_trims_text_and_empty_search_does_not_emit(self):
        spy = QSignalSpy(self.search_box.searched)
        self.search_box.line_edit.setText("  button query  ")
        QTest.mouseClick(
            self.search_box.search_button,
            Qt.MouseButton.LeftButton,
        )
        self.search_box.line_edit.clear()
        QTest.mouseClick(
            self.search_box.search_button,
            Qt.MouseButton.LeftButton,
        )

        self.assertEqual(1, len(spy))
        self.assertEqual("button query", spy[0][0])

    def test_searched_text_reaches_a_qobject_slot(self):
        receiver = SearchReceiver()
        self.search_box.searched.connect(receiver.receive)
        self.search_box.line_edit.setText("slot query")

        QTest.mouseClick(
            self.search_box.search_button,
            Qt.MouseButton.LeftButton,
        )

        self.assertEqual(["slot query"], receiver.received)

    def test_delegate_uses_option_font_and_scales_its_size_hint(self):
        model_item = QStandardItem("A long completion title")
        model_item.setData("A long completion title", TITLE_ROLE)
        model_item.setData("A subtitle", SUBTITLE_ROLE)
        model = self.search_box.completer_model
        model.appendRow(model_item)
        index: QModelIndex = model.index(0, 0)
        delegate = RichSearchCompleterItemDelegate()

        small_option = QStyleOptionViewItem()
        small_option.font = QFont("Arial", 8)
        large_option = QStyleOptionViewItem()
        large_option.font = QFont("Arial", 18)

        self.assertEqual(
            small_option.font.family(),
            delegate.title_font(small_option).family(),
        )
        self.assertIsNot(small_option.font, delegate.title_font(small_option))
        self.assertGreater(
            delegate.sizeHint(large_option, index).height(),
            delegate.sizeHint(small_option, index).height(),
        )

    def test_popup_height_is_recomputed_after_font_change(self):
        self._prepare_suggestions()
        popup = self.search_box.completer.popup()
        initial_height = popup.height()
        original_font = QFont(QApplication.font())
        try:
            larger_font = QFont(original_font)
            larger_font.setPointSize(larger_font.pointSize() + 6)
            QApplication.setFont(larger_font)
            QApplication.processEvents()
            popup.hide()
            self.search_box.completer.complete()
            QApplication.processEvents()

            self.assertGreater(popup.height(), initial_height)
            expected_rows_height = sum(
                popup.sizeHintForRow(row)
                for row in range(self.search_box.completer_model.rowCount())
            )
            self.assertGreaterEqual(
                popup.height(),
                expected_rows_height + (2 * popup.frameWidth()),
            )
        finally:
            QApplication.setFont(original_font)


if __name__ == "__main__":
    unittest.main()
