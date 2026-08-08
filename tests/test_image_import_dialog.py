import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QDialog

import apppath
from ui.dialog_image_import import ImageImportDialog


class FakeDatabase:
    def __init__(self, hashes=()):
        self._stickers = [SimpleNamespace(hash=value) for value in hashes]

    def list_stickers(self, *, count=None):
        return list(self._stickers)


class ImageImportDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        source_dir = Path(__file__).resolve().parents[1] / "src"
        apppath.setup_data_path(source_dir)

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self._temp_dir.name)
        self.import_service = Mock(return_value=[object()])
        self.dialog = ImageImportDialog(
            database=FakeDatabase(),
            import_service=self.import_service,
        )

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

    def test_recursively_adds_supported_images_without_duplicate_paths(self):
        first = self._make_image("first.png")
        second = self._make_image("nested/second.webp")
        (self.temp_path / "not-an-image.txt").write_text("text", encoding="utf-8")

        with patch(
            "ui.dialog_image_import.QFileDialog.getExistingDirectory",
            return_value=str(self.temp_path),
        ):
            self.dialog._add_directory()

        self.dialog._add_paths([first])
        self.assertEqual(
            {str(first.resolve()), str(second.resolve())},
            set(self.dialog.selected_file_paths),
        )

    def test_excludes_duplicate_content_and_existing_database_images(self):
        first = self._make_image("first.png", 0xFFFFFFFF)
        first_copy = self.temp_path / "first-copy.png"
        shutil.copyfile(first, first_copy)
        existing = self._make_image("existing.png", 0xFF000000)
        existing_hash = hashlib.sha1(existing.read_bytes()).hexdigest()

        self.dialog.close()
        self.dialog = ImageImportDialog(
            database=FakeDatabase([existing_hash]),
            import_service=self.import_service,
        )
        self.dialog._add_paths([first, first_copy, existing])
        self.dialog._show_confirmation_page()

        self.assertEqual([str(first.resolve())], self.dialog.prepared_file_paths)
        self.assertIn("已排除 2 个重复文件", self.dialog.labelNonDuplicateFilesCount.text())

        self.dialog._start_import()

        self.import_service.assert_called_once_with([str(first.resolve())])
        self.assertEqual(QDialog.DialogCode.Accepted, self.dialog.result())
        self.assertEqual(1, len(self.dialog.imported_stickers))


if __name__ == "__main__":
    unittest.main()
