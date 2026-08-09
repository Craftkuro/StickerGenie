import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from PyQt6.QtCore import QCoreApplication, QEventLoop, QThread, QTimer

import apppath
import services.global_instances
from blob_storage import BlobStorage
from commons.signal_objects import ImportImagesRequest
from image_features_extractor import ImageFeatureResult
from image_features_extractor.models import ExtractionProgress, FeatureResultBatch
from services.import_images import (
    ImageImportService,
    ImportImagesProgress,
    ImportImagesResult,
    import_images_with_result,
)
from stickerdb.v1.sticker_db import StickerDBV1
from stickerdb.vectordb import ChromaVectorStore


def _single_result_batch(image_path, vector):
    return FeatureResultBatch(
        results=(ImageFeatureResult.succeeded(image_path, vector),),
        progress=ExtractionProgress(
            completed=1,
            total=1,
            succeeded=1,
            failed=0,
        ),
    )


class FakeVectorStore:
    def __init__(self):
        self.vectors = None
        self.metadata = None

    def add_batch(self, vectors, metadata):
        self.vectors = vectors
        self.metadata = metadata
        return ["vector-uuid-1"]

    def delete_batch(self, vector_ids):
        return len(vector_ids)


class ImportImagesVectorTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.db = StickerDBV1(str(self.root / "library.db"))
        self.blob_storage = BlobStorage(str(self.root / "blob"))
        self.vector_store = FakeVectorStore()

        self._old_db = services.global_instances.current_library_db
        self._old_blob = services.global_instances.current_blob_storage
        self._old_vectors = services.global_instances.current_vector_store
        self._old_app_path = apppath.app_path
        services.global_instances.current_library_db = self.db
        services.global_instances.current_blob_storage = self.blob_storage
        services.global_instances.current_vector_store = self.vector_store
        apppath.app_path = self.root
        (self.root / "vit_b_16_features.onnx").write_bytes(b"model")

        self.source_path = self.root / "source.png"
        Image.new("RGB", (12, 8), "white").save(self.source_path)

    def tearDown(self):
        services.global_instances.current_library_db = self._old_db
        services.global_instances.current_blob_storage = self._old_blob
        services.global_instances.current_vector_store = self._old_vectors
        apppath.app_path = self._old_app_path
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def test_vectorizes_blob_path_and_backfills_uuid(self):
        captured_paths = []
        captured_cancel_events = []
        progress_events = []
        cancel_event = threading.Event()

        def extract(image_paths, **kwargs):
            captured_paths.extend(image_paths)
            captured_cancel_events.append(kwargs["cancel_event"])
            vector = np.ones(768, dtype=np.float32)
            yield _single_result_batch(image_paths[0], vector)

        with patch("services.import_images.iter_features", side_effect=extract), patch(
            "services.import_images._get_model_hash",
            return_value="test-model-hash",
        ):
            result = import_images_with_result(
                [str(self.source_path)],
                generate_vectors=True,
                progress=progress_events.append,
                cancel_event=cancel_event,
            )

        self.assertEqual(1, len(result.imported_stickers))
        self.assertEqual(1, result.vectorized_count)
        self.assertEqual((), result.vector_errors)
        self.assertNotEqual(str(self.source_path), captured_paths[0])
        self.assertTrue(
            Path(captured_paths[0]).is_relative_to(self.blob_storage.base_path)
        )

        sticker = self.db.list_stickers()[0]
        self.assertEqual("vector-uuid-1", sticker.vectordb_id)
        self.assertEqual(sticker.id, self.vector_store.metadata[0].sqlite_id)
        self.assertEqual("test-model-hash", self.vector_store.metadata[0].model_hash)
        self.assertEqual([cancel_event], captured_cancel_events)
        percents = [event.percent for event in progress_events]
        self.assertEqual(sorted(percents), percents)
        self.assertEqual([0, 1], percents[:2])
        self.assertEqual(100, percents[-1])
        vector_progress = [
            event
            for event in progress_events
            if event.status == "正在生成图片向量"
        ]
        self.assertTrue(vector_progress)
        self.assertEqual([30, 100], [
            event.percent for event in vector_progress
        ])
        self.assertEqual([0, 1], [
            event.completed for event in vector_progress
        ])

    def test_reimporting_the_same_hash_is_silently_ignored(self):
        vector = np.ones(768, dtype=np.float32)

        def fake_iter_features(image_paths, **kwargs):
            yield _single_result_batch(image_paths[0], vector)

        with patch(
            "services.import_images.iter_features",
            side_effect=fake_iter_features,
        ) as iter_features_call, patch(
            "services.import_images._get_model_hash",
            return_value="test-model-hash",
        ):
            first_result = import_images_with_result(
                [str(self.source_path)],
                generate_vectors=True,
            )
            second_result = import_images_with_result(
                [str(self.source_path)],
                generate_vectors=True,
            )

        self.assertEqual(1, len(first_result.imported_stickers))
        self.assertEqual((), second_result.imported_stickers)
        self.assertEqual(1, second_result.duplicate_count)
        self.assertEqual(0, second_result.vectorized_count)
        self.assertEqual((), second_result.vector_errors)
        self.assertEqual(1, iter_features_call.call_count)
        self.assertEqual(1, len(self.db.list_stickers()))

    def test_duplicate_hashes_inside_one_request_are_imported_once(self):
        duplicate_path = self.root / "duplicate.png"
        duplicate_path.write_bytes(self.source_path.read_bytes())

        result = import_images_with_result(
            [str(self.source_path), str(duplicate_path)]
        )

        self.assertEqual(1, len(result.imported_stickers))
        self.assertEqual(1, len(self.db.list_stickers()))

    def test_multiple_import_batches_share_one_extraction_job(self):
        second_path = self.root / "second.png"
        third_path = self.root / "third.png"
        Image.new("RGB", (12, 8), "red").save(second_path)
        Image.new("RGB", (12, 8), "blue").save(third_path)
        progress_events = []
        captured_paths = []

        def fake_iter_features(image_paths, **kwargs):
            captured_paths.extend(image_paths)
            vector = np.ones(768, dtype=np.float32)
            for completed, image_path in enumerate(image_paths, 1):
                yield FeatureResultBatch(
                    results=(
                        ImageFeatureResult.succeeded(image_path, vector),
                    ),
                    progress=ExtractionProgress(
                        completed=completed,
                        total=len(image_paths),
                        succeeded=completed,
                        failed=0,
                    ),
                )

        iter_features_mock = Mock(side_effect=fake_iter_features)
        with patch("services.import_images.IMPORT_BATCH_SIZE", 1), patch(
            "services.import_images.iter_features",
            iter_features_mock,
        ), patch(
            "services.import_images._get_model_hash",
            return_value="test-model-hash",
        ):
            result = import_images_with_result(
                [
                    str(self.source_path),
                    str(second_path),
                    str(third_path),
                ],
                generate_vectors=True,
                progress=progress_events.append,
            )

        self.assertEqual(1, iter_features_mock.call_count)
        self.assertEqual(3, len(captured_paths))
        self.assertEqual(3, len(result.imported_stickers))
        self.assertEqual(3, result.vectorized_count)
        vector_progress = [
            event.percent
            for event in progress_events
            if event.status == "正在生成图片向量"
        ]
        self.assertEqual([30, 53, 76, 100], vector_progress)

    def test_reports_preprocessing_import_and_completion_progress(self):
        progress_events = []

        result = import_images_with_result(
            [str(self.source_path)],
            progress=progress_events.append,
        )

        self.assertEqual(1, len(result.imported_stickers))
        self.assertEqual(0, progress_events[0].percent)
        self.assertEqual("正在预处理图片", progress_events[0].status)
        self.assertEqual(1, progress_events[1].percent)
        self.assertEqual("正在写入图库", progress_events[1].status)
        self.assertEqual(100, progress_events[-1].percent)
        self.assertEqual("导入完成", progress_events[-1].status)
        self.assertTrue(
            any(
                event.last_file_name == self.source_path.name
                for event in progress_events
            )
        )

    def test_vector_store_failure_does_not_report_the_image_import_as_failed(self):
        vector = np.ones(768, dtype=np.float32)
        self.vector_store.add_batch = Mock(
            side_effect=RuntimeError("vector store unavailable")
        )

        def fake_iter_features(image_paths, **kwargs):
            yield _single_result_batch(image_paths[0], vector)

        with patch(
            "services.import_images.iter_features",
            side_effect=fake_iter_features,
        ), patch(
            "services.import_images._get_model_hash",
            return_value="test-model-hash",
        ):
            result = import_images_with_result(
                [str(self.source_path)],
                generate_vectors=True,
            )

        self.assertEqual(1, len(result.imported_stickers))
        self.assertEqual(0, result.vectorized_count)
        self.assertIn("vector store unavailable", result.vector_errors[0])
        self.assertEqual(1, len(self.db.list_stickers()))

    def test_import_service_executes_the_request_outside_the_main_thread(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        service = ImageImportService()
        loop = QEventLoop()
        execution_threads = []
        received_results = []
        expected_result = ImportImagesResult(imported_stickers=())
        expected_progress = ImportImagesProgress(0, "正在预处理图片")
        received_progress = []

        def execute_request(*args, **kwargs):
            execution_threads.append(QThread.currentThread())
            kwargs["progress"](expected_progress)
            return expected_result

        def finish(result):
            received_results.append(result)
            loop.quit()

        service.import_finished.connect(finish)
        service.import_progress_changed.connect(received_progress.append)
        request = ImportImagesRequest(file_paths=(str(self.source_path),))
        with patch(
            "services.import_images.import_images_with_result",
            side_effect=execute_request,
        ):
            service.start_import(request)
            thread = next(iter(service._jobs))
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            thread.wait(3000)
            app.processEvents()

        self.assertEqual([expected_result], received_results)
        self.assertEqual([expected_progress], received_progress)
        self.assertEqual(1, len(execution_threads))
        self.assertIsNot(execution_threads[0], app.thread())
        self.assertEqual(0, service.active_job_count)
        self.assertFalse(service.cancel_import())

    @unittest.skipUnless(
        os.environ.get("STICKERGENIE_RUN_MODEL_TESTS") == "1",
        "set STICKERGENIE_RUN_MODEL_TESTS=1 to run the real vector integration test",
    )
    def test_real_model_vector_is_queryable_by_sqlite_id(self):
        project_source = Path(__file__).resolve().parents[1] / "src"
        real_vector_store = ChromaVectorStore(str(self.root / "vectors"))
        real_vector_store.initialize()
        services.global_instances.current_vector_store = real_vector_store
        apppath.app_path = project_source

        try:
            result = import_images_with_result(
                [str(self.source_path)],
                generate_vectors=True,
            )
            sticker = result.imported_stickers[0]
            record = real_vector_store.get_by_sqlite_id(sticker.id)

            self.assertEqual(1, result.vectorized_count)
            self.assertEqual((), result.vector_errors)
            self.assertIsNotNone(record)
            self.assertEqual(sticker.vectordb_id, record.id)
            self.assertEqual(sticker.id, record.metadata.sqlite_id)
        finally:
            real_vector_store.close()


if __name__ == "__main__":
    unittest.main()
