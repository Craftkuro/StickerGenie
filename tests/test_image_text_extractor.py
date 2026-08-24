import multiprocessing
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from image_text_extractor import (
    OcrBatchJobRunner,
    compose_ocr_text,
    normalize_image_path,
)
from image_text_extractor import stages
from image_text_extractor.runner import default_ocr_pool_size
from image_text_extractor.stages import (
    OCR_TEXT_MAX_LENGTH,
    OCR_TEXT_PREFIX,
    load_ocr_engine,
    ocr_image,
)


class FakeRapidOutput:
    txts = ("Hello", "世界")
    scores = (0.95, 0.96)


class OcrTextModelTests(unittest.TestCase):
    def test_normalize_image_path_is_absolute_without_reading_file(self):
        normalized = normalize_image_path(Path("missing") / "image.png")
        self.assertTrue(Path(normalized).is_absolute())
        self.assertTrue(normalized.endswith(str(Path("missing") / "image.png")))


class ComposeOcrTextTests(unittest.TestCase):
    def test_filters_by_strict_confidence_threshold(self):
        self.assertEqual(
            "[OCR]keep",
            compose_ocr_text([("keep", 0.91)]),
        )
        self.assertEqual(
            "[OCR]keep",
            compose_ocr_text([("keep", 0.81)]),
        )
        self.assertIsNone(compose_ocr_text([("drop", 0.80)]))

    def test_joins_cjk_without_space_and_other_text_with_space(self):
        self.assertEqual(
            "[OCR]你好世界",
            compose_ocr_text([("你好", 0.95), ("世界", 0.96)]),
        )
        self.assertEqual(
            "[OCR]Hello World",
            compose_ocr_text([("Hello", 0.95), ("World", 0.96)]),
        )
        self.assertEqual(
            "[OCR]你好world",
            compose_ocr_text([("你好", 0.95), ("world", 0.96)]),
        )
        self.assertEqual(
            "[OCR]Hello 你好",
            compose_ocr_text([("Hello", 0.95), ("你好", 0.96)]),
        )

    def test_accepts_rapidocr_like_output_and_legacy_triples(self):
        self.assertEqual(
            "[OCR]Hello 世界",
            compose_ocr_text(FakeRapidOutput()),
        )
        self.assertEqual(
            "[OCR]Hello 世界",
            compose_ocr_text(
                [
                    ([[0, 0, 1, 1]], "Hello", 0.95),
                    ([[0, 0, 1, 1]], "世界", 0.96),
                ]
            ),
        )

    def test_strips_blocks_and_prefixes_without_space(self):
        self.assertEqual(
            "[OCR]one two",
            compose_ocr_text([("  one  ", 0.95), ("  two  ", 0.96)]),
        )

    def test_truncates_body_but_not_prefix(self):
        long_text = "字" * (OCR_TEXT_MAX_LENGTH + 1)
        result = compose_ocr_text([(long_text, 0.99)])
        self.assertEqual(OCR_TEXT_PREFIX + "字" * OCR_TEXT_MAX_LENGTH, result)
        self.assertEqual(
            len(OCR_TEXT_PREFIX) + OCR_TEXT_MAX_LENGTH,
            len(result),
        )

    def test_empty_or_all_low_confidence_returns_none(self):
        self.assertIsNone(compose_ocr_text([]))
        self.assertIsNone(compose_ocr_text(None))
        self.assertIsNone(
            compose_ocr_text([("  ", 0.99), ("drop", 0.8)])
        )


class OcrStageTests(unittest.TestCase):
    def test_ocr_image_returns_path_and_composed_text(self):
        def fake_engine(image_path):
            self.assertEqual("image.png", image_path)
            return [("Hello", 0.95), ("世界", 0.96)]

        with patch("image_text_extractor.stages._get_engine", return_value=fake_engine):
            image_path, text = ocr_image("image.png")

        self.assertEqual("image.png", image_path)
        self.assertEqual("[OCR]Hello 世界", text)

    def test_ocr_image_returns_none_text_for_empty_output(self):
        with patch(
            "image_text_extractor.stages._get_engine",
            return_value=lambda _path: [],
        ):
            image_path, text = ocr_image("image.png")
        self.assertEqual("image.png", image_path)
        self.assertIsNone(text)


class OcrRunnerTests(unittest.TestCase):
    def test_build_pipeline_declares_single_ocr_stage(self):
        spec = OcrBatchJobRunner().build_pipeline()
        self.assertEqual(("input", "output"), tuple(q.name for q in spec.queues))
        self.assertEqual(1, len(spec.stages))
        stage = spec.stages[0]
        self.assertEqual("ocr", stage.name)
        self.assertGreaterEqual(stage.pool_size, 1)
        self.assertEqual(1, stage.batch_size)
        self.assertIs(spec.setup_func, load_ocr_engine)
        self.assertIs(stage.func, ocr_image)

    def test_default_pool_size_is_at_least_one(self):
        self.assertGreaterEqual(default_ocr_pool_size(), 1)

    def test_pool_size_can_be_overridden(self):
        spec = OcrBatchJobRunner(pool_size=3).build_pipeline()
        self.assertEqual(3, spec.stages[0].pool_size)


class LoadOcrEngineTests(unittest.TestCase):
    def tearDown(self):
        stages._local.__dict__.clear()

    def test_engine_is_thread_local(self):
        factory_calls = []
        lock = threading.Lock()

        def fake_factory():
            with lock:
                engine = object()
                factory_calls.append(engine)
            return engine

        started = threading.Event()
        results = {}

        def worker():
            started.wait()
            results["engine"] = stages._get_engine()

        with patch("ppocr_lite.OcrEngine", side_effect=fake_factory):
            main_first = stages._get_engine()
            main_second = stages._get_engine()
            thread = threading.Thread(target=worker)
            thread.start()
            started.set()
            thread.join()

        self.assertIs(main_first, main_second)
        self.assertIsNot(main_first, results["engine"])
        self.assertEqual(2, len(factory_calls))

    def test_load_ocr_engine_returns_startup_info(self):
        with patch(
            "ppocr_lite.OcrEngine", side_effect=lambda: object()
        ):
            self.assertEqual(
                {"engine_name": "ppocr_lite"}, load_ocr_engine()
            )

    @unittest.skipUnless(
        os.environ.get("STICKERGENIE_RUN_MODEL_TESTS") == "1",
        "set STICKERGENIE_RUN_MODEL_TESTS=1 to run the real OCR model test",
    )
    def test_real_ppocr_lite_runner(self):
        from PIL import Image, ImageDraw, ImageFont

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "text.png"
            image = Image.new("RGB", (640, 180), "white")
            draw = ImageDraw.Draw(image)
            try:
                font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 48)
            except OSError:
                font = ImageFont.load_default()
            draw.text((30, 50), "Hello 世界 123", fill="black", font=font)
            image.save(image_path)

            summary = OcrBatchJobRunner().run([image_path], timeout=120)

        self.assertEqual(1, summary.completed)
        self.assertEqual(1, summary.succeeded)
        self.assertEqual(0, summary.failed)
        self.assertEqual("ppocr_lite", summary.startup_info["engine_name"])
        path, text = summary.results[0].data
        self.assertEqual(normalize_image_path(image_path), path)
        self.assertTrue(text.startswith(OCR_TEXT_PREFIX))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    unittest.main()
