import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from blob_storage import BlobFileEntity, BlobStorage


class BlobStorageTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.storage = BlobStorage(str(self.root / "blob"))

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_store_file_uses_extension_override(self):
        source_path = self.root / "image.jpg"
        Image.new("RGB", (4, 3), "red").save(source_path, format="PNG")
        file_hash = hashlib.sha1(source_path.read_bytes()).hexdigest()

        entity = self.storage.store_file(
            str(source_path),
            file_hash,
            extension_override=".PNG",
        )

        self.assertEqual(BlobFileEntity(file_hash, ".png"), entity)
        self.assertTrue(self.storage.exists(entity))
        self.assertFalse(
            (self.storage.base_path / file_hash[:2] / f"{file_hash}.jpg").exists()
        )

    def test_store_file_rejects_unsafe_extension_override(self):
        source_path = self.root / "image.png"
        source_path.write_bytes(b"image")

        with self.assertRaises(ValueError):
            self.storage.store_file(
                str(source_path),
                extension_override="../png",
            )
