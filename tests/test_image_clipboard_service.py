import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QImage

from services.image_clipboard_service import (
    STAGING_TTL_SECONDS,
    create_image_mime_data,
)


class ImageClipboardServiceTests(unittest.TestCase):
    def test_static_image_exposes_file_raw_bytes_and_bitmap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "stored-hash.png"
            image = QImage(2, 2, QImage.Format.Format_ARGB32)
            image.fill(0xFF11AA22)
            self.assertTrue(image.save(str(source)))

            mime_data, staged_path = create_image_mime_data(
                source,
                "原始名称.png",
                staging_root=root / "clipboard",
            )

            self.assertEqual("原始名称.png", staged_path.name)
            self.assertEqual(source.read_bytes(), staged_path.read_bytes())
            self.assertEqual(
                str(staged_path.resolve()),
                Path(mime_data.urls()[0].toLocalFile()).resolve().as_posix()
                if os.name != "nt"
                else str(Path(mime_data.urls()[0].toLocalFile()).resolve()),
            )
            self.assertEqual(source.read_bytes(), bytes(mime_data.data("image/png")))
            self.assertTrue(mime_data.hasImage())
            self.assertFalse(mime_data.hasHtml())

    def test_gif_exposes_html_fragment_with_local_file_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "stored-hash.gif"
            first_frame = Image.new("RGBA", (2, 2), "red")
            second_frame = Image.new("RGBA", (2, 2), "blue")
            first_frame.save(
                source,
                save_all=True,
                append_images=[second_frame],
                duration=100,
                loop=0,
            )

            mime_data, staged_path = create_image_mime_data(
                source,
                "动态表情.gif",
                staging_root=root / "clipboard",
            )

            self.assertEqual("动态表情.gif", staged_path.name)
            self.assertEqual(source.read_bytes(), staged_path.read_bytes())
            self.assertTrue(mime_data.hasHtml())
            self.assertFalse(mime_data.hasUrls())
            self.assertFalse(mime_data.hasImage())
            self.assertNotIn("image/gif", mime_data.formats())
            html_text = mime_data.html()
            self.assertIn("<!--StartFragment-->", html_text)
            self.assertIn("<!--EndFragment-->", html_text)
            self.assertIn('<meta charset="utf-8">', html_text)
            self.assertIn(
                f"file:///{staged_path.resolve().as_posix()}",
                html_text,
            )

    def test_gif_first_frame_reuses_static_image_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "stored-hash.gif"
            first_frame = Image.new("RGBA", (2, 2), "red")
            second_frame = Image.new("RGBA", (2, 2), "blue")
            first_frame.save(
                source,
                save_all=True,
                append_images=[second_frame],
                duration=100,
                loop=0,
            )

            mime_data, staged_path = create_image_mime_data(
                source,
                "动态表情.gif",
                staging_root=root / "clipboard",
                anim_as_static_image=True,
            )

            self.assertEqual("动态表情.gif", staged_path.name)
            self.assertEqual(source.read_bytes(), staged_path.read_bytes())
            self.assertTrue(mime_data.hasImage())
            self.assertTrue(mime_data.hasUrls())
            self.assertFalse(mime_data.hasHtml())
            self.assertIn("image/gif", mime_data.formats())
            self.assertEqual(
                source.read_bytes(),
                bytes(mime_data.data("image/gif")),
            )
            first_frame_image = QImage.fromData(
                bytes(mime_data.data("image/gif"))
            )
            self.assertEqual("#ff0000", first_frame_image.pixelColor(0, 0).name())

    def test_copy_cleans_only_expired_staging_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "stored-hash.png"
            image = QImage(1, 1, QImage.Format.Format_ARGB32)
            image.fill(0xFFFFFFFF)
            self.assertTrue(image.save(str(source)))

            staging_root = root / "clipboard"
            old_directory = staging_root / "old"
            recent_directory = staging_root / "recent"
            old_directory.mkdir(parents=True)
            recent_directory.mkdir()
            old_timestamp = time.time() - STAGING_TTL_SECONDS - 10
            os.utime(old_directory, (old_timestamp, old_timestamp))

            create_image_mime_data(
                source,
                "image.png",
                staging_root=staging_root,
            )

            self.assertFalse(old_directory.exists())
            self.assertTrue(recent_directory.exists())


if __name__ == "__main__":
    unittest.main()
