import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from commons.signal_objects import ImportImagesRequest
from ui.main_window import MainWindow


class MainWindowImageImportTests(unittest.TestCase):
    def test_refreshes_library_after_import_service_completes(self):
        events = []
        request = ImportImagesRequest(
            file_paths=("first.png", "second.png"),
            generate_vectors=True,
        )

        def import_images(file_paths):
            events.append(("imported", file_paths))
            return [object()]

        def refresh_content():
            events.append(("refreshed", None))

        with patch(
            "ui.main_window.services.import_images.import_images",
            side_effect=import_images,
        ), patch(
            "ui.main_window.services.sticker_library_viewer_service.wiring.slot_refresh_content",
            side_effect=refresh_content,
        ):
            MainWindow.handle_import_images_request(None, request)

        self.assertEqual(
            [
                ("imported", ["first.png", "second.png"]),
                ("refreshed", None),
            ],
            events,
        )

    def test_does_not_refresh_when_nothing_was_imported(self):
        request = ImportImagesRequest(file_paths=("duplicate.png",))

        with patch(
            "ui.main_window.services.import_images.import_images",
            return_value=[],
        ), patch(
            "ui.main_window.services.sticker_library_viewer_service.wiring.slot_refresh_content"
        ) as refresh_content:
            MainWindow.handle_import_images_request(None, request)

        refresh_content.assert_not_called()


if __name__ == "__main__":
    unittest.main()
