import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem
from PyQt6.QtWidgets import QApplication, QTextEdit

import apppath
from ui.page_advanced_search_result import AdvancedSearchResultPage
from ui.page_finite_sticker_collection import FiniteStickerCollectionPage


class AdvancedSearchResultPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apppath.setup_data_path(Path(__file__).resolve().parents[1] / "src")

    def test_page_inherits_finite_page_and_initializes_copy_controls(self):
        page = AdvancedSearchResultPage("A AND B", [])

        self.assertIsInstance(page, FiniteStickerCollectionPage)
        self.assertIsInstance(page.expression_text_edit, QTextEdit)
        self.assertEqual("A AND B", page.expression_text_edit.toPlainText())
        self.assertTrue(page.expression_text_edit.isReadOnly())
        interaction_flags = page.expression_text_edit.textInteractionFlags()
        self.assertTrue(
            interaction_flags & Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.assertTrue(
            interaction_flags
            & Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.assertEqual("表达式", page.expression_label.text())
        self.assertEqual("复制", page.copy_button.text())
        self.assertEqual(0, page.listViewStickerList.model().rowCount())
        toolbar_widgets = [
            page.toolbar.widgetForAction(action)
            for action in page.toolbar.actions()
        ]
        self.assertEqual(
            [
                page.expression_label,
                page.expression_text_edit,
                page.copy_button,
            ],
            toolbar_widgets[:3],
        )
        self.assertIs(page.display_size_slider, toolbar_widgets[-1])
        page.close()

    def test_copy_button_copies_the_read_only_expression(self):
        page = AdvancedSearchResultPage("(A OR B) AND NOT C", [])
        clipboard = Mock()

        with patch(
            "ui.page_advanced_search_result.QApplication.clipboard",
            return_value=clipboard,
        ):
            page.copy_button.click()

        clipboard.setText.assert_called_once_with("(A OR B) AND NOT C")
        page.close()

    def test_expression_page_does_not_submit_or_refresh_queries(self):
        page = AdvancedSearchResultPage("A", [])
        previous_model = page.listViewStickerList.model()
        previous_model.appendRow(QStandardItem("existing"))
        page.refresh_content = Mock()

        page.copy_button.click()

        page.refresh_content.assert_not_called()
        self.assertIs(previous_model, page.listViewStickerList.model())
        self.assertEqual(1, previous_model.rowCount())
        page.close()


if __name__ == "__main__":
    unittest.main()
