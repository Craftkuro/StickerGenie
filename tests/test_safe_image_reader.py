import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image

from utils.safe_image_reader import (
    SafeImageReadError,
    SafeImageReadResult,
    detect_image_format,
    generate_thumbnail_safe,
    open_image_safe,
    pil_to_qimage,
)


class DetectImageFormatTests(unittest.TestCase):
    def test_detects_common_formats_from_magic_bytes(self):
        cases = {
            b"\x89PNG\r\n\x1a\nrest": "PNG",
            b"\xff\xd8\xff\xe0rest": "JPEG",
            b"GIF89a...": "GIF",
            b"BM....": "BMP",
            b"II*\x00....": "TIFF",
            b"MM\x00*....": "TIFF",
            b"RIFF\x00\x00\x00\x00WEBPVP8 ": "WEBP",
        }
        for data, expected in cases.items():
            with self.subTest(header=data[:8]):
                self.assertEqual(expected, detect_image_format(data))

    def test_returns_none_for_unknown_data(self):
        self.assertIsNone(detect_image_format(b"not an image"))


class OpenImageSafeTests(unittest.TestCase):
    def _save_image(self, directory, name, size, mode="RGB", color=(255, 0, 0)):
        image = Image.new(mode, size, color)
        path = Path(directory) / name
        image.save(path)
        return path

    def test_open_normal_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._save_image(Path(temp_dir), "normal.png", (40, 30))

            result = open_image_safe(path)

            self.assertIsInstance(result, SafeImageReadResult)
            self.assertFalse(result.used_fallback)
            self.assertEqual("PNG", result.format_name)
            self.assertEqual((40, 30), result.image.size)
            self.assertEqual((), result.warnings)

    def test_format_detected_from_content_not_extension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            # PNG 内容但扩展名是 .jpg：PIL 按文件内容识别为 PNG
            path = Path(temp_dir) / "actually_png.jpg"
            Image.new("RGB", (40, 30), (255, 0, 0)).save(path, format="PNG")

            result = open_image_safe(path)

            self.assertEqual("PNG", result.format_name)
            self.assertFalse(result.used_fallback)

    def test_fallback_used_when_normal_read_fails(self):
        import utils.safe_image_reader as safe_image_reader

        real_open = safe_image_reader.Image.open

        def fake_open(source, *args, **kwargs):
            if isinstance(source, (str, Path)):
                raise OSError("simulated corrupt file header")
            return real_open(source, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._save_image(Path(temp_dir), "fallback.png", (40, 30))
            with patch.object(safe_image_reader.Image, "open", fake_open):
                result = open_image_safe(path)

        self.assertTrue(result.used_fallback)
        self.assertEqual("PNG", result.format_name)
        self.assertEqual((40, 30), result.image.size)
        self.assertEqual("RGB", result.image.mode)
        self.assertTrue(any("PNG" in warning for warning in result.warnings))

    def test_raises_when_all_strategies_fail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "not_image.txt"
            path.write_text("hello", encoding="utf-8")

            with self.assertRaises(SafeImageReadError) as context:
                open_image_safe(path)

            self.assertIn(str(path), str(context.exception))


class GenerateThumbnailSafeTests(unittest.TestCase):
    def test_resizes_longest_side(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Image.new("RGB", (400, 300), (10, 20, 30))
            path = Path(temp_dir) / "wide.jpg"
            image.save(path, quality=90)

            result = generate_thumbnail_safe(path, 200)

            self.assertEqual((200, 150), result.image.size)
            self.assertEqual("RGB", result.image.mode)

    def test_keeps_alpha_channel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Image.new("RGBA", (100, 80), (10, 20, 30, 128))
            path = Path(temp_dir) / "alpha.png"
            image.save(path)

            result = generate_thumbnail_safe(path, 200)

            self.assertEqual((100, 80), result.image.size)
            self.assertEqual("RGBA", result.image.mode)

    def test_small_image_keeps_original_size(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Image.new("RGB", (50, 40))
            path = Path(temp_dir) / "small.png"
            image.save(path)

            result = generate_thumbnail_safe(path, 200)

            self.assertEqual((50, 40), result.image.size)


class PilToQImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_converts_rgb_and_rgba(self):
        for mode in ("RGB", "RGBA"):
            with self.subTest(mode=mode):
                image = Image.new(mode, (30, 20), (1, 2, 3, 128))

                qimage = pil_to_qimage(image)

                self.assertFalse(qimage.isNull())
                self.assertEqual((30, 20), (qimage.width(), qimage.height()))


if __name__ == "__main__":
    unittest.main()