import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from commons.signal_objects import ImportImagesRequest
from services.import_images import ImportImagesProgress, ImportImagesResult
from ui.main_window import MainWindow


class MainWindowImageImportTests(unittest.TestCase):
    def test_passes_the_complete_request_to_the_background_service(self):
        request = ImportImagesRequest(
            file_paths=("first.png", "second.png"),
            generate_vectors=True,
        )
        import_service = Mock()
        status_bar = Mock()
        progress_dialog = Mock()
        window = SimpleNamespace(
            _image_import_service=import_service,
            _image_import_progress_dialog=None,
            statusBar=lambda: status_bar,
        )

        with patch(
            "ui.main_window.ImageImportProgressDialog",
            return_value=progress_dialog,
        ) as progress_dialog_class:
            MainWindow.handle_import_images_request(window, request)

        progress_dialog_class.assert_called_once_with(window)
        progress_dialog.cancel_requested.connect.assert_called_once_with(
            import_service.cancel_import
        )
        progress_dialog.open.assert_called_once_with()
        self.assertIs(progress_dialog, window._image_import_progress_dialog)
        import_service.start_import.assert_called_once_with(request)
        status_bar.showMessage.assert_called_once_with("正在导入图片…")

    def test_forwards_progress_to_the_active_dialog(self):
        progress_dialog = Mock()
        window = SimpleNamespace(_image_import_progress_dialog=progress_dialog)
        progress = ImportImagesProgress(
            percent=38,
            status="正在导入图片",
            completed=2,
            total=5,
            last_file_name="second.png",
        )

        MainWindow._on_import_images_progress_changed(window, progress)

        progress_dialog.update_progress.assert_called_once_with(progress)

    def test_refreshes_library_after_import_service_completes(self):
        status_bar = Mock()
        close_progress_dialog = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
            _close_image_import_progress_dialog=close_progress_dialog,
        )
        result = ImportImagesResult(
            imported_stickers=(object(),),
            duplicate_count=2,
            vectorized_count=1,
        )

        with patch(
            "ui.main_window.services.sticker_library_viewer_service.wiring.slot_refresh_content",
        ) as refresh_content, patch(
            "ui.main_window.QMessageBox.information"
        ) as information:
            MainWindow._on_import_images_finished(window, result)

        close_progress_dialog.assert_called_once_with()
        refresh_content.assert_called_once_with()
        information.assert_called_once_with(
            window,
            "导入完成",
            "已导入 1 张图片，另有 2 个重复图片未导入。",
        )
        status_bar.showMessage.assert_called_once_with(
            "已导入 1 张图片，生成 1 个向量",
            8000,
        )

    def test_does_not_refresh_when_nothing_was_imported(self):
        status_bar = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
            _close_image_import_progress_dialog=Mock(),
        )
        result = ImportImagesResult(imported_stickers=())

        with patch(
            "ui.main_window.services.sticker_library_viewer_service.wiring.slot_refresh_content"
        ) as refresh_content, patch(
            "ui.main_window.QMessageBox.information"
        ):
            MainWindow._on_import_images_finished(window, result)

        refresh_content.assert_not_called()

    def test_failure_closes_the_progress_dialog_before_showing_the_error(self):
        status_bar = Mock()
        close_progress_dialog = Mock()
        window = SimpleNamespace(
            statusBar=lambda: status_bar,
            _close_image_import_progress_dialog=close_progress_dialog,
        )

        with patch("ui.main_window.QMessageBox.critical") as critical:
            MainWindow._on_import_images_failed(window, "database unavailable")

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
            _close_image_import_progress_dialog=close_progress_dialog,
        )
        result = ImportImagesResult(
            imported_stickers=(object(),),
            cancelled=True,
        )

        with patch(
            "ui.main_window.services.sticker_library_viewer_service.wiring.slot_refresh_content",
        ) as refresh_content, patch(
            "ui.main_window.QMessageBox.information"
        ) as information:
            MainWindow._on_import_images_cancelled(window, result)

        close_progress_dialog.assert_called_once_with()
        refresh_content.assert_called_once_with()
        status_bar.showMessage.assert_called_once_with(
            "导入已中止，已导入 1 张图片。",
            8000,
        )
        information.assert_called_once_with(
            window,
            "导入已中止",
            "导入已中止，已导入 1 张图片。",
        )

    def test_cancelled_import_does_not_refresh_when_no_rows_were_added(self):
        window = SimpleNamespace(
            statusBar=Mock(return_value=Mock()),
            _close_image_import_progress_dialog=Mock(),
        )
        result = ImportImagesResult(imported_stickers=(), cancelled=True)

        with patch(
            "ui.main_window.services.sticker_library_viewer_service.wiring.slot_refresh_content",
        ) as refresh_content, patch("ui.main_window.QMessageBox.information"):
            MainWindow._on_import_images_cancelled(window, result)

        refresh_content.assert_not_called()

    def test_start_failure_routes_through_the_normal_failure_handler(self):
        request = ImportImagesRequest(file_paths=("first.png",))
        import_service = Mock()
        import_service.start_import.side_effect = RuntimeError("已有图片导入任务正在进行")
        progress_dialog = Mock()
        failure_handler = Mock()
        window = SimpleNamespace(
            _image_import_service=import_service,
            _image_import_progress_dialog=None,
            _on_import_images_failed=failure_handler,
            statusBar=Mock(return_value=Mock()),
        )

        with patch(
            "ui.main_window.ImageImportProgressDialog",
            return_value=progress_dialog,
        ):
            MainWindow.handle_import_images_request(window, request)

        failure_handler.assert_called_once_with("已有图片导入任务正在进行")
        window.statusBar.return_value.showMessage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
