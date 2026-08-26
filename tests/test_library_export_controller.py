import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QStandardPaths
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication

import apppath
import services.global_instances
from services.export_library import ExportLibraryProgress, ExportLibraryResult
from services.settings import create_settings_manager
from ui.main_window import MainWindow
from ui.operations.library_export_controller import LibraryExportController


class LibraryExportControllerTests(unittest.TestCase):
    def test_canceling_directory_selection_does_not_start_export(self):
        export_service = Mock()
        action = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            actionExportLibrary=action,
            statusBar=lambda: status_bar,
        )
        controller = LibraryExportController(window, export_service)

        with patch(
            "ui.operations.library_export_controller.QFileDialog.getExistingDirectory",
            return_value="",
        ):
            controller.export_library()

        export_service.start_export.assert_not_called()
        action.setEnabled.assert_not_called()
        status_bar.showMessage.assert_not_called()

    def test_selected_directory_starts_background_export(self):
        export_service = Mock()
        action = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            actionExportLibrary=action,
            statusBar=lambda: status_bar,
        )
        controller = LibraryExportController(window, export_service)

        with patch(
            "ui.operations.library_export_controller.QFileDialog.getExistingDirectory",
            return_value="C:/exports/gallery",
        ) as dialog_mock:
            controller.export_library()

        dialog_mock.assert_called_once_with(
            window,
            "选择导出目录",
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DesktopLocation
            ),
        )
        action.setEnabled.assert_called_once_with(False)
        status_bar.showMessage.assert_called_once_with("正在导出图库…")
        export_service.start_export.assert_called_once_with("C:/exports/gallery")

    def test_start_failure_restores_action_and_shows_error(self):
        export_service = Mock()
        export_service.start_export.side_effect = RuntimeError("图库尚未初始化。")
        action = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            actionExportLibrary=action,
            statusBar=lambda: status_bar,
        )
        controller = LibraryExportController(window, export_service)

        with patch(
            "ui.operations.library_export_controller.QFileDialog.getExistingDirectory",
            return_value="C:/exports/gallery",
        ), patch(
            "ui.operations.library_export_controller.QMessageBox.critical"
        ) as critical:
            controller.export_library()

        self.assertEqual([call(False), call(True)], action.setEnabled.call_args_list)
        status_bar.clearMessage.assert_called_once_with()
        critical.assert_called_once_with(window, "导出失败", "图库尚未初始化。")

    def test_progress_is_shown_in_the_status_bar(self):
        status_bar = Mock()
        window = SimpleNamespace(statusBar=lambda: status_bar)
        controller = LibraryExportController(window, Mock())
        progress = ExportLibraryProgress(
            51,
            "正在导出图片",
            completed=5,
            total=10,
            last_file_name="five.png",
        )

        controller._on_export_library_progress_changed(progress)

        status_bar.showMessage.assert_called_once_with("正在导出图片（5/10）")

    def test_success_restores_action_and_shows_exact_completion_message(self):
        action = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            actionExportLibrary=action,
            statusBar=lambda: status_bar,
        )
        controller = LibraryExportController(window, Mock())
        result = ExportLibraryResult("C:/exports/gallery", 12, 4, 2)

        with patch(
            "ui.operations.library_export_controller.QMessageBox.information"
        ) as information:
            controller._on_export_library_finished(result)

        action.setEnabled.assert_called_once_with(True)
        status_bar.showMessage.assert_called_once_with(
            "已导出 12 个图片和 4 个标签",
            8000,
        )
        information.assert_called_once_with(
            window,
            "导出完成",
            "导出完成，已导出12个图片和4个标签。",
        )

    def test_async_failure_restores_action_and_shows_error(self):
        action = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            actionExportLibrary=action,
            statusBar=lambda: status_bar,
        )
        controller = LibraryExportController(window, Mock())

        with patch(
            "ui.operations.library_export_controller.QMessageBox.critical"
        ) as critical:
            controller._on_export_library_failed("导出目录必须为空。")

        action.setEnabled.assert_called_once_with(True)
        status_bar.clearMessage.assert_called_once_with()
        critical.assert_called_once_with(window, "导出失败", "导出目录必须为空。")


class MainWindowLibraryExportMenuTests(unittest.TestCase):
    """集成用例：菜单 action 触发后调用导出控制器入口。"""

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
            self.app.processEvents()
        services.global_instances.main_window = self.previous_main_window
        self.temporary_directory.cleanup()

    def test_export_menu_action_triggers_controller_entry(self):
        with patch("ui.main_window.ImageImportService"), patch(
            "ui.main_window.services.export_library.LibraryExportService"
        ), patch("ui.main_window.DatabaseMaintenanceService"), patch.object(
            MainWindow, "debug_start_test_view"
        ):
            self.window = MainWindow(settings_manager=self.settings_manager)

        action = self.window.findChild(QAction, "actionExportLibrary")
        self.assertIsNotNone(action)

        with patch(
            "ui.operations.library_export_controller.QFileDialog.getExistingDirectory",
            return_value="",
        ):
            action.trigger()


if __name__ == "__main__":
    unittest.main()
