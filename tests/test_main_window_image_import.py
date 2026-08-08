import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from commons.signal_objects import ImportImagesRequest
from services.import_images import ImportImagesResult
from ui.main_window import MainWindow


class MainWindowImageImportTests(unittest.TestCase):
    def test_passes_the_complete_request_to_the_background_service(self):
        request = ImportImagesRequest(
            file_paths=("first.png", "second.png"),
            generate_vectors=True,
        )
        import_service = Mock()
        status_bar = Mock()
        window = SimpleNamespace(
            _image_import_service=import_service,
            statusBar=lambda: status_bar,
        )

        MainWindow.handle_import_images_request(window, request)

        import_service.start_import.assert_called_once_with(request)
        status_bar.showMessage.assert_called_once_with("正在导入图片…")

    def test_refreshes_library_after_import_service_completes(self):
        status_bar = Mock()
        window = SimpleNamespace(statusBar=lambda: status_bar)
        result = ImportImagesResult(
            imported_stickers=(object(),),
            vectorized_count=1,
        )

        with patch(
            "ui.main_window.services.sticker_library_viewer_service.wiring.slot_refresh_content",
        ) as refresh_content:
            MainWindow._on_import_images_finished(window, result)

        refresh_content.assert_called_once_with()
        status_bar.showMessage.assert_called_once_with(
            "已导入 1 张图片，生成 1 个向量",
            8000,
        )

    def test_does_not_refresh_when_nothing_was_imported(self):
        status_bar = Mock()
        window = SimpleNamespace(statusBar=lambda: status_bar)
        result = ImportImagesResult(imported_stickers=())

        with patch(
            "ui.main_window.services.sticker_library_viewer_service.wiring.slot_refresh_content"
        ) as refresh_content:
            MainWindow._on_import_images_finished(window, result)

        refresh_content.assert_not_called()


if __name__ == "__main__":
    unittest.main()
