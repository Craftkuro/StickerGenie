import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMenu

import apppath
import services.global_instances
from services.settings import create_settings_manager
from ui.main_window import MainWindow


class MainWindowDeveloperToolsTests(unittest.TestCase):
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
        self.window = None

    def tearDown(self):
        if self.window is not None:
            self.window.close()
            self.window.deleteLater()
        services.global_instances.main_window = self.previous_main_window
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temporary_directory.cleanup()

    def _create_window(self, *, frozen: bool):
        with patch.object(sys, "frozen", frozen, create=True), patch(
            "ui.main_window.ImageImportService"
        ), patch(
            "ui.main_window.services.export_library.LibraryExportService"
        ), patch.object(MainWindow, "debug_start_test_view"):
            self.window = MainWindow(settings_manager=self.settings_manager)

    def test_developer_tools_are_added_when_running_from_source(self):
        self._create_window(frozen=False)

        menu = self.window.findChild(QMenu, "menu_6")
        action = self.window.findChild(QAction, "actionCustomDebug")

        self.assertIsNotNone(menu)
        self.assertEqual("开发工具", menu.title())
        self.assertIsNotNone(action)
        self.assertEqual("自定义调试操作", action.text())
        self.assertIn(action, menu.actions())

    def test_developer_tools_are_omitted_from_frozen_build(self):
        self._create_window(frozen=True)

        self.assertIsNone(self.window.findChild(QMenu, "menu_6"))
        self.assertIsNone(
            self.window.findChild(QAction, "actionCustomDebug")
        )


if __name__ == "__main__":
    unittest.main()
