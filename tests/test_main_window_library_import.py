import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication

import apppath
import services.global_instances
from services.settings import create_settings_manager
from ui.main_window import MainWindow


WRITE_ACTION_NAMES = (
    "actionImportRepoBackup",
    "actionImportImages",
    "actionExportLibrary",
    "actionStartDatabaseMaintenance",
    "pushButtonAddSticker",
)


def make_actions():
    return {name: Mock() for name in WRITE_ACTION_NAMES}


class MainWindowWriteActionsTests(unittest.TestCase):
    def test_write_actions_are_toggled_together(self):
        actions = make_actions()
        window = SimpleNamespace(**actions)

        MainWindow.set_write_actions_enabled(window, False)
        for action in actions.values():
            action.setEnabled.assert_called_once_with(False)

        MainWindow.set_write_actions_enabled(window, True)
        for action in actions.values():
            action.setEnabled.assert_called_with(True)


class MainWindowLibraryImportMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apppath.app_path = Path(__file__).resolve().parents[1] / "src"

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.settings_manager = create_settings_manager(
            Path(self.temporary_directory.name) / "settings.toml"
        )
        self.previous_main_window = services.global_instances.main_window
        self.window = None

    def tearDown(self):
        if self.window is not None:
            self.window.close()
            self.window.deleteLater()
            self.app.processEvents()
        services.global_instances.main_window = self.previous_main_window
        self.temporary_directory.cleanup()

    def test_backup_import_menu_action_triggers_file_selection(self):
        with patch("ui.main_window.ImageImportService"), patch(
            "ui.main_window.services.export_library.LibraryExportService"
        ), patch(
            "ui.main_window.services.import_library.LibraryImportService"
        ), patch("ui.main_window.DatabaseMaintenanceService"), patch.object(
            MainWindow, "debug_start_test_view"
        ):
            self.window = MainWindow(settings_manager=self.settings_manager)

        action = self.window.findChild(QAction, "actionImportRepoBackup")
        self.assertIsNotNone(action)
        self.assertEqual("导入备份", action.text())

        with patch(
            "ui.operations.library_import_controller.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ), patch(
            "ui.operations.library_import_controller.services.import_library.preflight"
        ) as preflight:
            action.trigger()

        preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main()