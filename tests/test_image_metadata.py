import tempfile
import unittest
from pathlib import Path

from PIL import Image

from utils.image_metadata import get_image_metadata


class ImageMetadataTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_extension_is_detected_from_image_content(self):
        image_path = self.root / "image.jpg"
        Image.new("RGB", (4, 3), "red").save(image_path, format="PNG")

        metadata = get_image_metadata(image_path)

        self.assertEqual("image.jpg", metadata.original_file_name)
        self.assertEqual(".png", metadata.extension)
        self.assertEqual((4, 3), (metadata.size_width, metadata.size_height))
