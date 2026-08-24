import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMenu

import apppath
import services.global_instances
from services.database_maintenance import (
    DatabaseMaintenanceOptions,
    DatabaseMaintenanceProgress,
    DatabaseMaintenanceResult,
)
from services.settings import create_settings_manager
from ui.main_window import MainWindow
from ui.operations.database_maintenance_controller import DatabaseMaintenanceController


class DatabaseMaintenanceControllerTests(unittest.TestCase):
    def test_start_disables_action_and_starts_service(self):
        service = Mock()
        action = Mock()
        status_bar = Mock()
        options = DatabaseMaintenanceOptions()
        window = SimpleNamespace(
            actionStartDatabaseMaintenance=action,
            statusBar=lambda: status_bar,
        )
        controller = DatabaseMaintenanceController(window, service)
        controller._on_database_maintenance_failed = Mock()

        controller.start_database_maintenance(options)

        action.setEnabled.assert_called_once_with(False)
        service.start_maintenance.assert_called_once_with(options)
        status_bar.showMessage.assert_called_once_with("正在进行数据库维护…")

    def test_start_failure_routes_through_the_failure_handler(self):
        service = Mock()
        service.start_maintenance.side_effect = RuntimeError(
            "已有数据库维护任务正在运行"
        )
        action = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            actionStartDatabaseMaintenance=action,
            statusBar=lambda: status_bar,
        )
        controller = DatabaseMaintenanceController(window, service)
        failure_handler = Mock()
        controller._on_database_maintenance_failed = failure_handler

        controller.start_database_maintenance(DatabaseMaintenanceOptions())

        failure_handler.assert_called_once_with("已有数据库维护任务正在运行")

    def test_progress_updates_dialog_and_status_bar(self):
        dialog = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
        )
        controller = DatabaseMaintenanceController(window, Mock())
        controller._dialog = dialog
        progress = DatabaseMaintenanceProgress(
            50,
            "删除未引用的Blob数据",
            "正在清理Blob存储",
            4,
            8,
            False,
        )

        controller._on_database_maintenance_progress_changed(progress)

        dialog.update_progress.assert_called_once_with(progress)
        status_bar.showMessage.assert_called_once_with("正在清理Blob存储（4/8）")

    def test_completion_restores_action_and_reports_summary(self):
        action = Mock()
        status_bar = Mock()
        close_dialog = Mock()
        window = SimpleNamespace(
            actionStartDatabaseMaintenance=action,
            statusBar=lambda: status_bar,
        )
        controller = DatabaseMaintenanceController(window, Mock())
        controller._close_database_maintenance_dialog = close_dialog
        result = DatabaseMaintenanceResult(
            deleted_blob_count=2,
            vectorized_count=3,
            relinked_vector_count=1,
        )

        with patch(
            "ui.operations.database_maintenance_controller.QMessageBox.information"
        ) as information:
            controller._on_database_maintenance_finished(result)

        message = (
            "已删除 2 个未引用Blob，识别 0 张图片文字，"
            "生成 3 个向量，修复 1 个向量关联。"
        )
        action.setEnabled.assert_called_once_with(True)
        close_dialog.assert_called_once_with()
        status_bar.showMessage.assert_called_once_with(message, 8000)
        information.assert_called_once_with(window, "数据库维护完成", message)

    def test_completion_summary_includes_thumbnail_cache_deletion(self):
        result = DatabaseMaintenanceResult(
            deleted_blob_count=0,
            deleted_thumbnail_count=5,
        )

        message = DatabaseMaintenanceController._database_maintenance_summary(result)

        self.assertEqual(
            "已删除 0 个未引用Blob，识别 0 张图片文字，"
            "删除 5 个缩略图缓存，生成 0 个向量，修复 0 个向量关联。",
            message,
        )

    def test_taskbar_bridge_tracks_start_progress_and_completion(self):
        action = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            actionStartDatabaseMaintenance=action,
            statusBar=lambda: status_bar,
        )
        taskbar = Mock()
        controller = DatabaseMaintenanceController(
            window,
            Mock(),
            taskbar_progress=taskbar,
        )
        progress = DatabaseMaintenanceProgress(50, "任务", "状态", 4, 8, False)

        controller.start_database_maintenance(DatabaseMaintenanceOptions())
        controller._on_database_maintenance_progress_changed(progress)
        controller._close_database_maintenance_dialog()

        taskbar.begin.assert_called_once_with()
        taskbar.update.assert_called_once_with(50)
        taskbar.clear.assert_called_once_with()


class MainWindowDatabaseMaintenanceMenuTests(unittest.TestCase):
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

    def _create_window(self):
        with patch("ui.main_window.ImageImportService"), patch(
            "ui.main_window.services.export_library.LibraryExportService"
        ), patch("ui.main_window.DatabaseMaintenanceService"), patch.object(
            MainWindow, "debug_start_test_view"
        ):
            self.window = MainWindow(settings_manager=self.settings_manager)

    def test_repository_menu_contains_database_maintenance_action(self):
        self._create_window()

        menu = self.window.findChild(QMenu, "menu_2")
        action = self.window.findChild(QAction, "actionStartDatabaseMaintenance")
        self.assertIsNotNone(action)
        self.assertEqual("开始数据库维护", action.text())
        self.assertIn(action, menu.actions())

    def test_maintenance_menu_action_triggers_controller_entry(self):
        self._create_window()

        with patch(
            "ui.operations.database_maintenance_controller.QMessageBox.warning"
        ) as warning:
            self.window.actionStartDatabaseMaintenance.trigger()

        warning.assert_called_once_with(
            self.window,
            "无法打开",
            "仓库数据库尚未初始化。",
        )


if __name__ == "__main__":
    unittest.main()