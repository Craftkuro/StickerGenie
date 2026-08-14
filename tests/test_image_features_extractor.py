import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from image_features_extractor import (
    VectorBatchJobRunner,
    WorkerInitializationError,
    normalize_image_path,
)
from image_features_extractor import stages as feature_stages
from image_features_extractor.model_specs import (
    DEFAULT_MODEL_FILENAME,
    DEFAULT_MODEL_SPEC,
    DINOV2_VITB14_REG4_SPEC,
    SIGLIP_BASE_PATCH16_224_SPEC,
    get_model_spec,
)
from image_features_extractor.stages import (
    preprocess_image,
    run_batch_inference,
    select_execution_providers,
)


class FakeSession:
    def __init__(self):
        self.inputs = []

    def run(self, output_names, inputs):
        del output_names
        model_input = inputs["images"]
        self.inputs.append(model_input)
        rows = np.arange(model_input.shape[0], dtype=np.float32)[:, None]
        return [np.repeat(rows, DEFAULT_MODEL_SPEC.feature_vector_size, axis=1)]


class ImageFeatureModelTests(unittest.TestCase):
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
        _, rgb = preprocess_image(str(rgb_path), DEFAULT_MODEL_SPEC)
        self.assertEqual((3, 224, 224), rgb.shape)
        self.assertEqual(np.float32, rgb.dtype)
        self.assertTrue(rgb.flags.c_contiguous)

        rgba_path = self.temp_path / "rgba.png"
        Image.new("RGBA", (32, 48), (255, 0, 0, 0)).save(rgba_path)
        _, rgba = preprocess_image(str(rgba_path), DEFAULT_MODEL_SPEC)
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
        _, palette_result = preprocess_image(str(palette_path), DEFAULT_MODEL_SPEC)
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

        _, transformed = preprocess_image(str(image_path), DEFAULT_MODEL_SPEC)
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

        _, actual = preprocess_image(str(image_path), DEFAULT_MODEL_SPEC)
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
        from image_features_extractor.model_specs import ImageFeatureModelSpec

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

        _, transformed = preprocess_image(str(image_path), spec)

        self.assertEqual((3, 32, 32), transformed.shape)

    def test_preprocess_failure_raises_for_missing_file(self):
        missing = self.temp_path / "missing.png"
        with self.assertRaises(FileNotFoundError):
            preprocess_image(str(missing), DEFAULT_MODEL_SPEC)


class InferenceStageTests(unittest.TestCase):
    def _install_fake_session(self):
        feature_stages._session = FakeSession()
        feature_stages._input_name = "images"
        feature_stages._spec = DEFAULT_MODEL_SPEC

    def tearDown(self):
        feature_stages._session = None
        feature_stages._input_name = None
        feature_stages._spec = None

    def test_run_batch_inference_returns_path_vector_pairs(self):
        self._install_fake_session()
        tensor = np.zeros((3, 224, 224), dtype=np.float32)
        results = run_batch_inference(
            [
                ("a.png", tensor),
                ("b.png", tensor),
            ]
        )
        self.assertEqual(2, len(results))
        self.assertEqual("a.png", results[0][0])
        self.assertEqual("b.png", results[1][0])
        self.assertEqual((DEFAULT_MODEL_SPEC.feature_vector_size,), results[0][1].shape)
        self.assertEqual(np.float32, results[0][1].dtype)
        self.assertEqual(0, results[0][1][0])
        self.assertEqual(1, results[1][1][0])
        self.assertEqual(2, feature_stages._session.inputs[0].shape[0])

    def test_run_batch_inference_requires_initialized_session(self):
        with self.assertRaises(RuntimeError):
            run_batch_inference([("a.png", np.zeros((3, 224, 224)))])


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


class VectorRunnerTests(unittest.TestCase):
    def test_build_pipeline_declares_preprocess_and_infer_stages(self):
        runner = VectorBatchJobRunner("model.onnx")
        spec = runner.build_pipeline()
        self.assertEqual(
            ("input", "preprocessed", "inferred"),
            tuple(q.name for q in spec.queues),
        )
        self.assertEqual(2, len(spec.stages))
        preprocess, infer = spec.stages
        self.assertEqual("preprocess", preprocess.name)
        self.assertEqual(4, preprocess.pool_size)
        self.assertEqual(1, preprocess.batch_size)
        self.assertEqual("infer", infer.name)
        self.assertEqual(1, infer.pool_size)
        self.assertEqual(32, infer.batch_size)

    def test_missing_model_is_worker_initialization_error(self):
        runner = VectorBatchJobRunner("does-not-exist.onnx")
        with self.assertRaises(WorkerInitializationError):
            runner.run([], timeout=10)
        self.assertFalse(
            any(
                child.name == "BatchJobRunnerWorker"
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
            runner = VectorBatchJobRunner(
                model_path, providers=["CPUExecutionProvider"]
            )
            summary = runner.run(
                [first_path, missing_path, third_path],
                started=startup.append,
                timeout=120,
            )

        self.assertEqual(3, summary.completed)
        self.assertEqual(2, summary.succeeded)
        self.assertEqual(1, summary.failed)
        succeeded = [
            wrapper for wrapper in summary.results if not wrapper.hasException
        ]
        self.assertEqual(2, len(succeeded))
        for wrapper in succeeded:
            _, vector = wrapper.data
            self.assertEqual((768,), vector.shape)
            self.assertEqual(np.float32, vector.dtype)
        self.assertEqual(("CPUExecutionProvider",), startup[0]["providers"])


if __name__ == "__main__":
    multiprocessing.freeze_support()
    unittest.main()
