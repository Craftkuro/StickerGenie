import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

import apppath
import services.global_instances
from blob_storage import BlobStorage
from commons.signal_objects import ImportImagesRequest
from image_features_extractor import ExtractionCancelledError, ImageFeatureResult
from services.import_images import (
    ImageImportService,
    ImportImagesResult,
    import_images_with_result,
)
from stickerdb.v1.sticker_db import StickerDBV1


class TrackingVectorStore:
    def __init__(self):
        self.on_add = None
        self.add_count = 0

    def add_batch(self, vectors, metadata):
        self.add_count += 1
        if self.on_add is not None:
            self.on_add()
        return [f"vector-{index}" for index in range(len(vectors))]

    def delete_batch(self, vector_ids):
        return len(vector_ids)


class ImageImportCancellationTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.db = StickerDBV1(str(self.root / "library.db"))
        self.blob_storage = BlobStorage(str(self.root / "blob"))
        self.vector_store = TrackingVectorStore()

        self._old_db = services.global_instances.current_library_db
        self._old_blob = services.global_instances.current_blob_storage
        self._old_vectors = services.global_instances.current_vector_store
        self._old_app_path = apppath.app_path
        services.global_instances.current_library_db = self.db
        services.global_instances.current_blob_storage = self.blob_storage
        services.global_instances.current_vector_store = self.vector_store
        apppath.app_path = self.root
        (self.root / "vit_b_16_features.onnx").write_bytes(b"model")

        self.first_path = self.root / "first.png"
        self.second_path = self.root / "second.png"
        Image.new("RGB", (12, 8), "white").save(self.first_path)
        Image.new("RGB", (12, 8), "black").save(self.second_path)

    def tearDown(self):
        services.global_instances.current_library_db = self._old_db
        services.global_instances.current_blob_storage = self._old_blob
        services.global_instances.current_vector_store = self._old_vectors
        apppath.app_path = self._old_app_path
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def test_cancel_before_preprocessing_returns_no_sqlite_rows(self):
        cancel_event = threading.Event()
        cancel_event.set()

        result = import_images_with_result(
            [str(self.first_path)],
            cancel_event=cancel_event,
        )

        self.assertTrue(result.cancelled)
        self.assertEqual((), result.imported_stickers)
        self.assertEqual([], self.db.list_stickers())

    def test_cancel_after_metadata_returns_before_blob_or_sqlite(self):
        cancel_event = threading.Event()

        from services import import_images as import_images_module

        original_get_metadata = import_images_module.get_image_metadata

        def get_metadata_and_cancel(path):
            metadata = original_get_metadata(path)
            cancel_event.set()
            return metadata

        with patch(
            "services.import_images.get_image_metadata",
            side_effect=get_metadata_and_cancel,
        ), patch.object(self.blob_storage, "store_file") as store_file:
            result = import_images_with_result(
                [str(self.first_path)],
                cancel_event=cancel_event,
            )

        self.assertTrue(result.cancelled)
        store_file.assert_not_called()
        self.assertEqual([], self.db.list_stickers())

    def test_cancel_after_blob_copy_does_not_commit_the_batch(self):
        cancel_event = threading.Event()
        original_store_file = self.blob_storage.store_file

        def store_file_and_cancel(file_path, file_hash):
            entity = original_store_file(file_path, file_hash)
            cancel_event.set()
            return entity

        with patch.object(
            self.blob_storage,
            "store_file",
            side_effect=store_file_and_cancel,
        ):
            result = import_images_with_result(
                [str(self.first_path)],
                cancel_event=cancel_event,
            )

        self.assertTrue(result.cancelled)
        self.assertEqual((), result.imported_stickers)
        self.assertEqual([], self.db.list_stickers())

    def test_cancel_during_sqlite_commit_keeps_the_returned_batch(self):
        cancel_event = threading.Event()
        original_add_stickers = self.db.add_stickers

        def add_stickers_and_cancel(stickers):
            inserted = original_add_stickers(stickers)
            cancel_event.set()
            return inserted

        with patch.object(
            self.db,
            "add_stickers",
            side_effect=add_stickers_and_cancel,
        ), patch("services.import_images.IMPORT_BATCH_SIZE", 1):
            result = import_images_with_result(
                [str(self.first_path), str(self.second_path)],
                cancel_event=cancel_event,
            )

        self.assertTrue(result.cancelled)
        self.assertEqual(1, len(result.imported_stickers))
        self.assertEqual(1, len(self.db.list_stickers()))

    def test_cancel_during_feature_extraction_keeps_sqlite_without_vector(self):
        cancel_event = threading.Event()

        def extract_features(_image_paths, **kwargs):
            self.assertIs(cancel_event, kwargs["cancel_event"])
            cancel_event.set()
            raise ExtractionCancelledError("cancelled")

        with patch(
            "services.import_images.extract_features",
            side_effect=extract_features,
        ):
            result = import_images_with_result(
                [str(self.first_path)],
                generate_vectors=True,
                cancel_event=cancel_event,
            )

        self.assertTrue(result.cancelled)
        self.assertEqual(1, len(result.imported_stickers))
        self.assertEqual(0, result.vectorized_count)
        self.assertIsNone(self.db.list_stickers()[0].vectordb_id)
        self.assertEqual(0, self.vector_store.add_count)

    def test_cancel_during_chroma_add_completes_sqlite_vector_backfill(self):
        cancel_event = threading.Event()
        vector = np.ones(768, dtype=np.float32)
        self.vector_store.on_add = cancel_event.set

        with patch(
            "services.import_images.extract_features",
            return_value=[
                ImageFeatureResult.succeeded(str(self.first_path), vector)
            ],
        ), patch(
            "services.import_images._get_model_hash",
            return_value="test-model-hash",
        ):
            result = import_images_with_result(
                [str(self.first_path)],
                generate_vectors=True,
                cancel_event=cancel_event,
            )

        self.assertTrue(result.cancelled)
        self.assertEqual(1, result.vectorized_count)
        self.assertEqual("vector-0", result.imported_stickers[0].vectordb_id)
        self.assertEqual("vector-0", self.db.list_stickers()[0].vectordb_id)


class ImageImportServiceCancellationTests(unittest.TestCase):
    def test_cancel_is_idempotent_and_rejects_a_second_active_job(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        service = ImageImportService()
        loop = QEventLoop()
        worker_started = threading.Event()
        received_cancelled = []
        received_finished = []
        received_failed = []

        def execute_request(*_args, **kwargs):
            cancel_event = kwargs["cancel_event"]
            worker_started.set()
            while not cancel_event.wait(0.01):
                time.sleep(0)
            return ImportImagesResult(imported_stickers=(), cancelled=True)

        service.import_cancelled.connect(received_cancelled.append)
        service.import_cancelled.connect(lambda _result: loop.quit())
        service.import_finished.connect(received_finished.append)
        service.import_failed.connect(received_failed.append)

        request = ImportImagesRequest(file_paths=("first.png",))
        with patch(
            "services.import_images.import_images_with_result",
            side_effect=execute_request,
        ):
            service.start_import(request)
            thread = next(iter(service._jobs))
            self.assertTrue(worker_started.wait(3))
            with self.assertRaisesRegex(RuntimeError, "已有图片导入任务"):
                service.start_import(request)
            self.assertTrue(service.cancel_import())
            self.assertFalse(service.cancel_import())
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            thread.wait(3000)
            app.processEvents()

        self.assertEqual(1, len(received_cancelled))
        self.assertEqual([], received_finished)
        self.assertEqual([], received_failed)
        self.assertEqual(0, service.active_job_count)
        self.assertFalse(service.cancel_import())

    def test_failure_is_exclusive_and_releases_the_active_job(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        service = ImageImportService()
        loop = QEventLoop()
        received_cancelled = []
        received_finished = []
        received_failed = []

        service.import_cancelled.connect(received_cancelled.append)
        service.import_finished.connect(received_finished.append)
        service.import_failed.connect(received_failed.append)
        service.import_failed.connect(lambda _error: loop.quit())

        request = ImportImagesRequest(file_paths=("first.png",))
        with patch(
            "services.import_images.import_images_with_result",
            side_effect=RuntimeError("database unavailable"),
        ):
            service.start_import(request)
            thread = next(iter(service._jobs))
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            thread.wait(3000)
            app.processEvents()

        self.assertEqual([], received_cancelled)
        self.assertEqual([], received_finished)
        self.assertEqual(["database unavailable"], received_failed)
        self.assertEqual(0, service.active_job_count)
        self.assertFalse(service.cancel_import())


if __name__ == "__main__":
    unittest.main()
