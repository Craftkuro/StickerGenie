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

    def test_gif_exposes_animation_formats_and_static_bitmap_fallback(self):
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
            self.assertEqual(source.read_bytes(), bytes(mime_data.data("image/gif")))
            self.assertEqual(
                [QUrl.fromLocalFile(str(staged_path.resolve()))],
                mime_data.urls(),
            )
            self.assertTrue(mime_data.hasHtml())
            self.assertIn(
                bytes(QUrl.fromLocalFile(str(staged_path.resolve())).toEncoded()).decode(
                    "ascii"
                ),
                mime_data.html(),
            )
            self.assertIn("application/x-qt-image", mime_data.formats())
            self.assertTrue(mime_data.hasImage())

    def test_gif_static_bitmap_fallback_can_be_disabled(self):
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

            mime_data, _ = create_image_mime_data(
                source,
                "动态表情.gif",
                include_static_gif_fallback=False,
                staging_root=root / "clipboard",
            )

            self.assertNotIn("application/x-qt-image", mime_data.formats())

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
