import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from image_features_extractor import (
    ExtractionCancelledError,
    ExtractionProgress,
    ExtractionRequest,
    ExtractionTimeoutError,
    ImageFeatureResult,
    WorkerCrashedError,
    WorkerInitializationError,
    extract_features,
    iter_features,
    normalize_image_path,
)
from image_features_extractor.models import FEATURE_VECTOR_SIZE, WorkerStartupInfo
from image_features_extractor.model_specs import (
    DEFAULT_MODEL_FILENAME,
    DEFAULT_MODEL_SPEC,
    DINOV2_VITB14_REG4_SPEC,
    SIGLIP_BASE_PATCH16_224_SPEC,
    ImageFeatureModelSpec,
    get_model_spec,
)
from image_features_extractor.worker import (
    BATCH_RESULT,
    CANCEL,
    DONE,
    END_INPUT,
    INIT_ERROR,
    INIT_OK,
    PROCESS_BATCH,
    REQUEST_BATCH,
    preprocess_image,
    process_image_batch,
    select_execution_providers,
)


def fake_worker_entry(connection, model_path, providers):
    """Small spawn-safe worker used to test the real parent IPC controller."""

    del providers
    mode = Path(model_path).stem
    if mode == "init_error":
        connection.send((INIT_ERROR, "fake initialization failure"))
        connection.close()
        return
    connection.send(
        (
            INIT_OK,
            WorkerStartupInfo(
                providers=("CPUExecutionProvider",), input_name="images"
            ),
        )
    )
    connection.send((REQUEST_BATCH, None))
    produced = 0
    try:
        while True:
            kind, payload = connection.recv()
            if kind == PROCESS_BATCH:
                if mode == "crash":
                    os._exit(17)
                if mode == "slow":
                    time.sleep(2.0)

                results = []
                for image_path in payload:
                    if Path(image_path).name.startswith("bad"):
                        results.append(
                            ImageFeatureResult.failed(image_path, "damaged image")
                        )
                    else:
                        vector = np.full(
                            FEATURE_VECTOR_SIZE, produced, dtype=np.float32
                        )
                        results.append(ImageFeatureResult.succeeded(image_path, vector))
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


def prefetch_probe_worker_entry(connection, model_path, providers):
    """Worker that records when it receives each PROCESS_BATCH to a file."""

    del providers
    record_path = Path(model_path)
    connection.send(
        (
            INIT_OK,
            WorkerStartupInfo(
                providers=("CPUExecutionProvider",), input_name="images"
            ),
        )
    )
    connection.send((REQUEST_BATCH, None))
    try:
        while True:
            kind, payload = connection.recv()
            if kind == PROCESS_BATCH:
                with open(record_path, "a", encoding="utf-8") as f:
                    f.write(f"worker_batch:{time.monotonic()}\n")
                results = tuple(
                    ImageFeatureResult.succeeded(
                        image_path,
                        np.zeros(FEATURE_VECTOR_SIZE, dtype=np.float32),
                    )
                    for image_path in payload
                )
                connection.send((BATCH_RESULT, results))
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


class FakeSession:
    def __init__(self):
        self.inputs = []

    def run(self, output_names, inputs):
        del output_names
        model_input = inputs["images"]
        self.inputs.append(model_input)
        rows = np.arange(model_input.shape[0], dtype=np.float32)[:, None]
        return [np.repeat(rows, FEATURE_VECTOR_SIZE, axis=1)]


class ImageFeatureModelTests(unittest.TestCase):
    def test_result_invariants(self):
        vector = np.zeros(FEATURE_VECTOR_SIZE, dtype=np.float32)
        success = ImageFeatureResult.succeeded("image.png", vector)
        failure = ImageFeatureResult.failed("bad.png", "cannot decode")

        self.assertTrue(success.success)
        self.assertIs(success.vector, vector)
        self.assertFalse(failure.success)
        self.assertIsNone(failure.vector)

        with self.assertRaises(ValueError):
            ImageFeatureResult.succeeded(
                "image.png", np.zeros(FEATURE_VECTOR_SIZE, dtype=np.float64)
            )
        generic = ImageFeatureResult.succeeded(
            "other.png", np.zeros(512, dtype=np.float32)
        )
        self.assertTrue(generic.success)
        with self.assertRaises(ValueError):
            ImageFeatureResult.succeeded(
                "other.png", np.zeros((1, 512), dtype=np.float32)
            )
        with self.assertRaises(ValueError):
            ImageFeatureResult.failed("bad.png", "")

    def test_progress_invariants(self):
        progress = ExtractionProgress(3, 5, 2, 1)
        self.assertEqual(3, progress.completed)
        with self.assertRaises(ValueError):
            ExtractionProgress(3, 5, 1, 1)

    def test_normalize_image_path_is_absolute_without_reading_file(self):
        normalized = normalize_image_path(Path("missing") / "image.png")
        self.assertTrue(Path(normalized).is_absolute())
        self.assertTrue(normalized.endswith(str(Path("missing") / "image.png")))


class ImagePreprocessingTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self._temp_dir.name)

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_rgb_rgba_and_palette_transparency(self):
        rgb_path = self.temp_path / "rgb.png"
        Image.new("RGB", (300, 180), (10, 20, 30)).save(rgb_path)
        rgb = preprocess_image(str(rgb_path), DEFAULT_MODEL_SPEC)
        self.assertEqual((3, 224, 224), rgb.shape)
        self.assertEqual(np.float32, rgb.dtype)
        self.assertTrue(rgb.flags.c_contiguous)

        rgba_path = self.temp_path / "rgba.png"
        Image.new("RGBA", (32, 48), (255, 0, 0, 0)).save(rgba_path)
        rgba = preprocess_image(str(rgba_path), DEFAULT_MODEL_SPEC)
        mean = np.asarray(
            DEFAULT_MODEL_SPEC.normalize_mean, dtype=np.float32
        )[:, None, None]
        std = np.asarray(
            DEFAULT_MODEL_SPEC.normalize_std, dtype=np.float32
        )[:, None, None]
        expected_white = (
            np.ones((3, 1, 1), dtype=np.float32) - mean
        ) / std
        np.testing.assert_allclose(rgba[:, :1, :1], expected_white, atol=1e-6)

        palette_path = self.temp_path / "palette.png"
        palette = Image.new("P", (24, 24), 0)
        palette.putpalette([255, 0, 0] + [0, 0, 0] * 255)
        palette.info["transparency"] = 0
        palette.save(palette_path)
        palette_result = preprocess_image(str(palette_path), DEFAULT_MODEL_SPEC)
        np.testing.assert_allclose(
            palette_result[:, :1, :1], expected_white, atol=1e-6
        )

    def test_exif_orientation_is_applied(self):
        image_path = self.temp_path / "oriented.jpg"
        pixels = np.zeros((200, 300, 3), dtype=np.uint8)
        pixels[:, :150] = (240, 10, 10)
        pixels[:, 150:] = (10, 10, 240)
        image = Image.fromarray(pixels, mode="RGB")
        exif = image.getexif()
        exif[274] = 6
        image.save(image_path, quality=100, exif=exif)

        transformed = preprocess_image(str(image_path), DEFAULT_MODEL_SPEC)
        top_red = transformed[0, :80].mean()
        bottom_red = transformed[0, -80:].mean()
        top_blue = transformed[2, :80].mean()
        bottom_blue = transformed[2, -80:].mean()
        self.assertGreater(top_red, bottom_red)
        self.assertLess(top_blue, bottom_blue)

    def test_matches_siglip_image_processor(self):
        try:
            from transformers import SiglipImageProcessor
        except ImportError as error:
            self.skipTest(str(error))

        image_path = self.temp_path / "pattern.png"
        y, x = np.mgrid[:199, :301]
        pixels = np.stack(
            ((x * 3) % 256, (y * 5) % 256, (x + y * 2) % 256), axis=2
        ).astype(np.uint8)
        Image.fromarray(pixels, mode="RGB").save(image_path)

        actual = preprocess_image(str(image_path), DEFAULT_MODEL_SPEC)
        processor = SiglipImageProcessor(
            size={"height": 224, "width": 224},
            image_mean=(0.5, 0.5, 0.5),
            image_std=(0.5, 0.5, 0.5),
            resample=Image.Resampling.BICUBIC,
        )
        with Image.open(image_path) as source:
            expected = processor(
                images=source.convert("RGB"),
                return_tensors="pt",
            )["pixel_values"][0].numpy()
        self.assertEqual(actual.shape, expected.shape)
        np.testing.assert_allclose(
            actual,
            expected,
            rtol=0,
            atol=0.02,
        )

    def test_preprocess_uses_model_spec_dimensions(self):
        image_path = self.temp_path / "small.png"
        Image.new("RGB", (80, 60), "white").save(image_path)
        spec = ImageFeatureModelSpec(
            name="small-test",
            model_filename="small-test.onnx",
            feature_vector_size=64,
            input_size=32,
            resize_size=40,
            normalize_mean=(0.5, 0.5, 0.5),
            normalize_std=(0.25, 0.25, 0.25),
        )

        transformed = preprocess_image(str(image_path), spec)

        self.assertEqual((3, 32, 32), transformed.shape)

    def test_single_image_failure_does_not_skip_later_images(self):
        first = self.temp_path / "first.png"
        missing = self.temp_path / "missing.png"
        third = self.temp_path / "third.png"
        Image.new("RGB", (16, 16), "red").save(first)
        Image.new("RGB", (16, 16), "blue").save(third)
        session = FakeSession()

        results = process_image_batch(
            session,
            "images",
            [str(first), str(missing), str(third)],
            DEFAULT_MODEL_SPEC,
        )

        self.assertEqual([str(first), str(missing), str(third)], [r.image_path for r in results])
        self.assertEqual([True, False, True], [r.success for r in results])
        self.assertEqual((2, 3, 224, 224), session.inputs[0].shape)
        self.assertEqual(0, results[0].vector[0])
        self.assertEqual(1, results[2].vector[0])


class ModelSpecTests(unittest.TestCase):
    def test_default_model_is_registered_by_filename(self):
        self.assertIs(DEFAULT_MODEL_SPEC, get_model_spec(DEFAULT_MODEL_FILENAME))

    def test_default_model_is_siglip(self):
        spec = DEFAULT_MODEL_SPEC
        self.assertEqual("siglip_base_patch16_224", spec.name)
        self.assertEqual(768, spec.feature_vector_size)
        self.assertEqual("resize", spec.resize_mode)
        self.assertEqual((0.5, 0.5, 0.5), spec.normalize_mean)

    def test_reg4_model_is_registered(self):
        spec = get_model_spec(DINOV2_VITB14_REG4_SPEC.model_filename)
        self.assertIs(DINOV2_VITB14_REG4_SPEC, spec)
        self.assertEqual(768, spec.feature_vector_size)

    def test_siglip_model_is_registered(self):
        spec = get_model_spec(SIGLIP_BASE_PATCH16_224_SPEC.model_filename)
        self.assertIs(SIGLIP_BASE_PATCH16_224_SPEC, spec)
        self.assertEqual(768, spec.feature_vector_size)
        self.assertEqual("resize", spec.resize_mode)

    def test_unknown_model_raises(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            get_model_spec("unknown.onnx")


class ProviderSelectionTests(unittest.TestCase):
    def test_default_provider_selection_prefers_cuda(self):
        self.assertEqual(
            ["CUDAExecutionProvider", "CPUExecutionProvider"],
            select_execution_providers(
                ["CPUExecutionProvider", "CUDAExecutionProvider"]
            ),
        )
        self.assertEqual(
            ["CPUExecutionProvider"],
            select_execution_providers(["CPUExecutionProvider"]),
        )

    def test_explicit_provider_must_be_available(self):
        with self.assertRaises(RuntimeError):
            select_execution_providers(
                ["CPUExecutionProvider"], ["CUDAExecutionProvider"]
            )


class ExtractionControllerTests(unittest.TestCase):
    def _fake_worker_patch(self):
        return patch(
            "image_features_extractor.extractor.worker_process_entry",
            fake_worker_entry,
        )

    def test_streaming_preserves_order_progress_and_failures(self):
        paths = ["one.png", "bad.png", "three.png", "four.png", "five.png"]
        progress_updates = []
        startup = []

        with self._fake_worker_patch():
            batches = list(
                iter_features(
                    paths,
                    model_path="normal.onnx",
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
        self.assertEqual([True, False, True, True, True], [r.success for r in results])
        self.assertEqual([2, 4, 5], [item.completed for item in progress_updates])
        self.assertEqual([5, 5, 5], [item.total for item in progress_updates])
        self.assertEqual(("CPUExecutionProvider",), startup[0].providers)

    def test_generator_without_total_reports_indeterminate_progress(self):
        paths = (path for path in ["one.png", "two.png", "three.png"])
        updates = []
        with self._fake_worker_patch():
            results = extract_features(
                paths,
                model_path="normal.onnx",
                batch_size=2,
                progress=updates.append,
                timeout=10,
            )
        self.assertEqual(3, len(results))
        self.assertEqual([None, None], [update.total for update in updates])

    def test_prefetch_dispatches_next_batch_while_consumer_is_busy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = Path(temp_dir) / "prefetch_probe.onnx"
            consumer_finished = []

            with patch(
                "image_features_extractor.extractor.worker_process_entry",
                prefetch_probe_worker_entry,
            ):
                for batch in iter_features(
                    ["one.png", "two.png", "three.png", "four.png"],
                    model_path=str(record_path),
                    batch_size=2,
                    timeout=10,
                ):
                    if batch.progress.completed < batch.progress.total:
                        # 模拟消费方写库耗时（ChromaDB + SQLite 回填）。
                        time.sleep(0.5)
                        consumer_finished.append(time.monotonic())

            records = [
                float(line.split(":", 1)[1])
                for line in record_path.read_text(encoding="utf-8").splitlines()
                if line.startswith("worker_batch:")
            ]

        self.assertEqual(2, len(records))
        # 第二批应在消费方处理第一批期间就已下发（预取），
        # 否则会晚于消费方完成时刻。
        self.assertLess(records[1], consumer_finished[0])

    def test_cancellation_timeout_and_crash_reap_worker(self):
        cancel_event = threading.Event()

        def cancel_after_first_batch(progress):
            if progress.completed:
                cancel_event.set()

        with self._fake_worker_patch():
            with self.assertRaises(ExtractionCancelledError):
                list(
                    iter_features(
                        ["one.png", "two.png", "three.png"],
                        model_path="normal.onnx",
                        batch_size=1,
                        progress=cancel_after_first_batch,
                        cancel_event=cancel_event,
                        timeout=10,
                    )
                )

        with self._fake_worker_patch():
            with self.assertRaises(ExtractionTimeoutError):
                extract_features(
                    ["one.png"], model_path="slow.onnx", timeout=0.2
                )

        with self._fake_worker_patch():
            with self.assertRaises(WorkerCrashedError):
                extract_features(
                    ["one.png"], model_path="crash.onnx", timeout=10
                )

        self.assertFalse(
            any(
                child.name == "ImageFeaturesExtractorWorker"
                for child in multiprocessing.active_children()
            )
        )

    def test_missing_model_is_worker_initialization_error(self):
        with self.assertRaises(WorkerInitializationError):
            extract_features([], model_path="does-not-exist.onnx", timeout=10)
        self.assertFalse(
            any(
                child.name == "ImageFeaturesExtractorWorker"
                for child in multiprocessing.active_children()
            )
        )

    @unittest.skipUnless(
        os.environ.get("STICKERGENIE_RUN_MODEL_TESTS") == "1",
        "set STICKERGENIE_RUN_MODEL_TESTS=1 to run the SigLIP model test",
    )
    def test_real_model_cpu_inference(self):
        project_root = Path(__file__).resolve().parents[1]
        model_path = project_root / "src" / DEFAULT_MODEL_FILENAME
        startup = []
        with tempfile.TemporaryDirectory() as temp_dir:
            first_path = Path(temp_dir) / "first.png"
            missing_path = Path(temp_dir) / "missing.png"
            third_path = Path(temp_dir) / "third.png"
            Image.new("RGB", (320, 240), (12, 34, 56)).save(first_path)
            Image.new("RGB", (200, 360), (210, 180, 25)).save(third_path)
            results = extract_features(
                [first_path, missing_path, third_path],
                model_path=model_path,
                batch_size=3,
                providers=["CPUExecutionProvider"],
                started=startup.append,
                timeout=120,
            )
        self.assertEqual(3, len(results))
        self.assertTrue(results[0].success)
        self.assertFalse(results[1].success)
        self.assertTrue(results[2].success)
        self.assertEqual((FEATURE_VECTOR_SIZE,), results[0].vector.shape)
        self.assertEqual(np.float32, results[0].vector.dtype)
        self.assertEqual((FEATURE_VECTOR_SIZE,), results[2].vector.shape)
        self.assertEqual(np.float32, results[2].vector.dtype)
        self.assertEqual(("CPUExecutionProvider",), startup[0].providers)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    unittest.main()
