import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.import_library import (
    LibraryImportError,
    LibraryImportProgress,
    LibraryImportResult,
)
from ui.operations.library_import_controller import (
    LIBRARY_IMPORT_CONFIRM_TEXT,
    LibraryImportController,
)


class LibraryImportControllerTests(unittest.TestCase):
    def test_canceling_file_selection_does_nothing(self):
        controller = LibraryImportController(SimpleNamespace(), Mock())
        with patch(
            "ui.operations.library_import_controller.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ), patch(
            "ui.operations.library_import_controller.services.import_library.preflight"
        ) as preflight:
            controller.import_library_backup()

        preflight.assert_not_called()

    def test_preflight_failure_shows_error_without_confirmation(self):
        window = SimpleNamespace()
        controller = LibraryImportController(window, Mock())
        with patch(
            "ui.operations.library_import_controller.QFileDialog.getOpenFileName",
            return_value=("C:/backup/metadata.json", ""),
        ), patch(
            "ui.operations.library_import_controller.services.import_library.preflight",
            side_effect=LibraryImportError("备份包含图片，但缺少 set_1 目录。"),
        ), patch.object(
            controller, "_confirm_library_import"
        ) as confirm, patch(
            "ui.operations.library_import_controller.QMessageBox.critical"
        ) as critical:
            controller.import_library_backup()

        critical.assert_called_once_with(
            window,
            "导入失败",
            "备份包含图片，但缺少 set_1 目录。",
        )
        confirm.assert_not_called()

    def test_confirm_box_shows_path_and_defaults_to_no(self):
        window = SimpleNamespace()
        controller = LibraryImportController(window, Mock())
        box = Mock()
        yes_button = Mock()
        no_button = Mock()
        box.addButton.side_effect = [yes_button, no_button]
        box.clickedButton.return_value = no_button

        with patch(
            "ui.operations.library_import_controller.QMessageBox",
            return_value=box,
        ):
            accepted = controller._confirm_library_import(
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
            statusBar=Mock(return_value=Mock()),
            set_write_actions_enabled=Mock(),
        )
        controller = LibraryImportController(window, service)
        controller._confirm_library_import = Mock(return_value=False)
        with patch(
            "ui.operations.library_import_controller.QFileDialog.getOpenFileName",
            return_value=("C:/backup/metadata.json", ""),
        ), patch(
            "ui.operations.library_import_controller.services.import_library.preflight"
        ), patch(
            "ui.operations.library_import_controller.LibraryImportProgressDialog"
        ) as dialog_class:
            controller.import_library_backup()

        service.start_import.assert_not_called()
        window.set_write_actions_enabled.assert_not_called()
        dialog_class.assert_not_called()

    def test_confirmed_import_disables_actions_and_starts_service(self):
        service = Mock()
        status_bar = Mock()
        dialog = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
            set_write_actions_enabled=Mock(),
        )
        controller = LibraryImportController(window, service)
        controller._confirm_library_import = Mock(return_value=True)
        with patch(
            "ui.operations.library_import_controller.QFileDialog.getOpenFileName",
            return_value=("C:/backup/metadata.json", ""),
        ), patch(
            "ui.operations.library_import_controller.services.import_library.preflight"
        ), patch(
            "ui.operations.library_import_controller.LibraryImportProgressDialog",
            return_value=dialog,
        ):
            controller.import_library_backup()

        window.set_write_actions_enabled.assert_called_once_with(False)
        dialog.cancel_requested.connect.assert_called_once_with(
            controller._on_import_cancel_requested
        )
        dialog.open.assert_called_once_with()
        self.assertIs(dialog, controller._dialog)
        status_bar.showMessage.assert_called_once_with("正在导入图库备份…")
        service.start_import.assert_called_once_with("C:/backup/metadata.json")

    def test_start_failure_routes_through_the_failure_handler(self):
        service = Mock()
        service.start_import.side_effect = RuntimeError(
            "已有图库备份导入任务正在运行。"
        )
        failure_handler = Mock()
        window = SimpleNamespace(
            statusBar=Mock(return_value=Mock()),
            set_write_actions_enabled=Mock(),
        )
        controller = LibraryImportController(window, service)
        controller._confirm_library_import = Mock(return_value=True)
        controller._on_import_library_failed = failure_handler
        with patch(
            "ui.operations.library_import_controller.QFileDialog.getOpenFileName",
            return_value=("C:/backup/metadata.json", ""),
        ), patch(
            "ui.operations.library_import_controller.services.import_library.preflight"
        ), patch(
            "ui.operations.library_import_controller.LibraryImportProgressDialog"
        ):
            controller.import_library_backup()

        failure_handler.assert_called_once_with(
            "已有图库备份导入任务正在运行。"
        )

    def test_progress_updates_dialog_and_status_bar(self):
        dialog = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
        )
        controller = LibraryImportController(window, Mock())
        controller._dialog = dialog
        progress = LibraryImportProgress(
            percent=50,
            status="正在导入备份图片",
            completed=3,
            total=8,
            cancellable=True,
        )

        controller._on_import_library_progress_changed(progress)

        dialog.update_progress.assert_called_once_with(progress)
        status_bar.showMessage.assert_called_once_with(
            "正在导入备份图片（3/8）"
        )

    def test_progress_shows_cancelling_status_in_the_status_bar(self):
        dialog = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
        )
        controller = LibraryImportController(window, Mock())
        controller._dialog = dialog
        controller._cancelling = True
        progress = LibraryImportProgress(
            percent=50,
            status="正在导入备份图片",
            completed=3,
            total=8,
            cancellable=True,
        )

        controller._on_import_library_progress_changed(progress)

        dialog.update_progress.assert_called_once_with(progress)
        status_bar.showMessage.assert_called_once_with("正在中止导入（3/8）")

    def test_cancel_request_forwards_to_service_and_updates_status_bar(self):
        service = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
        )
        controller = LibraryImportController(window, service)

        controller._on_import_cancel_requested()

        service.cancel_import.assert_called_once_with()
        status_bar.showMessage.assert_called_once_with("正在中止导入…")
        self.assertTrue(controller._cancelling)

    def test_finished_restores_actions_refreshes_and_reports_summary(self):
        status_bar = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
        )
        controller = LibraryImportController(window, Mock())
        finish = Mock()
        refresh = Mock()
        controller._finish_library_import = finish
        controller._refresh_after_library_import = refresh
        result = LibraryImportResult(
            "C:/backup/metadata.json",
            added_image_count=2,
            merged_tag_image_count=1,
            added_tag_count=3,
        )

        with patch(
            "ui.operations.library_import_controller.QMessageBox.information"
        ) as information:
            controller._on_import_library_finished(result)

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
        )
        controller = LibraryImportController(window, Mock())
        controller._finish_library_import = Mock()
        controller._refresh_after_library_import = Mock()
        result = LibraryImportResult(
            "C:/backup/metadata.json",
            added_image_count=1,
            damaged_count=2,
            errors=errors,
        )

        with patch(
            "ui.operations.library_import_controller.QMessageBox.information"
        ) as information, patch(
            "ui.operations.library_import_controller.QMessageBox.warning"
        ) as warning:
            controller._on_import_library_finished(result)

        self.assertIn("跳过 2 张损坏图片", information.call_args.args[2])
        details = warning.call_args.args[2]
        self.assertIn("错误 0", details)
        self.assertIn("错误 9", details)
        self.assertNotIn("错误 10", details)
        self.assertIn("另有 2 项未显示。", details)

    def test_cancelled_restores_actions_refreshes_and_reports_partial_counts(self):
        status_bar = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
        )
        controller = LibraryImportController(window, Mock())
        finish = Mock()
        refresh = Mock()
        controller._finish_library_import = finish
        controller._refresh_after_library_import = refresh
        result = LibraryImportResult(
            "C:/backup/metadata.json",
            added_image_count=1,
            added_tag_count=2,
            cancelled=True,
        )

        with patch(
            "ui.operations.library_import_controller.QMessageBox.information"
        ) as information:
            controller._on_import_library_cancelled(result)

        finish.assert_called_once_with()
        refresh.assert_called_once_with(result)
        message = information.call_args.args[2]
        self.assertIn("导入已中止，新增图片 1 张", message)
        self.assertIn("未引用的Blob文件", message)
        self.assertIn("补做OCR和生成图片特征索引", message)
        status_bar.showMessage.assert_called_once_with(message, 8000)

    def test_failed_restores_actions_and_shows_error(self):
        status_bar = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
        )
        controller = LibraryImportController(window, Mock())
        finish = Mock()
        controller._finish_library_import = finish

        with patch(
            "ui.operations.library_import_controller.QMessageBox.critical"
        ) as critical:
            controller._on_import_library_failed("database unavailable")

        finish.assert_called_once_with()
        status_bar.clearMessage.assert_called_once_with()
        critical.assert_called_once_with(window, "导入失败", "database unavailable")

    def test_refresh_after_import_only_when_counts_changed(self):
        window = SimpleNamespace(customSearchBox=Mock())
        controller = LibraryImportController(window, Mock())
        with patch(
            "ui.operations.library_import_controller.services.sticker_library_viewer_service.wiring.slot_refresh_content"
        ) as refresh_content:
            controller._refresh_after_library_import(
                LibraryImportResult("backup"),
            )
            refresh_content.assert_not_called()
            window.customSearchBox.refresh_suggestions.assert_not_called()

            controller._refresh_after_library_import(
                LibraryImportResult(
                    "backup",
                    added_image_count=1,
                    added_tag_count=1,
                ),
            )
            refresh_content.assert_called_once_with()
            window.customSearchBox.refresh_suggestions.assert_called_once_with()

    def test_finish_closes_dialog_and_restores_actions(self):
        close_dialog = Mock()
        window = SimpleNamespace(set_write_actions_enabled=Mock())
        controller = LibraryImportController(window, Mock())
        controller._close_library_import_progress_dialog = close_dialog

        controller._finish_library_import()

        close_dialog.assert_called_once_with()
        window.set_write_actions_enabled.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()