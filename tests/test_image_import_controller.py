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
from commons.signal_objects import ImportImagesRequest
from services.import_images import ImportImagesProgress, ImportImagesResult
from services.settings import create_settings_manager
from ui.main_window import MainWindow
from ui.operations.image_import_controller import ImageImportController


class ImageImportControllerTests(unittest.TestCase):
    def test_passes_the_complete_request_to_the_background_service(self):
        request = ImportImagesRequest(
            file_paths=("first.png", "second.png"),
            generate_vectors=True,
        )
        import_service = Mock()
        status_bar = Mock()
        progress_dialog = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
        )
        controller = ImageImportController(window, import_service)

        with patch(
            "ui.operations.image_import_controller.ImageImportProgressDialog",
            return_value=progress_dialog,
        ) as progress_dialog_class:
            controller.handle_import_images_request(request)

        progress_dialog_class.assert_called_once_with(window)
        progress_dialog.cancel_requested.connect.assert_called_once_with(
            import_service.cancel_import
        )
        progress_dialog.open.assert_called_once_with()
        self.assertIs(progress_dialog, controller._dialog)
        import_service.start_import.assert_called_once_with(request)
        status_bar.showMessage.assert_called_once_with("正在导入图片…")

    def test_forwards_progress_to_the_active_dialog(self):
        progress_dialog = Mock()
        window = SimpleNamespace()
        controller = ImageImportController(window, Mock())
        controller._dialog = progress_dialog
        progress = ImportImagesProgress(
            percent=38,
            status="正在写入图库",
            completed=2,
            total=5,
        )

        controller._on_import_images_progress_changed(progress)

        progress_dialog.update_progress.assert_called_once_with(progress)

    def test_taskbar_bridge_tracks_start_progress_and_terminal_states(self):
        import_service = Mock()
        status_bar = Mock()
        window = SimpleNamespace(statusBar=lambda: status_bar)
        taskbar = Mock()
        controller = ImageImportController(
            window,
            import_service,
            taskbar_progress=taskbar,
        )
        request = ImportImagesRequest(file_paths=("a.png",))

        with patch(
            "ui.operations.image_import_controller.ImageImportProgressDialog"
        ):
            controller.handle_import_images_request(request)

        taskbar.begin.assert_called_once_with()

        controller._dialog = Mock()
        controller._on_import_images_progress_changed(
            ImportImagesProgress(percent=60, status="正在生成图片向量")
        )
        taskbar.update.assert_called_once_with(60)

        with patch(
            "ui.operations.image_import_controller.QMessageBox.critical"
        ):
            controller._on_import_images_failed("boom")

        taskbar.clear.assert_called_once_with()

    def test_refreshes_library_after_import_service_completes(self):
        status_bar = Mock()
        close_progress_dialog = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
        )
        controller = ImageImportController(window, Mock())
        controller._close_image_import_progress_dialog = close_progress_dialog
        message_box = Mock()
        result = ImportImagesResult(
            imported_stickers=(object(),),
            duplicate_count=2,
            vectorized_count=1,
        )

        with patch(
            "ui.operations.image_import_controller.services.sticker_library_viewer_service.wiring.slot_refresh_content",
        ) as refresh_content, patch(
            "ui.operations.image_import_controller.QMessageBox",
            return_value=message_box,
        ):
            controller._on_import_images_finished(result)

        close_progress_dialog.assert_called_once_with()
        refresh_content.assert_called_once_with()
        message_box.setWindowTitle.assert_called_once_with("导入完成")
        message_box.setText.assert_called_once_with(
            "已导入 1 张图片，生成 1 个向量，另有 2 个重复图片未导入。"
        )
        message_box.exec.assert_called_once_with()
        status_bar.showMessage.assert_called_once_with(
            "已导入 1 张图片，生成 1 个向量",
            8000,
        )

    def test_reports_failed_import_files_in_a_collapsible_section(self):
        status_bar = Mock()
        close_progress_dialog = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
        )
        controller = ImageImportController(window, Mock())
        controller._close_image_import_progress_dialog = close_progress_dialog
        message_box = Mock()
        result = ImportImagesResult(
            imported_stickers=(object(),),
            file_errors=("corrupt.png：无法识别图片实际格式：MPO", "fake.jpg"),
        )

        with patch(
            "ui.operations.image_import_controller.services.sticker_library_viewer_service.wiring.slot_refresh_content",
        ) as refresh_content, patch(
            "ui.operations.image_import_controller.QMessageBox",
            return_value=message_box,
        ):
            controller._on_import_images_finished(result)

        refresh_content.assert_called_once_with()
        message_box.setText.assert_called_once_with("已导入 1 张图片，2 张图片导入失败。")
        message_box.setDetailedText.assert_called_once_with(
            "corrupt.png：无法识别图片实际格式：MPO\nfake.jpg"
        )
        status_bar.showMessage.assert_called_once_with(
            "已导入 1 张图片，2 张图片导入失败",
            8000,
        )

    def test_does_not_refresh_when_nothing_was_imported(self):
        status_bar = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
        )
        controller = ImageImportController(window, Mock())
        controller._close_image_import_progress_dialog = Mock()
        result = ImportImagesResult(imported_stickers=())

        with patch(
            "ui.operations.image_import_controller.services.sticker_library_viewer_service.wiring.slot_refresh_content"
        ) as refresh_content, patch(
            "ui.operations.image_import_controller.QMessageBox",
            return_value=Mock(),
        ):
            controller._on_import_images_finished(result)

        refresh_content.assert_not_called()

    def test_failure_closes_the_progress_dialog_before_showing_the_error(self):
        status_bar = Mock()
        close_progress_dialog = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
        )
        controller = ImageImportController(window, Mock())
        controller._close_image_import_progress_dialog = close_progress_dialog

        with patch(
            "ui.operations.image_import_controller.QMessageBox.critical"
        ) as critical:
            controller._on_import_images_failed("database unavailable")

        close_progress_dialog.assert_called_once_with()
        status_bar.clearMessage.assert_called_once_with()
        critical.assert_called_once_with(
            window,
            "导入失败",
            "database unavailable",
        )

    def test_cancelled_import_refreshes_only_when_sqlite_rows_were_added(self):
        status_bar = Mock()
        close_progress_dialog = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
        )
        controller = ImageImportController(window, Mock())
        controller._close_image_import_progress_dialog = close_progress_dialog
        result = ImportImagesResult(
            imported_stickers=(object(),),
            cancelled=True,
            vectorized_count=1,
        )

        with patch(
            "ui.operations.image_import_controller.services.sticker_library_viewer_service.wiring.slot_refresh_content",
        ) as refresh_content, patch(
            "ui.operations.image_import_controller.QMessageBox.information"
        ) as information:
            controller._on_import_images_cancelled(result)

        close_progress_dialog.assert_called_once_with()
        refresh_content.assert_called_once_with()
        status_bar.showMessage.assert_called_once_with(
            "导入已中止，已导入 1 张图片，已生成 1 个向量。",
            8000,
        )
        information.assert_called_once_with(
            window,
            "导入已中止",
            "导入已中止，已导入 1 张图片，已生成 1 个向量。",
        )

    def test_cancelled_import_does_not_refresh_when_no_rows_were_added(self):
        window = SimpleNamespace(
            statusBar=Mock(return_value=Mock()),
        )
        controller = ImageImportController(window, Mock())
        controller._close_image_import_progress_dialog = Mock()
        result = ImportImagesResult(imported_stickers=(), cancelled=True)

        with patch(
            "ui.operations.image_import_controller.services.sticker_library_viewer_service.wiring.slot_refresh_content",
        ) as refresh_content, patch(
            "ui.operations.image_import_controller.QMessageBox.information"
        ):
            controller._on_import_images_cancelled(result)

        refresh_content.assert_not_called()

    def test_start_failure_routes_through_the_normal_failure_handler(self):
        request = ImportImagesRequest(file_paths=("first.png",))
        import_service = Mock()
        import_service.start_import.side_effect = RuntimeError("已有图片导入任务正在进行")
        progress_dialog = Mock()
        failure_handler = Mock()
        window = SimpleNamespace(
            statusBar=Mock(return_value=Mock()),
        )
        controller = ImageImportController(window, import_service)
        controller._on_import_images_failed = failure_handler

        with patch(
            "ui.operations.image_import_controller.ImageImportProgressDialog",
            return_value=progress_dialog,
        ):
            controller.handle_import_images_request(request)

        failure_handler.assert_called_once_with("已有图片导入任务正在进行")
        window.statusBar.return_value.showMessage.assert_not_called()


class MainWindowImageImportMenuTests(unittest.TestCase):
    """集成用例：菜单 action 触发后调用图片导入控制器入口。"""

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

    def test_import_images_menu_action_triggers_controller_entry(self):
        with patch("ui.main_window.ImageImportService"), patch(
            "ui.main_window.services.export_library.LibraryExportService"
        ), patch("ui.main_window.DatabaseMaintenanceService"), patch.object(
            MainWindow, "debug_start_test_view"
        ):
            self.window = MainWindow(settings_manager=self.settings_manager)

        action = self.window.findChild(QAction, "actionImportImages")
        self.assertIsNotNone(action)

        with patch(
            "ui.operations.image_import_controller.ImageImportDialog"
        ) as dialog_class:
            action.trigger()

        dialog_class.assert_called_once_with(self.window)
        dialog_class.return_value.exec.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()