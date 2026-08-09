import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication

from thumbnail_disk_storage import ThumbnailDiskStorage


class ThumbnailDiskStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_pixmap(self, width: int = 150, height: int = 75) -> QPixmap:
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(0xFFFF0000)
        return QPixmap.fromImage(image)

    def test_save_read_exists_and_delete_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ThumbnailDiskStorage(Path(temp_dir) / "thumbnails")
            file_hash = "a" * 40

            self.assertFalse(storage.exists(file_hash))
            storage.save_pixmap(self._make_pixmap(), file_hash)
            self.assertTrue(storage.exists(file_hash))

            file_path = storage.read_file(file_hash)
            self.assertTrue(Path(file_path).exists())
            self.assertTrue(file_path.endswith(f"{file_hash}.png"))

            storage.delete_file(file_hash)
            self.assertFalse(storage.exists(file_hash))
            with self.assertRaises(FileNotFoundError):
                storage.read_file(file_hash)

    def test_files_are_bucketed_by_hash_prefix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ThumbnailDiskStorage(Path(temp_dir) / "thumbnails")
            storage.save_pixmap(self._make_pixmap(), "ab" + "0" * 38)
            storage.save_pixmap(self._make_pixmap(), "cd" + "1" * 38)

            subdirs = sorted(child.name for child in storage.base_path.iterdir())
            self.assertEqual(["ab", "cd"], subdirs)

    def test_delete_all_removes_everything_under_base(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ThumbnailDiskStorage(Path(temp_dir) / "thumbnails")
            storage.save_pixmap(self._make_pixmap(), "ab" + "0" * 38)
            storage.save_pixmap(self._make_pixmap(), "cd" + "1" * 38)
            nested = storage.base_path / "xy" / "nested"
            nested.mkdir(parents=True)
            (nested / "extra.bin").write_bytes(b"data")
            (storage.base_path / "notes.txt").write_text("keep", encoding="utf-8")

            deleted_count, errors = storage.delete_all()

            self.assertEqual(4, deleted_count)
            self.assertEqual((), errors)
            self.assertTrue(storage.base_path.exists())
            self.assertEqual([], list(storage.base_path.iterdir()))

    def test_delete_all_on_missing_base_returns_zero(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ThumbnailDiskStorage(Path(temp_dir) / "thumbnails")
            storage.delete_all()
            self.assertTrue(storage.base_path.exists())

            deleted_count, errors = storage.delete_all()

            self.assertEqual(0, deleted_count)
            self.assertEqual((), errors)


if __name__ == "__main__":
    unittest.main()
