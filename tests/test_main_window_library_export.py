import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.export_library import ExportLibraryProgress, ExportLibraryResult
from ui.main_window import MainWindow


class MainWindowLibraryExportTests(unittest.TestCase):
    def test_canceling_directory_selection_does_not_start_export(self):
        export_service = Mock()
        action = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            _library_export_service=export_service,
            actionExportLibrary=action,
            statusBar=lambda: status_bar,
        )

        with patch(
            "ui.main_window.QFileDialog.getExistingDirectory",
            return_value="",
        ):
            MainWindow.export_library(window)

        export_service.start_export.assert_not_called()
        action.setEnabled.assert_not_called()
        status_bar.showMessage.assert_not_called()

    def test_selected_directory_starts_background_export(self):
        export_service = Mock()
        action = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            _library_export_service=export_service,
            actionExportLibrary=action,
            statusBar=lambda: status_bar,
        )

        with patch(
            "ui.main_window.QFileDialog.getExistingDirectory",
            return_value="C:/exports/gallery",
        ):
            MainWindow.export_library(window)

        action.setEnabled.assert_called_once_with(False)
        status_bar.showMessage.assert_called_once_with("正在导出图库…")
        export_service.start_export.assert_called_once_with("C:/exports/gallery")

    def test_start_failure_restores_action_and_shows_error(self):
        export_service = Mock()
        export_service.start_export.side_effect = RuntimeError("图库尚未初始化。")
        action = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            _library_export_service=export_service,
            actionExportLibrary=action,
            statusBar=lambda: status_bar,
        )

        with patch(
            "ui.main_window.QFileDialog.getExistingDirectory",
            return_value="C:/exports/gallery",
        ), patch("ui.main_window.QMessageBox.critical") as critical:
            MainWindow.export_library(window)

        self.assertEqual([call(False), call(True)], action.setEnabled.call_args_list)
        status_bar.clearMessage.assert_called_once_with()
        critical.assert_called_once_with(window, "导出失败", "图库尚未初始化。")

    def test_progress_is_shown_in_the_status_bar(self):
        status_bar = Mock()
        window = SimpleNamespace(statusBar=lambda: status_bar)
        progress = ExportLibraryProgress(
            51,
            "正在导出图片",
            completed=5,
            total=10,
            last_file_name="five.png",
        )

        MainWindow._on_export_library_progress_changed(window, progress)

        status_bar.showMessage.assert_called_once_with("正在导出图片（5/10）")

    def test_success_restores_action_and_shows_exact_completion_message(self):
        action = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            actionExportLibrary=action,
            statusBar=lambda: status_bar,
        )
        result = ExportLibraryResult("C:/exports/gallery", 12, 4, 2)

        with patch("ui.main_window.QMessageBox.information") as information:
            MainWindow._on_export_library_finished(window, result)

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

        with patch("ui.main_window.QMessageBox.critical") as critical:
            MainWindow._on_export_library_failed(window, "导出目录必须为空。")

        action.setEnabled.assert_called_once_with(True)
        status_bar.clearMessage.assert_called_once_with()
        critical.assert_called_once_with(window, "导出失败", "导出目录必须为空。")


if __name__ == "__main__":
    unittest.main()
