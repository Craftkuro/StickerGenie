import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from blob_storage import BlobFileEntity
from services.thumbnail_provider import ThumbnailProvider


class FakeBlobStorage:
    def __init__(self, file_path: Path):
        self.file_path = file_path

    def read_file(self, _entity):
        return str(self.file_path)


class ThumbnailProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _save_image(self, directory: Path, name: str, width: int, height: int) -> str:
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(0xFFFF0000)
        path = directory / name
        self.assertTrue(image.save(str(path)))
        return str(path)

    def test_wide_image_keeps_aspect_ratio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = self._save_image(Path(temp_dir), "wide.jpg", 400, 100)
            provider = ThumbnailProvider(
                blob_storage=FakeBlobStorage(Path(file_path))
            )
            thumbnail = provider.get_thumbnail(
                BlobFileEntity("wide-hash", ".jpg")
            )

        self.assertFalse(thumbnail.isNull())
        self.assertEqual((144, 36), (thumbnail.width(), thumbnail.height()))

    def test_tall_image_keeps_aspect_ratio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = self._save_image(Path(temp_dir), "tall.jpg", 100, 400)
            provider = ThumbnailProvider(
                blob_storage=FakeBlobStorage(Path(file_path))
            )
            thumbnail = provider.get_thumbnail(
                BlobFileEntity("tall-hash", ".jpg")
            )

        self.assertFalse(thumbnail.isNull())
        self.assertEqual((36, 144), (thumbnail.width(), thumbnail.height()))

    def test_small_image_is_scaled_up_to_longest_edge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = self._save_image(Path(temp_dir), "small.png", 20, 20)
            provider = ThumbnailProvider(
                blob_storage=FakeBlobStorage(Path(file_path))
            )
            thumbnail = provider.get_thumbnail(
                BlobFileEntity("small-hash", ".png")
            )

        self.assertFalse(thumbnail.isNull())
        self.assertEqual((144, 144), (thumbnail.width(), thumbnail.height()))

    def test_missing_file_returns_null_pixmap(self):
        provider = ThumbnailProvider(
            blob_storage=FakeBlobStorage(Path("missing-file.png"))
        )
        thumbnail = provider.get_thumbnail(
            BlobFileEntity("missing-hash", ".png")
        )

        self.assertTrue(thumbnail.isNull())


if __name__ == "__main__":
    unittest.main()
