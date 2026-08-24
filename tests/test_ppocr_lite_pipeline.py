# coding=utf-8
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ppocr_lite.engine import OcrEngine

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/calibri.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _load_font(size=32):
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size), candidate
            except OSError:
                continue
    return ImageFont.load_default(size), None


def render_text_image(
    text,
    size=(360, 120),
    bg=(255, 255, 255),
    color=(0, 0, 0),
    mode="RGB",
    rotate=0,
    font_size=32,
):
    font, _ = _load_font(font_size)
    img = Image.new(mode, size, bg)
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    position = ((size[0] - text_w) // 2 - bbox[0], (size[1] - text_h) // 2 - bbox[1])
    draw.text(position, text, fill=color, font=font)

    if rotate == 180:
        img = img.rotate(180)
    return img


class PipelineTestBase(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls):
        cls.engine = OcrEngine()


@unittest.skipUnless(_load_font()[1], "需要系统 TrueType 字体")
class RecognitionPipelineTests(PipelineTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp_dir = Path(tempfile.mkdtemp(prefix="ppocr_lite_test_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def _recognize(self, img: Image.Image):
        path = self.tmp_dir / f"{self.id()}.png"
        img.save(path)
        return self.engine.recognize(path), path

    def test_english_text_recognized(self):
        results, _ = self._recognize(render_text_image("Hello World 123"))
        self.assertTrue(results, "应识别出至少一行文本")
        joined = " ".join(text for text, _ in results)
        self.assertIn("Hello", joined)
        for _, score in results:
            self.assertGreaterEqual(score, 0.5)

    def test_transparent_sticker_png(self):
        img = render_text_image(
            "Sticker", size=(300, 140), bg=(0, 0, 0, 0), color=(30, 30, 30), mode="RGBA"
        )
        results, _ = self._recognize(img)
        self.assertTrue(results)
        joined = " ".join(text for text, _ in results)
        self.assertIn("Sticker", joined)

    def test_upside_down_text_rotated_back_by_cls(self):
        results, _ = self._recognize(render_text_image("UPSIDE 77", rotate=180))
        self.assertTrue(results)
        joined = " ".join(text for text, _ in results)
        self.assertIn("77", joined)

    def test_blank_image_returns_empty_list(self):
        img = Image.new("RGB", (200, 200), (240, 240, 240))
        results, _ = self._recognize(img)
        self.assertEqual([], results)

    def test_thin_banner_triggers_vertical_padding(self):
        img = render_text_image("banner text", size=(600, 24), font_size=18)
        results, _ = self._recognize(img)
        self.assertTrue(results)


@unittest.skipUnless(_load_font()[1], "需要系统 TrueType 字体")
class RapidOcrComparisonTests(PipelineTestBase):
    """模糊比对：txts 完全一致、scores 差 <0.01、行数与顺序一致。"""

    @classmethod
    def setUpClass(cls):
        import importlib.util

        spec = importlib.util.find_spec("rapidocr")
        if spec is None:
            raise unittest.SkipTest("环境中无 rapidocr，跳过对比测试")

        from rapidocr import RapidOCR

        super().setUpClass()
        cls.rapidocr = RapidOCR(params={"Global.log_level": "WARNING"})

    def _compare(self, img: Image.Image, label: str):
        tmp_dir = Path(tempfile.mkdtemp(prefix="ppocr_lite_cmp_"))
        tmp_path = tmp_dir / f"cmp_{label}.png"
        img.save(tmp_path)

        ours = self.engine.recognize(tmp_path)
        theirs = self.rapidocr(str(tmp_path))
        shutil.rmtree(tmp_dir, ignore_errors=True)

        expected_txts = list(theirs.txts or [])
        expected_scores = list(theirs.scores or [])

        self.assertEqual(len(expected_txts), len(ours), f"{label}: 行数不一致")
        for (our_text, our_score), their_text, their_score in zip(
            ours, expected_txts, expected_scores
        ):
            self.assertEqual(their_text, our_text, f"{label}: 文本不一致")
            self.assertLess(abs(our_score - float(their_score)), 0.01, f"{label}: 分数偏差过大")

    def test_comparison_on_key_samples(self):
        cases = {
            "english": render_text_image("Hello World 123"),
            "transparent": render_text_image(
                "Sticker", size=(300, 140), bg=(0, 0, 0, 0), color=(30, 30, 30), mode="RGBA"
            ),
            "rotated": render_text_image("UPSIDE 77", rotate=180),
            "blank": Image.new("RGB", (200, 200), (240, 240, 240)),
        }
        for label, img in cases.items():
            with self.subTest(case=label):
                self._compare(img, label)


if __name__ == "__main__":
    unittest.main()
