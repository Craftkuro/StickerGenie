import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication

import services.global_instances
from services.import_library import (
    LibraryImportError,
    LibraryImportProgress,
    LibraryImportResult,
)
from services.settings import create_settings_manager
from ui.main_window import LIBRARY_IMPORT_CONFIRM_TEXT, MainWindow

import apppath


WRITE_ACTION_NAMES = (
    "actionImportRepoBackup",
    "actionImportImages",
    "actionExportLibrary",
    "actionStartDatabaseMaintenance",
    "pushButtonAddSticker",
)


def make_actions():
    return {name: Mock() for name in WRITE_ACTION_NAMES}


class MainWindowLibraryImportTests(unittest.TestCase):
    def test_canceling_file_selection_does_nothing(self):
        window = SimpleNamespace()
        with patch(
            "ui.main_window.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ), patch("ui.main_window.services.import_library.preflight") as preflight:
            MainWindow.import_library_backup(window)

        preflight.assert_not_called()

    def test_preflight_failure_shows_error_without_confirmation(self):
        window = SimpleNamespace()
        with patch(
            "ui.main_window.QFileDialog.getOpenFileName",
            return_value=("C:/backup/metadata.json", ""),
        ), patch(
            "ui.main_window.services.import_library.preflight",
            side_effect=LibraryImportError("备份包含图片，但缺少 set_1 目录。"),
        ), patch(
            "ui.main_window.MainWindow._confirm_library_import"
        ) as confirm, patch(
            "ui.main_window.QMessageBox.critical"
        ) as critical:
            MainWindow.import_library_backup(window)

        critical.assert_called_once_with(
            window,
            "导入失败",
            "备份包含图片，但缺少 set_1 目录。",
        )
        confirm.assert_not_called()

    def test_confirm_box_shows_path_and_defaults_to_no(self):
        window = SimpleNamespace()
        box = Mock()
        yes_button = Mock()
        no_button = Mock()
        box.addButton.side_effect = [yes_button, no_button]
        box.clickedButton.return_value = no_button

        with patch("ui.main_window.QMessageBox", return_value=box):
            accepted = MainWindow._confirm_library_import(
                window,
                "C:/backup/metadata.json",
            )

        self.assertFalse(accepted)
        box.setWindowTitle.assert_called_once_with("导入图库备份")
        box.setText.assert_called_once_with(
            "已选择备份文件：\nC:/backup/metadata.json"
        )
        box.setInformativeText.assert_called_once_with(LIBRARY_IMPORT_CONFIRM_TEXT)
        box.setDefaultButton.assert_called_once_with(no_button)
        box.exec.assert_called_once_with()

    def test_declining_confirmation_does_not_start_import(self):
        service = Mock()
        window = SimpleNamespace(
            _library_import_service=service,
            _library_import_progress_dialog=None,
            statusBar=Mock(return_value=Mock()),
            _confirm_library_import=Mock(return_value=False),
        )
        with patch(
            "ui.main_window.QFileDialog.getOpenFileName",
            return_value=("C:/backup/metadata.json", ""),
        ), patch("ui.main_window.services.import_library.preflight"), patch(
            "ui.main_window.MainWindow._set_write_actions_enabled"
        ) as set_actions, patch(
            "ui.main_window.LibraryImportProgressDialog"
        ) as dialog_class:
            MainWindow.import_library_backup(window)

        service.start_import.assert_not_called()
        set_actions.assert_not_called()
        dialog_class.assert_not_called()

    def test_confirmed_import_disables_actions_and_starts_service(self):
        service = Mock()
        status_bar = Mock()
        dialog = Mock()
        actions = make_actions()
        window = SimpleNamespace(
            _library_import_service=service,
            _library_import_progress_dialog=None,
            statusBar=lambda: status_bar,
            **actions,
            _confirm_library_import=Mock(return_value=True),
            _set_write_actions_enabled=Mock(),
        )
        with patch(
            "ui.main_window.QFileDialog.getOpenFileName",
            return_value=("C:/backup/metadata.json", ""),
        ), patch("ui.main_window.services.import_library.preflight"), patch(
            "ui.main_window.LibraryImportProgressDialog",
            return_value=dialog,
        ):
            MainWindow.import_library_backup(window)

        window._set_write_actions_enabled.assert_called_once_with(False)
        dialog.cancel_requested.connect.assert_called_once_with(
            service.cancel_import
        )
        dialog.open.assert_called_once_with()
        self.assertIs(dialog, window._library_import_progress_dialog)
        status_bar.showMessage.assert_called_once_with("正在导入图库备份…")
        service.start_import.assert_called_once_with("C:/backup/metadata.json")

    def test_start_failure_routes_through_the_failure_handler(self):
        service = Mock()
        service.start_import.side_effect = RuntimeError(
            "已有图库备份导入任务正在运行。"
        )
        failure_handler = Mock()
        window = SimpleNamespace(
            _library_import_service=service,
            _library_import_progress_dialog=None,
            _on_import_library_failed=failure_handler,
            statusBar=Mock(return_value=Mock()),
            _confirm_library_import=Mock(return_value=True),
            _set_write_actions_enabled=Mock(),
        )
        with patch(
            "ui.main_window.QFileDialog.getOpenFileName",
            return_value=("C:/backup/metadata.json", ""),
        ), patch("ui.main_window.services.import_library.preflight"), patch(
            "ui.main_window.LibraryImportProgressDialog"
        ):
            MainWindow.import_library_backup(window)

        failure_handler.assert_called_once_with(
            "已有图库备份导入任务正在运行。"
        )

    def test_progress_updates_dialog_and_status_bar(self):
        dialog = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            _library_import_progress_dialog=dialog,
            statusBar=lambda: status_bar,
        )
        progress = LibraryImportProgress(
            percent=50,
            status="正在导入备份图片",
            completed=3,
            total=8,
            cancellable=True,
        )

        MainWindow._on_import_library_progress_changed(window, progress)

        dialog.update_progress.assert_called_once_with(progress)
        status_bar.showMessage.assert_called_once_with(
            "正在导入备份图片（3/8）"
        )

    def test_finished_restores_actions_refreshes_and_reports_summary(self):
        status_bar = Mock()
        finish = Mock()
        refresh = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
            _finish_library_import=finish,
            _refresh_after_library_import=refresh,
            _library_import_summary=MainWindow._library_import_summary,
        )
        result = LibraryImportResult(
            "C:/backup/metadata.json",
            added_image_count=2,
            merged_tag_image_count=1,
            added_tag_count=3,
        )

        with patch("ui.main_window.QMessageBox.information") as information:
            MainWindow._on_import_library_finished(window, result)

        finish.assert_called_once_with()
        refresh.assert_called_once_with(result)
        status_bar.showMessage.assert_called_once_with(
            "导入完成，新增图片 2 张，为 1 张已有图片合并标签，新增标签 3 个。",
            8000,
        )
        information.assert_called_once_with(
            window,
            "导入完成",
            "导入完成，新增图片 2 张，为 1 张已有图片合并标签，新增标签 3 个。"
            "\n\n为了实现完整的搜索功能，请在数据库维护功能里"
            "按需重新进行OCR和生成图片特征索引。",
        )

    def test_finished_with_damaged_images_and_errors_shows_warnings(self):
        errors = tuple(f"错误 {index}" for index in range(12))
        window = SimpleNamespace(
            statusBar=Mock(return_value=Mock()),
            _finish_library_import=Mock(),
            _refresh_after_library_import=Mock(),
            _library_import_summary=MainWindow._library_import_summary,
        )
        result = LibraryImportResult(
            "C:/backup/metadata.json",
            added_image_count=1,
            damaged_count=2,
            errors=errors,
        )

        with patch("ui.main_window.QMessageBox.information") as information, patch(
            "ui.main_window.QMessageBox.warning"
        ) as warning:
            MainWindow._on_import_library_finished(window, result)

        self.assertIn("跳过 2 张损坏图片", information.call_args.args[2])
        details = warning.call_args.args[2]
        self.assertIn("错误 0", details)
        self.assertIn("错误 9", details)
        self.assertNotIn("错误 10", details)
        self.assertIn("另有 2 项未显示。", details)

    def test_cancelled_restores_actions_refreshes_and_reports_partial_counts(self):
        status_bar = Mock()
        finish = Mock()
        refresh = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
            _finish_library_import=finish,
            _refresh_after_library_import=refresh,
            _library_import_summary=MainWindow._library_import_summary,
        )
        result = LibraryImportResult(
            "C:/backup/metadata.json",
            added_image_count=1,
            added_tag_count=2,
            cancelled=True,
        )

        with patch("ui.main_window.QMessageBox.information") as information:
            MainWindow._on_import_library_cancelled(window, result)

        finish.assert_called_once_with()
        refresh.assert_called_once_with(result)
        message = information.call_args.args[2]
        self.assertIn("导入已中止，新增图片 1 张", message)
        self.assertIn("未引用的Blob文件", message)
        self.assertIn("补做OCR和生成图片特征索引", message)
        status_bar.showMessage.assert_called_once_with(message, 8000)

    def test_failed_restores_actions_and_shows_error(self):
        status_bar = Mock()
        finish = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
            _finish_library_import=finish,
        )

        with patch("ui.main_window.QMessageBox.critical") as critical:
            MainWindow._on_import_library_failed(window, "database unavailable")

        finish.assert_called_once_with()
        status_bar.clearMessage.assert_called_once_with()
        critical.assert_called_once_with(window, "导入失败", "database unavailable")

    def test_refresh_after_import_only_when_counts_changed(self):
        window = SimpleNamespace(customSearchBox=Mock())
        with patch(
            "ui.main_window.services.sticker_library_viewer_service.wiring.slot_refresh_content"
        ) as refresh_content:
            MainWindow._refresh_after_library_import(
                window,
                LibraryImportResult("backup"),
            )
            refresh_content.assert_not_called()
            window.customSearchBox.refresh_suggestions.assert_not_called()

            MainWindow._refresh_after_library_import(
                window,
                LibraryImportResult(
                    "backup",
                    added_image_count=1,
                    added_tag_count=1,
                ),
            )
            refresh_content.assert_called_once_with()
            window.customSearchBox.refresh_suggestions.assert_called_once_with()

    def test_write_actions_are_toggled_together(self):
        actions = make_actions()
        window = SimpleNamespace(**actions)

        MainWindow._set_write_actions_enabled(window, False)
        for action in actions.values():
            action.setEnabled.assert_called_once_with(False)

        MainWindow._set_write_actions_enabled(window, True)
        for action in actions.values():
            action.setEnabled.assert_called_with(True)

    def test_finish_closes_dialog_and_restores_actions(self):
        close_dialog = Mock()
        set_actions = Mock()
        window = SimpleNamespace(
            _close_library_import_progress_dialog=close_dialog,
            _set_write_actions_enabled=set_actions,
        )

        MainWindow._finish_library_import(window)

        close_dialog.assert_called_once_with()
        set_actions.assert_called_once_with(True)


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
            "ui.main_window.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ), patch("ui.main_window.services.import_library.preflight") as preflight:
            action.trigger()

        preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main()
