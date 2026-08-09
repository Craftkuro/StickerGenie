import os
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from blob_storage import BlobFileEntity
from services.thumbnail_provider import ThumbnailProvider
from thumbnail_disk_storage import ThumbnailDiskStorage


class FakeBlobStorage:
    def __init__(self, files: dict[str, str]):
        self.files = files
        self.read_calls = defaultdict(int)

    def read_file(self, entity):
        self.read_calls[entity.hash] += 1
        file_path = self.files.get(entity.hash)
        if file_path is None:
            raise FileNotFoundError(entity.hash)
        return file_path


class NoopDiskStorage:
    def read_file(self, file_hash):
        raise FileNotFoundError(file_hash)

    def save_pixmap(self, pixmap, file_hash):
        pass

    def delete_file(self, file_hash):
        raise FileNotFoundError(file_hash)


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

    def test_wide_image_keeps_aspect_ratio_and_writes_disk_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "wide.jpg", 400, 100)
            blob_storage = FakeBlobStorage({"wide-hash": file_path})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )

            thumbnail = provider.get_thumbnail(
                BlobFileEntity("wide-hash", ".jpg")
            )
            self.assertTrue(disk_storage.exists("wide-hash"))

        self.assertFalse(thumbnail.isNull())
        self.assertEqual((150, 37), (thumbnail.width(), thumbnail.height()))

    def test_tall_image_keeps_aspect_ratio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "tall.jpg", 100, 400)
            provider = ThumbnailProvider(
                blob_storage=FakeBlobStorage({"tall-hash": file_path}),
                disk_storage=NoopDiskStorage(),
            )
            thumbnail = provider.get_thumbnail(
                BlobFileEntity("tall-hash", ".jpg")
            )

        self.assertFalse(thumbnail.isNull())
        self.assertEqual((37, 150), (thumbnail.width(), thumbnail.height()))

    def test_small_image_returns_original_without_disk_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "small.png", 20, 20)
            blob_storage = FakeBlobStorage({"small-hash": file_path})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )

            thumbnail = provider.get_thumbnail(
                BlobFileEntity("small-hash", ".png")
            )
            self.assertFalse(disk_storage.exists("small-hash"))

        self.assertFalse(thumbnail.isNull())
        self.assertEqual((20, 20), (thumbnail.width(), thumbnail.height()))
        self.assertEqual(1, blob_storage.read_calls["small-hash"])

    def test_memory_cache_avoids_repeated_blob_reads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "cached.png", 400, 100)
            blob_storage = FakeBlobStorage({"cached-hash": file_path})
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=NoopDiskStorage(),
            )
            entity = BlobFileEntity("cached-hash", ".png")

            provider.get_thumbnail(entity)
            provider.get_thumbnail(entity)

        self.assertEqual(1, blob_storage.read_calls["cached-hash"])

    def test_lru_evicts_least_recently_used_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            blob_storage = FakeBlobStorage(
                {
                    "a" * 40: self._save_image(root, "a.png", 400, 100),
                    "b" * 40: self._save_image(root, "b.png", 300, 150),
                    "c" * 40: self._save_image(root, "c.png", 200, 100),
                }
            )
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=NoopDiskStorage(),
                max_cache_size=2,
            )

            provider.get_thumbnail(BlobFileEntity("a" * 40, ".png"))
            provider.get_thumbnail(BlobFileEntity("b" * 40, ".png"))
            provider.get_thumbnail(BlobFileEntity("a" * 40, ".png"))
            provider.get_thumbnail(BlobFileEntity("c" * 40, ".png"))
            provider.get_thumbnail(BlobFileEntity("a" * 40, ".png"))
            provider.get_thumbnail(BlobFileEntity("b" * 40, ".png"))

        self.assertEqual(1, blob_storage.read_calls["a" * 40])
        self.assertEqual(2, blob_storage.read_calls["b" * 40])
        self.assertEqual(1, blob_storage.read_calls["c" * 40])
        self.assertEqual({"a" * 40, "b" * 40}, set(provider._memory_cache))

    def test_disk_cache_serves_thumbnail_when_blob_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "source.png", 400, 100)
            blob_storage = FakeBlobStorage({"disk-hash": file_path})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )

            provider.get_thumbnail(BlobFileEntity("disk-hash", ".png"))
            provider.clear_memory_cache()
            blob_storage.files.clear()
            thumbnail = provider.get_thumbnail(
                BlobFileEntity("disk-hash", ".png")
            )

        self.assertFalse(thumbnail.isNull())
        self.assertEqual((150, 37), (thumbnail.width(), thumbnail.height()))

    def test_corrupt_disk_cache_is_rebuilt_from_blob(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "source.png", 400, 100)
            blob_storage = FakeBlobStorage({"corrupt-hash": file_path})
            disk_storage = ThumbnailDiskStorage(root / "thumbnails")
            disk_file = root / "thumbnails" / "co" / "corrupt-hash.png"
            disk_file.parent.mkdir(parents=True)
            disk_file.write_bytes(b"not a png")
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=disk_storage,
            )

            thumbnail = provider.get_thumbnail(
                BlobFileEntity("corrupt-hash", ".png")
            )
            self.assertTrue(disk_file.exists())

        self.assertFalse(thumbnail.isNull())
        self.assertEqual((150, 37), (thumbnail.width(), thumbnail.height()))
        self.assertEqual(1, blob_storage.read_calls["corrupt-hash"])

    def test_clear_memory_cache_forces_reload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = self._save_image(root, "cleared.png", 400, 100)
            blob_storage = FakeBlobStorage({"cleared-hash": file_path})
            provider = ThumbnailProvider(
                blob_storage=blob_storage,
                disk_storage=NoopDiskStorage(),
            )
            entity = BlobFileEntity("cleared-hash", ".png")

            provider.get_thumbnail(entity)
            provider.clear_memory_cache()
            provider.get_thumbnail(entity)

        self.assertEqual(2, blob_storage.read_calls["cleared-hash"])

    def test_missing_file_returns_null_pixmap(self):
        provider = ThumbnailProvider(
            blob_storage=FakeBlobStorage({}),
            disk_storage=NoopDiskStorage(),
        )
        thumbnail = provider.get_thumbnail(
            BlobFileEntity("missing-hash", ".png")
        )

        self.assertTrue(thumbnail.isNull())


if __name__ == "__main__":
    unittest.main()
