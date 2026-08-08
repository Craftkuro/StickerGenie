import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication

import apppath
import services.global_instances
from ui.main_window import MainWindow
from ui.widgets.custom_search_box import CustomSearchBox


class MainWindowSearchBoxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apppath.app_path = Path(__file__).resolve().parents[1] / "src"

    def setUp(self):
        self.previous_main_window = services.global_instances.main_window
        with patch(
            "ui.main_window.services.import_images.ImageImportService"
        ), patch(
            "ui.main_window.services.export_library.LibraryExportService"
        ), patch.object(MainWindow, "debug_start_test_view"):
            self.window = MainWindow()

    def tearDown(self):
        self.window.customSearchBox.completer.popup().hide()
        self.window.close()
        services.global_instances.main_window = self.previous_main_window
        QApplication.processEvents()

    def test_ui_uses_custom_search_box_without_legacy_controls(self):
        search_box = self.window.customSearchBox

        self.assertIsInstance(search_box, CustomSearchBox)
        self.assertIs(
            search_box,
            self.window.widgetUnifiedBar.layout().itemAt(2).widget(),
        )
        self.assertEqual(4, self.window.widgetUnifiedBar.layout().count())
        self.assertIsNone(self.window.findChild(QObject, "comboBox"))
        self.assertIsNone(
            self.window.findChild(QObject, "pushButtonStartSearch")
        )

    def test_search_signal_is_connected_to_main_window_slot(self):
        with patch("ui.main_window.logger.info") as info:
            self.window.customSearchBox.searched.emit("test query")

        info.assert_called_once()
        self.assertIn("test query", info.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
