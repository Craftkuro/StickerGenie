import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication, QPushButton

import apppath
import services.global_instances
from services.settings import create_settings_manager
from ui.main_window import MainWindow
from ui.widgets.custom_search_box import CustomSearchBox


class MainWindowSearchBoxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apppath.app_path = Path(__file__).resolve().parents[1] / "src"

    def setUp(self):
        self.previous_main_window = services.global_instances.main_window
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.settings_manager = create_settings_manager(
            Path(self.temporary_directory.name) / "settings.toml"
        )
        with patch("ui.main_window.ImageImportService"), patch(
            "ui.main_window.services.export_library.LibraryExportService"
        ), patch.object(MainWindow, "debug_start_test_view"):
            self.window = MainWindow(settings_manager=self.settings_manager)

    def tearDown(self):
        self.window.customSearchBox.completer.popup().hide()
        self.window.close()
        services.global_instances.main_window = self.previous_main_window
        QApplication.processEvents()
        self.temporary_directory.cleanup()

    def test_ui_uses_custom_search_box_without_legacy_controls(self):
        search_box = self.window.customSearchBox

        self.assertIsInstance(search_box, CustomSearchBox)
        self.assertIs(
            search_box,
            self.window.widgetUnifiedBar.layout().itemAt(3).widget(),
        )
        self.assertEqual(7, self.window.widgetUnifiedBar.layout().count())
        self.assertIs(
            self.window.searchTypeComboBox,
            self.window.widgetUnifiedBar.layout().itemAt(2).widget(),
        )
        self.assertEqual("tag", self.window.searchTypeComboBox.currentData())
        self.assertEqual(
            ["tag", "text", "filename"],
            [
                self.window.searchTypeComboBox.itemData(index)
                for index in range(self.window.searchTypeComboBox.count())
            ],
        )
        self.assertTrue(
            self.window.customSearchBox
            ._submit_first_suggestion_when_unselected
        )
        self.assertIsNone(
            self.window.findChild(QObject, "pushButtonStartSearch")
        )

    def test_search_signal_is_connected_to_main_window_slot(self):
        with patch(
            "ui.main_window.services.search.open_search_results",
            return_value=2,
        ) as open_results:
            self.window.customSearchBox.searched.emit("test query")

        open_results.assert_called_once_with(
            self.window._current_search_type(),
            "test query",
        )
        self.assertEqual(["test query"], self.window._search_history.values())

    def test_search_button_matches_main_menu_button_dimensions(self):
        search_button = self.window.customSearchBox.search_button
        main_menu_button = self.window.pushButtonMainMenu

        self.assertIsInstance(search_button, QPushButton)
        self.assertEqual(main_menu_button.iconSize(), search_button.iconSize())
        self.assertEqual(main_menu_button.sizeHint(), search_button.sizeHint())

    def test_text_search_routes_with_text_type(self):
        self.window.searchTypeComboBox.setCurrentIndex(1)

        self.assertFalse(
            self.window.customSearchBox
            ._submit_first_suggestion_when_unselected
        )

        with patch(
            "ui.main_window.services.search.open_search_results",
            return_value=0,
        ) as open_results:
            self.window.customSearchBox.searched.emit("图片文字")

        self.assertEqual("text", open_results.call_args.args[0].value)

    def test_file_name_search_routes_with_file_name_type(self):
        self.window.searchTypeComboBox.setCurrentIndex(2)

        self.assertEqual(
            "filename",
            self.window.searchTypeComboBox.currentData(),
        )
        self.assertFalse(
            self.window.customSearchBox
            ._submit_first_suggestion_when_unselected
        )
        self.assertEqual(
            "搜索图片文件名...",
            self.window.customSearchBox.line_edit.placeholderText(),
        )

        with patch(
            "ui.main_window.services.search.open_search_results",
            return_value=0,
        ) as open_results:
            self.window.customSearchBox.searched.emit("cat.png")

        self.assertEqual("filename", open_results.call_args.args[0].value)

    def test_close_persists_recent_searches_latest_first(self):
        with patch(
            "ui.main_window.services.search.open_search_results",
            return_value=0,
        ):
            self.window.customSearchBox.searched.emit("first")
            self.window.customSearchBox.searched.emit("second")

        self.window.close()
        saved_manager = create_settings_manager(
            Path(self.temporary_directory.name) / "settings.toml"
        )

        self.assertEqual(
            ["second", "first"],
            saved_manager.get("recent_searches"),
        )


if __name__ == "__main__":
    unittest.main()
