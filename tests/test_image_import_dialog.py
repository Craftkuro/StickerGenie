import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QMimeData, QPoint, QPointF, QUrl, Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QImage
from PyQt6.QtWidgets import QApplication, QDialog

import apppath
from commons.signal_objects import ImportImagesRequest
from ui.dialog_image_import import (
    DIRECTORY_SOURCE,
    FILE_PATH_ROLE,
    FILE_SOURCE,
    SOURCE_TYPE_ROLE,
    ImageImportDialog,
)


class ImageImportDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        source_dir = Path(__file__).resolve().parents[1] / "src"
        apppath.setup_data_path(source_dir)

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self._temp_dir.name)
        self.dialog = ImageImportDialog()

    def tearDown(self):
        self.dialog.close()
        self._temp_dir.cleanup()

    def _make_image(self, relative_path: str, color: int = 0xFFFFFFFF) -> Path:
        path = self.temp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(2, 2, QImage.Format.Format_ARGB32)
        image.fill(color)
        self.assertTrue(image.save(str(path)))
        return path

    def _drop_mime_data(self, mime_data: QMimeData) -> None:
        viewport = self.dialog.listWidget.viewport()
        drop_action = Qt.DropAction.CopyAction
        buttons = Qt.MouseButton.LeftButton
        modifiers = Qt.KeyboardModifier.NoModifier
        QApplication.sendEvent(
            viewport,
            QDragEnterEvent(
                QPoint(2, 2),
                drop_action,
                mime_data,
                buttons,
                modifiers,
            ),
        )
        QApplication.sendEvent(
            viewport,
            QDropEvent(
                QPointF(2, 2),
                drop_action,
                mime_data,
                buttons,
                modifiers,
            ),
        )

    def _drop_paths(self, *paths: str | Path) -> None:
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        self._drop_mime_data(mime_data)

    def _item_for_path(self, path: str | Path):
        target = self.dialog._normalize_path(path)
        for row in range(self.dialog.listWidget.count()):
            item = self.dialog.listWidget.item(row)
            if item.data(FILE_PATH_ROLE) == target:
                return item
        self.fail(f"missing item for {path}")

    def test_switches_primary_buttons_between_pages(self):
        image_path = self._make_image("one.png")

        self.assertTrue(self.dialog.pushButtonPrev.isHidden())
        self.assertTrue(self.dialog.pushButtonOk.isHidden())
        self.assertFalse(self.dialog.pushButtonNext.isEnabled())

        self.dialog._add_paths([image_path])
        self.assertTrue(self.dialog.pushButtonNext.isEnabled())
        self.dialog.show()
        self.app.processEvents()
        next_button_geometry = self.dialog.pushButtonNext.geometry()
        self.dialog._show_confirmation_page()
        self.app.processEvents()

        self.assertTrue(self.dialog.pushButtonNext.isHidden())
        self.assertFalse(self.dialog.pushButtonPrev.isHidden())
        self.assertFalse(self.dialog.pushButtonOk.isHidden())
        self.assertTrue(self.dialog.pushButtonOk.isEnabled())
        self.assertEqual(next_button_geometry, self.dialog.pushButtonOk.geometry())

    def test_adds_directory_without_scanning_until_confirmation(self):
        first = self._make_image("first.png")
        second = self._make_image("nested/second.webp")
        (self.temp_path / "not-an-image.txt").write_text("text", encoding="utf-8")

        with patch(
            "ui.dialog_image_import.QFileDialog.getExistingDirectory",
            return_value=str(self.temp_path),
        ):
            self.dialog._add_directory()

        self.assertEqual(
            [str(self.temp_path.resolve())],
            self.dialog.selected_file_paths,
        )
        self.assertEqual([], self.dialog.prepared_file_paths)
        self.assertEqual(1, self.dialog.listWidget.count())

        self.dialog._show_confirmation_page()

        self.assertEqual(
            {str(first.resolve()), str(second.resolve())},
            set(self.dialog.prepared_file_paths),
        )
        self.assertEqual(
            "\n".join(self.dialog.prepared_file_paths),
            self.dialog.textEditNonDuplicateFiles.toPlainText(),
        )

    def test_deduplicates_files_from_overlapping_sources_on_confirmation(self):
        first = self._make_image("first.png")
        second = self._make_image("nested/second.webp")

        with patch(
            "ui.dialog_image_import.QFileDialog.getExistingDirectory",
            side_effect=[str(self.temp_path), str(self.temp_path / "nested")],
        ):
            self.dialog._add_directory()
            self.dialog._add_directory()

        self.dialog._add_paths([first])
        self.dialog._show_confirmation_page()

        self.assertEqual(
            {str(first.resolve()), str(second.resolve())},
            set(self.dialog.prepared_file_paths),
        )
        self.assertEqual(2, len(self.dialog.prepared_file_paths))

    def test_only_deduplicates_normalized_absolute_paths(self):
        first = self.temp_path / "first.png"
        second = self.temp_path / "same-content.png"
        first.write_bytes(b"same image content")
        second.write_bytes(b"same image content")

        self.dialog._add_paths([first, first.resolve(), second])
        self.dialog._show_confirmation_page()

        self.assertEqual(
            [str(first.resolve()), str(second.resolve())],
            self.dialog.prepared_file_paths,
        )
        self.assertEqual(2, self.dialog.listWidget.count())

    def test_drops_mixed_files_and_directories_into_list(self):
        image_path = self._make_image("dropped.png")
        nested_image = self._make_image("dropped_dir/nested.jpg")
        image_dir = self.temp_path / "dropped_dir"

        self._drop_paths(image_path, image_dir)

        self.assertEqual(
            {str(image_path.resolve()), str(image_dir.resolve())},
            set(self.dialog.selected_file_paths),
        )
        self.assertEqual(
            FILE_SOURCE,
            self._item_for_path(image_path).data(SOURCE_TYPE_ROLE),
        )
        self.assertEqual(
            DIRECTORY_SOURCE,
            self._item_for_path(image_dir).data(SOURCE_TYPE_ROLE),
        )

        self.dialog._show_confirmation_page()

        self.assertEqual(
            {str(image_path.resolve()), str(nested_image.resolve())},
            set(self.dialog.prepared_file_paths),
        )

    def test_drop_deduplicates_against_existing_list_entries(self):
        image_path = self._make_image("drop-duplicate.png")
        self.dialog._add_paths([image_path])

        self._drop_paths(image_path)

        self.assertEqual(1, self.dialog.listWidget.count())

    def test_drop_ignores_obviously_invalid_urls(self):
        image_path = self._make_image("local-only.png")
        mime_data = QMimeData()
        mime_data.setUrls(
            [
                QUrl.fromLocalFile(str(image_path)),
                QUrl("https://example.com/remote.png"),
                QUrl("file:relative/path.png"),
            ]
        )

        self._drop_mime_data(mime_data)

        self.assertEqual(
            [str(image_path.resolve())],
            self.dialog.selected_file_paths,
        )

    def test_accepts_and_sends_import_request(self):
        image_path = self._make_image("one.png")
        requests: list[ImportImagesRequest] = []
        self.dialog.signal_import_requested.connect(
            requests.append,
            type=Qt.ConnectionType.QueuedConnection,
        )
        self.dialog.checkBoxDoVectorGeneration.setChecked(True)
        self.dialog._add_paths([image_path])
        self.dialog._show_confirmation_page()

        self.dialog._send_import_request()

        self.assertEqual([], requests)
        self.app.processEvents()
        self.assertEqual(1, len(requests))
        self.assertEqual((str(image_path.resolve()),), requests[0].file_paths)
        self.assertTrue(requests[0].generate_vectors)
        self.assertTrue(requests[0].extract_text)
        self.assertEqual(QDialog.DialogCode.Accepted, self.dialog.result())


if __name__ == "__main__":
    unittest.main()
