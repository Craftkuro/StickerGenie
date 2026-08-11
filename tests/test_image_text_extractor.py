import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from image_text_extractor import (
    ImageTextResult,
    TextExtractionCancelledError,
    TextExtractionProgress,
    TextExtractionTimeoutError,
    WorkerCrashedError,
    WorkerInitializationError,
    compose_ocr_text,
    extract_texts,
    iter_texts,
    normalize_image_path,
)
from image_text_extractor.models import (
    OCR_TEXT_MAX_LENGTH,
    OCR_TEXT_PREFIX,
    WorkerStartupInfo,
)
from image_text_extractor.worker import (
    BATCH_RESULT,
    CANCEL,
    DONE,
    END_INPUT,
    INIT_ERROR,
    INIT_OK,
    PROCESS_BATCH,
    REQUEST_BATCH,
    process_image_batch,
)


def fake_worker_entry(connection):
    """Small spawn-safe worker used to test the real parent IPC controller."""

    connection.send(
        (INIT_OK, WorkerStartupInfo(engine_name="rapidocr"))
    )
    connection.send((REQUEST_BATCH, None))
    produced = 0
    try:
        while True:
            kind, payload = connection.recv()
            if kind == PROCESS_BATCH:
                results = []
                for image_path in payload:
                    if Path(image_path).name.startswith("bad"):
                        results.append(
                            ImageTextResult.failed(
                                image_path,
                                "damaged image",
                            )
                        )
                    else:
                        results.append(
                            ImageTextResult.succeeded(
                                image_path,
                                f"{OCR_TEXT_PREFIX}text-{produced}",
                            )
                        )
                    produced += 1
                connection.send((BATCH_RESULT, tuple(results)))
                connection.send((REQUEST_BATCH, None))
            elif kind == END_INPUT:
                connection.send((DONE, False))
                return
            elif kind == CANCEL:
                connection.send((DONE, True))
                return
            else:
                raise RuntimeError(f"unexpected test message: {kind}")
    finally:
        connection.close()


def fake_init_error_worker(connection):
    connection.send((INIT_ERROR, "fake initialization failure"))
    connection.close()


def fake_slow_worker(connection):
    connection.send((INIT_OK, WorkerStartupInfo(engine_name="rapidocr")))
    connection.send((REQUEST_BATCH, None))
    try:
        while True:
            kind, payload = connection.recv()
            if kind == PROCESS_BATCH:
                time.sleep(2.0)
            elif kind == CANCEL:
                connection.send((DONE, True))
                return
            elif kind == END_INPUT:
                connection.send((DONE, False))
                return
    finally:
        connection.close()


def fake_crash_worker(connection):
    connection.send((INIT_OK, WorkerStartupInfo(engine_name="rapidocr")))
    connection.send((REQUEST_BATCH, None))
    try:
        while True:
            kind, payload = connection.recv()
            if kind == PROCESS_BATCH:
                os._exit(17)
    finally:
        connection.close()


class FakeRapidOutput:
    txts = ("Hello", "世界")
    scores = (0.95, 0.96)


class ImageTextModelTests(unittest.TestCase):
    def test_result_invariants(self):
        success = ImageTextResult.succeeded("image.png", "[OCR]hello")
        no_text = ImageTextResult.succeeded("image.png")
        failure = ImageTextResult.failed("bad.png", "cannot decode")

        self.assertTrue(success.success)
        self.assertEqual("[OCR]hello", success.text)
        self.assertIsNone(no_text.text)
        self.assertFalse(failure.success)
        self.assertIsNone(failure.text)

        with self.assertRaises(ValueError):
            ImageTextResult.succeeded("image.png", "hello")
        with self.assertRaises(ValueError):
            ImageTextResult.failed("bad.png", "")
        with self.assertRaises(ValueError):
            ImageTextResult(
                "image.png",
                True,
                "[OCR]hello",
                "unexpected",
            )

    def test_progress_invariants(self):
        progress = TextExtractionProgress(3, 5, 2, 1)
        self.assertEqual(3, progress.completed)
        with self.assertRaises(ValueError):
            TextExtractionProgress(3, 5, 1, 1)

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
        self.assertIsNone(compose_ocr_text([("drop", 0.9)]))
        self.assertIsNone(compose_ocr_text([("drop", 0.89)]))

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


class ImageTextWorkerTests(unittest.TestCase):
    def test_process_image_batch_preserves_order_and_handles_failures(self):
        seen_paths = []

        def fake_engine(image_path):
            seen_paths.append(image_path)
            if Path(image_path).name.startswith("bad"):
                raise OSError("cannot decode")
            if Path(image_path).name.startswith("empty"):
                return []
            return [("Hello", 0.95), ("世界", 0.96)]

        results = process_image_batch(
            fake_engine,
            ["one.png", "bad.png", "empty.png"],
        )

        self.assertEqual(
            ["one.png", "bad.png", "empty.png"],
            [result.image_path for result in results],
        )
        self.assertEqual(
            [True, False, True],
            [result.success for result in results],
        )
        self.assertEqual("[OCR]Hello 世界", results[0].text)
        self.assertIsNone(results[2].text)
        self.assertTrue(results[1].error)
        self.assertEqual(seen_paths, ["one.png", "bad.png", "empty.png"])


class TextExtractionControllerTests(unittest.TestCase):
    def test_streaming_preserves_order_progress_and_failures(self):
        paths = ["one.png", "bad.png", "three.png", "four.png", "five.png"]
        progress_updates = []
        startup = []

        with patch(
            "image_text_extractor.extractor.worker_process_entry",
            fake_worker_entry,
        ):
            batches = list(
                iter_texts(
                    paths,
                    batch_size=2,
                    progress=progress_updates.append,
                    started=startup.append,
                    timeout=10,
                )
            )

        results = [result for batch in batches for result in batch.results]
        self.assertEqual([2, 2, 1], [len(batch.results) for batch in batches])
        self.assertEqual(
            [normalize_image_path(path) for path in paths],
            [result.image_path for result in results],
        )
        self.assertEqual(
            [True, False, True, True, True],
            [result.success for result in results],
        )
        self.assertEqual([2, 4, 5], [item.completed for item in progress_updates])
        self.assertEqual([5, 5, 5], [item.total for item in progress_updates])
        self.assertEqual("rapidocr", startup[0].engine_name)

    def test_generator_without_total_reports_indeterminate_progress(self):
        paths = (path for path in ["one.png", "two.png", "three.png"])
        updates = []
        with patch(
            "image_text_extractor.extractor.worker_process_entry",
            fake_worker_entry,
        ):
            results = extract_texts(
                paths,
                batch_size=2,
                progress=updates.append,
                timeout=10,
            )
        self.assertEqual(3, len(results))
        self.assertEqual([None, None], [update.total for update in updates])

    def test_cancellation_timeout_and_crash_reap_worker(self):
        cancel_event = threading.Event()

        def cancel_after_first_batch(progress):
            if progress.completed:
                cancel_event.set()

        with patch(
            "image_text_extractor.extractor.worker_process_entry",
            fake_worker_entry,
        ):
            with self.assertRaises(TextExtractionCancelledError):
                list(
                    iter_texts(
                        ["one.png", "two.png", "three.png"],
                        batch_size=1,
                        progress=cancel_after_first_batch,
                        cancel_event=cancel_event,
                        timeout=10,
                    )
                )

        with patch(
            "image_text_extractor.extractor.worker_process_entry",
            fake_slow_worker,
        ):
            with self.assertRaises(TextExtractionTimeoutError):
                extract_texts(
                    ["one.png"],
                    timeout=0.2,
                )

        with patch(
            "image_text_extractor.extractor.worker_process_entry",
            fake_crash_worker,
        ):
            with self.assertRaises(WorkerCrashedError):
                extract_texts(
                    ["one.png"],
                    timeout=10,
                )

        self.assertFalse(
            any(
                child.name == "ImageTextExtractorWorker"
                for child in multiprocessing.active_children()
            )
        )

    def test_worker_initialization_failure_is_reported(self):
        with patch(
            "image_text_extractor.extractor.worker_process_entry",
            fake_init_error_worker,
        ):
            with self.assertRaises(WorkerInitializationError):
                extract_texts([], timeout=10)
        self.assertFalse(
            any(
                child.name == "ImageTextExtractorWorker"
                for child in multiprocessing.active_children()
            )
        )

    @unittest.skipUnless(
        os.environ.get("STICKERGENIE_RUN_MODEL_TESTS") == "1",
        "set STICKERGENIE_RUN_MODEL_TESTS=1 to run the real RapidOCR test",
    )
    def test_real_rapidocr_worker(self):
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

            results = extract_texts(
                [image_path],
                timeout=120,
            )

        self.assertEqual(1, len(results))
        self.assertTrue(results[0].success)
        self.assertIsNone(results[0].error)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    unittest.main()
