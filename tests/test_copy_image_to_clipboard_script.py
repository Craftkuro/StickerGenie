import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtGui import QImage

from experiments.copy_image_to_clipboard import build_compound_mime_data, parse_arguments


class CopyImageToClipboardScriptTests(unittest.TestCase):
    def test_static_image_contains_file_raw_bytes_and_bitmap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.png"
            image = QImage(2, 2, QImage.Format.Format_ARGB32)
            image.fill(0xFF22AA44)
            self.assertTrue(image.save(str(path)))

            mime_data, mime_type, is_gif = build_compound_mime_data(path)

            self.assertEqual("image/png", mime_type)
            self.assertFalse(is_gif)
            self.assertEqual(path.read_bytes(), bytes(mime_data.data("image/png")))
            self.assertEqual(path.resolve(), Path(mime_data.urls()[0].toLocalFile()))
            self.assertTrue(mime_data.hasImage())

    def test_gif_exposes_html_fragment_with_local_file_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.gif"
            first_frame = Image.new("RGBA", (2, 2), "red")
            second_frame = Image.new("RGBA", (2, 2), "blue")
            first_frame.save(
                path,
                save_all=True,
                append_images=[second_frame],
                duration=100,
                loop=0,
            )

            mime_data, mime_type, is_gif = build_compound_mime_data(path)

            self.assertEqual("image/gif", mime_type)
            self.assertTrue(is_gif)
            self.assertTrue(mime_data.hasHtml())
            self.assertFalse(mime_data.hasImage())
            self.assertNotIn("image/gif", mime_data.formats())
            html_text = mime_data.html()
            self.assertIn("<!--StartFragment-->", html_text)
            self.assertIn("<!--EndFragment-->", html_text)
            self.assertIn('<meta charset="utf-8">', html_text)
            self.assertIn(f"file:///{path.resolve().as_posix()}", html_text)

    def test_command_line_option(self):
        options = parse_arguments(["sample.gif"])

        self.assertEqual(Path("sample.gif"), options.image_path)


if __name__ == "__main__":
    unittest.main()
