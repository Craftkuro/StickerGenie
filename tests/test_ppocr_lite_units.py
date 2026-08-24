# coding=utf-8
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from ppocr_lite.image_io import blend_la_to_bgr, blend_rgba_to_bgr, convert_to_bgr, load_bgr
from ppocr_lite.preprocess import (
    build_det_input,
    limit_image_size,
    resize_norm_rec,
    vertical_padding,
)
from ppocr_lite.recognition import ctc_decode


class LoadBgrTests(unittest.TestCase):
    def _save(self, img: Image.Image) -> str:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(tmp.name)
        return tmp.name

    def test_missing_path_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            load_bgr("definitely/not/exist.png")

    def test_rgb_png_reversed_to_bgr(self):
        rgb = np.zeros((10, 10, 3), dtype=np.uint8)
        rgb[...] = (1, 2, 3)
        path = self._save(Image.fromarray(rgb))
        bgr = load_bgr(path)
        self.assertEqual((1, 2, 3), tuple(reversed(tuple(bgr[0, 0]))))
        Path(path).unlink()

    def test_gray_png_expands_to_three_equal_channels(self):
        gray = np.full((8, 8), 77, dtype=np.uint8)
        path = self._save(Image.fromarray(gray, mode="L"))
        bgr = load_bgr(path)
        self.assertEqual((8, 8, 3), bgr.shape)
        np.testing.assert_array_equal(77, bgr[..., 0])
        np.testing.assert_array_equal(bgr[..., 0], bgr[..., 2])
        Path(path).unlink()


class ConvertToBgrTests(unittest.TestCase):
    @staticmethod
    def _la(gray_value, alpha_value):
        return np.array([[[gray_value, alpha_value]]], dtype=np.uint8)

    def test_two_channel_la_blend(self):
        result = blend_la_to_bgr(self._la(100, 255))
        self.assertEqual(100, result[0, 0, 0])

        result = blend_la_to_bgr(self._la(100, 0))
        self.assertEqual(255, result[0, 0, 0])

        # 半透明：gray + (255 - alpha)，cv2.add 饱和语义
        result = blend_la_to_bgr(self._la(200, 2))
        self.assertEqual(255, result[0, 0, 0])  # 200 + 253 饱和截断

    def test_two_channel_full_pipeline(self):
        la = np.zeros((4, 4, 2), dtype=np.uint8)
        la[..., 0] = 60
        la[..., 1] = 255
        bgr = convert_to_bgr(la)
        self.assertEqual(60, int(bgr[0, 0, 0]))

    def test_rgba_dark_content_blends_on_white(self):
        rgba = np.zeros((6, 6, 4), dtype=np.uint8)
        rgba[..., :3] = (20, 20, 20)
        rgba[..., 3] = 255
        bgr = blend_rgba_to_bgr(rgba)
        np.testing.assert_array_equal(20, bgr[..., 2])

    def test_rgba_light_content_blends_on_black(self):
        rgba = np.zeros((6, 6, 4), dtype=np.uint8)
        rgba[..., :3] = (240, 240, 240)
        rgba[..., 3] = 128
        bgr = blend_rgba_to_bgr(rgba)
        expected = round(240 * 128 / 255.0 + 0 * (127 / 255.0))
        self.assertEqual(expected, int(bgr[0, 0, 2]))

    def test_fully_transparent_uses_white_background(self):
        rgba = np.zeros((4, 4, 4), dtype=np.uint8)
        bgr = blend_rgba_to_bgr(rgba)
        np.testing.assert_array_equal(255, bgr[..., 0])


class LimitImageSizeTests(unittest.TestCase):
    def test_compliant_image_passes_through_without_copy(self):
        img = np.zeros((64, 320, 3), dtype=np.uint8)
        self.assertIs(img, limit_image_size(img))

    def test_large_image_scaled_down_to_multiple_of_32(self):
        img = np.zeros((3000, 600, 3), dtype=np.uint8)
        out = limit_image_size(img)
        self.assertLessEqual(max(out.shape[:2]), 2000)
        self.assertEqual(0, out.shape[0] % 32)
        self.assertEqual(0, out.shape[1] % 32)
        self.assertAlmostEqual(5.0, out.shape[0] / out.shape[1], delta=0.2)

    def test_tiny_image_scaled_up(self):
        img = np.zeros((10, 400, 3), dtype=np.uint8)
        out = limit_image_size(img)
        self.assertGreaterEqual(min(out.shape[:2]), 30)


class VerticalPaddingTests(unittest.TestCase):
    def test_thin_banner_gets_black_padding(self):
        img = np.full((16, 400, 3), 9, dtype=np.uint8)
        out = vertical_padding(img)
        new_h = max(int(400 / 8), 30) * 2
        padding_h = int(abs(new_h - 16) / 2)
        self.assertEqual(padding_h * 2 + 16, out.shape[0])
        np.testing.assert_array_equal(0, out[0, :, :])
        np.testing.assert_array_equal(9, out[padding_h])

    def test_normal_image_untouched(self):
        img = np.zeros((100, 300, 3), dtype=np.uint8)
        self.assertIs(img, vertical_padding(img))

    def test_extreme_ratio_triggers_padding_even_if_tall_enough(self):
        img = np.zeros((40, 500, 3), dtype=np.uint8)
        out = vertical_padding(img)
        self.assertGreater(out.shape[0], 40)


class BuildDetInputTests(unittest.TestCase):
    def test_small_image_upscaled_to_736_min_side(self):
        img = np.zeros((50, 300, 3), dtype=np.uint8)
        tensor = build_det_input(img)
        self.assertEqual((1, 3, 736, tensor.shape[3]), tensor.shape)
        self.assertEqual(np.float32, tensor.dtype)
        self.assertEqual(0, tensor.shape[3] % 32)

    def test_large_image_keeps_size_when_min_side_ge_limit(self):
        img = np.zeros((800, 1200, 3), dtype=np.uint8)
        tensor = build_det_input(img)
        self.assertEqual((1, 3, 800, 1216), tensor.shape)

    def test_normalized_values_within_minus_one_one(self):
        img = np.full((64, 64, 3), 255, dtype=np.uint8)
        tensor = build_det_input(img)
        self.assertGreaterEqual(float(tensor.min()), -1.01)
        self.assertLessEqual(float(tensor.max()), 1.01)
        self.assertAlmostEqual(1.0, float(tensor.mean()), places=2)


class ResizeNormRecTests(unittest.TestCase):
    def test_dynamic_width_from_ratio(self):
        img = np.zeros((48, 96, 3), dtype=np.uint8)
        padded = resize_norm_rec(img, max_wh_ratio=320 / 48)
        self.assertEqual((3, 48, 320), padded.shape)
        # 内容集中在左侧 96 列，右侧全零
        np.testing.assert_array_equal(0.0, padded[:, :, 97:])
        self.assertLess(float(np.abs(padded[:, :, :96]).max()), 1.01)


class CtcDecodeTests(unittest.TestCase):
    def _one_hot(self, tokens_and_probs, num_classes=3):
        probs = np.zeros((1, len(tokens_and_probs), num_classes), dtype=np.float64)
        for t, (token, prob) in enumerate(tokens_and_probs):
            probs[0, t, token] = prob
        return probs

    def test_decode_with_dedup_and_blank(self):
        characters = ["blank", "A", "B"]
        # token 序列 [A, A, blank, B, A]：相邻去重 + 去 blank → "ABA"
        probs = self._one_hot([(1, 0.9), (1, 0.8), (0, 0.95), (2, 0.7), (1, 0.6)])

        text, score = ctc_decode(probs, characters)[0]
        self.assertEqual("ABA", text)
        expected = round(float(np.mean([0.9, 0.7, 0.6])), 5)
        self.assertAlmostEqual(expected, score, places=5)

    def test_all_blank_gives_empty_text_zero_score(self):
        characters = ["blank", "A", "B"]
        probs = self._one_hot([(0, 1.0)] * 4)
        results = ctc_decode(probs, characters)
        self.assertEqual([("", 0.0)], results)


if __name__ == "__main__":
    unittest.main()
