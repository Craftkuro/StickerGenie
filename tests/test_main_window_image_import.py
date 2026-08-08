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


if __name__ == "__main__":
    unittest.main()
