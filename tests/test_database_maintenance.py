import datetime
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

import apppath
import services.global_instances
from blob_storage import BlobFileEntity, BlobStorage
from commons.dto import StickerImage
from image_features_extractor import ExtractionCancelledError, ImageFeatureResult
from image_features_extractor.models import ExtractionProgress, FeatureResultBatch
from services.database_maintenance import (
    DatabaseMaintenanceOptions,
    VectorMaintenanceScope,
    run_database_maintenance,
)
from stickerdb.v1.sticker_db import StickerDBV1
from stickerdb.vectordb import VectorMetadata, VectorRecord
from utils.image_metadata import get_image_metadata


class FakeVectorStore:
    def __init__(self):
        self.records = {}
        self.added_ids = []
        self.deleted_ids = []
        self.reset_calls = 0
        self._next_id = 1

    def reset(self):
        self.reset_calls += 1
        self.records = {}
        self.added_ids = []
        self.deleted_ids = []
        self._next_id = 1

    def add_existing(self, vector_id, sticker):
        metadata = VectorMetadata(
            image_filename=sticker.original_file_name,
            model_hash="old-model",
            sqlite_id=sticker.id,
            extraction_timestamp=1.0,
            image_width=sticker.size_width,
            image_height=sticker.size_height,
        )
        self.records[vector_id] = VectorRecord(
            vector_id,
            np.zeros(768, dtype=np.float32),
            metadata,
        )

    def get(self, vector_id):
        return self.records.get(vector_id)

    def get_by_sqlite_id(self, sqlite_id):
        return next(
            (
                record
                for record in self.records.values()
                if record.metadata.sqlite_id == sqlite_id
            ),
            None,
        )

    def add_batch(self, vectors, metadata_list):
        vector_ids = []
        for vector, metadata in zip(vectors, metadata_list):
            vector_id = f"new-vector-{self._next_id}"
            self._next_id += 1
            self.records[vector_id] = VectorRecord(vector_id, vector, metadata)
            self.added_ids.append(vector_id)
            vector_ids.append(vector_id)
        return vector_ids

    def delete_batch(self, vector_ids):
        for vector_id in vector_ids:
            self.records.pop(vector_id, None)
            self.deleted_ids.append(vector_id)
        return len(vector_ids)


class DatabaseMaintenanceTests(unittest.TestCase):
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

    def tearDown(self):
        services.global_instances.current_library_db = self._old_db
        services.global_instances.current_blob_storage = self._old_blob
        services.global_instances.current_vector_store = self._old_vectors
        apppath.app_path = self._old_app_path
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def _add_sticker(self, name, color):
        source_path = self.root / name
        Image.new("RGB", (12, 8), color).save(source_path)
        metadata = get_image_metadata(source_path)
        self.blob_storage.store_file(str(source_path), metadata.hash)

        sticker = StickerImage()
        sticker.original_file_name = metadata.original_file_name
        sticker.relative_path = str(source_path)
        sticker.file_size = metadata.file_size
        sticker.hash = metadata.hash
        sticker.extension = metadata.extension
        sticker.imported_at = datetime.datetime.now()
        sticker.modification_date = sticker.imported_at
        sticker.size_width = metadata.size_width
        sticker.size_height = metadata.size_height
        sticker.vectordb_id = None
        sticker.text_in_image = None
        return self.db.add_stickers([sticker])[0]

    @staticmethod
    def _successful_iter_features(image_paths, **kwargs):
        for index, image_path in enumerate(image_paths, start=1):
            yield FeatureResultBatch(
                results=(
                    ImageFeatureResult.succeeded(
                        image_path,
                        np.full(768, index, dtype=np.float32),
                    ),
                ),
                progress=ExtractionProgress(
                    completed=index,
                    total=len(image_paths),
                    succeeded=index,
                    failed=0,
                ),
            )

    def test_deletes_only_managed_blobs_missing_from_sqlite(self):
        sticker = self._add_sticker("kept.png", "white")
        orphan_path = self.root / "orphan.png"
        Image.new("RGB", (6, 6), "black").save(orphan_path)
        orphan = self.blob_storage.store_file(str(orphan_path))
        unexpected = self.blob_storage.base_path / "notes.txt"
        unexpected.write_text("keep", encoding="utf-8")
        progress_events = []

        result = run_database_maintenance(
            DatabaseMaintenanceOptions(
                delete_orphan_blobs=True,
                generate_vectors=False,
            ),
            progress=progress_events.append,
        )

        self.assertEqual(1, result.deleted_blob_count)
        self.assertTrue(
            self.blob_storage.exists(BlobFileEntity(sticker.hash, sticker.extension))
        )
        self.assertFalse(self.blob_storage.exists(orphan))
        self.assertTrue(unexpected.exists())
        self.assertEqual(100, progress_events[-1].percent)

    def test_missing_scope_skips_valid_repairs_unlinked_and_generates_absent(self):
        valid = self._add_sticker("valid.png", "red")
        unlinked = self._add_sticker("unlinked.png", "green")
        absent = self._add_sticker("absent.png", "blue")
        self.vector_store.add_existing("valid-vector", valid)
        self.vector_store.add_existing("unlinked-vector", unlinked)
        self.db.set_sticker_vector_ids({valid.id: "valid-vector"})

        with patch(
            "services.database_maintenance.iter_features",
            side_effect=self._successful_iter_features,
        ), patch(
            "services.database_maintenance.get_model_hash",
            return_value="current-model",
        ):
            result = run_database_maintenance(
                DatabaseMaintenanceOptions(
                    delete_orphan_blobs=False,
                    generate_vectors=True,
                    vector_scope=VectorMaintenanceScope.MISSING,
                )
            )

        stickers = {sticker.id: sticker for sticker in self.db.list_stickers(count=None)}
        self.assertEqual(1, result.vectorized_count)
        self.assertEqual(1, result.relinked_vector_count)
        self.assertEqual(1, result.skipped_vector_count)
        self.assertEqual("valid-vector", stickers[valid.id].vectordb_id)
        self.assertEqual("unlinked-vector", stickers[unlinked.id].vectordb_id)
        self.assertEqual("new-vector-1", stickers[absent.id].vectordb_id)

    def test_all_scope_resets_and_regenerates_all_vectors(self):
        sticker = self._add_sticker("replace.png", "purple")
        self.vector_store.add_existing("old-vector", sticker)
        self.db.set_sticker_vector_ids({sticker.id: "old-vector"})

        with patch(
            "services.database_maintenance.iter_features",
            side_effect=self._successful_iter_features,
        ), patch(
            "services.database_maintenance.get_model_hash",
            return_value="current-model",
        ):
            result = run_database_maintenance(
                DatabaseMaintenanceOptions(
                    delete_orphan_blobs=False,
                    generate_vectors=True,
                    vector_scope=VectorMaintenanceScope.ALL,
                )
            )

        stored = self.db.list_stickers()[0]
        self.assertEqual(1, result.vectorized_count)
        self.assertEqual("new-vector-1", stored.vectordb_id)
        self.assertEqual(1, self.vector_store.reset_calls)
        self.assertEqual([], self.vector_store.deleted_ids)
        self.assertNotIn("old-vector", self.vector_store.records)

    def test_sqlite_failure_keeps_new_vectors_for_later_repair(self):
        self._add_sticker("rollback.png", "orange")

        with patch(
            "services.database_maintenance.iter_features",
            side_effect=self._successful_iter_features,
        ), patch(
            "services.database_maintenance.get_model_hash",
            return_value="current-model",
        ), patch.object(
            self.db,
            "replace_sticker_vector_ids",
            side_effect=RuntimeError("sqlite failed"),
        ):
            result = run_database_maintenance(
                DatabaseMaintenanceOptions(
                    delete_orphan_blobs=False,
                    generate_vectors=True,
                )
            )

        self.assertEqual(0, result.vectorized_count)
        self.assertTrue(
            any("sqlite failed" in error for error in result.vector_errors)
        )
        self.assertEqual([], self.vector_store.deleted_ids)
        self.assertIn("new-vector-1", self.vector_store.records)
        self.assertIsNone(self.db.list_stickers()[0].vectordb_id)

    def test_cancel_during_extraction_does_not_commit_current_batch(self):
        self._add_sticker("cancel.png", "yellow")
        cancel_event = threading.Event()

        def cancel_extract(_image_paths, **kwargs):
            self.assertIs(cancel_event, kwargs["cancel_event"])
            cancel_event.set()
            raise ExtractionCancelledError("cancelled")

        with patch(
            "services.database_maintenance.iter_features",
            side_effect=cancel_extract,
        ), patch(
            "services.database_maintenance.get_model_hash",
            return_value="current-model",
        ):
            result = run_database_maintenance(
                DatabaseMaintenanceOptions(
                    delete_orphan_blobs=False,
                    generate_vectors=True,
                ),
                cancel_event=cancel_event,
            )

        self.assertTrue(result.cancelled)
        self.assertEqual([], self.vector_store.added_ids)
        self.assertIsNone(self.db.list_stickers()[0].vectordb_id)

    def test_two_selected_tasks_use_equal_progress_weights(self):
        self._add_sticker("weighted.png", "cyan")
        progress_events = []

        with patch(
            "services.database_maintenance.iter_features",
            side_effect=self._successful_iter_features,
        ), patch(
            "services.database_maintenance.get_model_hash",
            return_value="current-model",
        ):
            run_database_maintenance(
                DatabaseMaintenanceOptions(),
                progress=progress_events.append,
            )

        blob_events = [
            event
            for event in progress_events
            if event.task_name == "删除未引用的Blob数据"
        ]
        self.assertEqual(50, blob_events[-1].percent)
        self.assertEqual(100, progress_events[-1].percent)

    def test_multiple_vector_batches_share_one_extraction_job(self):
        self._add_sticker("first.png", "white")
        self._add_sticker("second.png", "red")
        self._add_sticker("third.png", "blue")
        progress_events = []
        captured_paths = []

        def fake_iter_features(image_paths, **kwargs):
            captured_paths.extend(image_paths)
            vector = np.ones(768, dtype=np.float32)
            for completed, image_path in enumerate(image_paths, start=1):
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
        with patch("services.database_maintenance.VECTOR_BATCH_SIZE", 1), patch(
            "services.database_maintenance.iter_features",
            iter_features_mock,
        ), patch(
            "services.database_maintenance.get_model_hash",
            return_value="current-model",
        ):
            result = run_database_maintenance(
                DatabaseMaintenanceOptions(
                    delete_orphan_blobs=False,
                    generate_vectors=True,
                    vector_scope=VectorMaintenanceScope.MISSING,
                ),
                progress=progress_events.append,
            )

        self.assertEqual(1, iter_features_mock.call_count)
        self.assertEqual(3, len(captured_paths))
        self.assertEqual(3, result.vectorized_count)
        vector_task_percents = [
            event.percent
            for event in progress_events
            if event.task_name == "生成图片特征向量"
        ]
        self.assertEqual([0, 10, 20, 30, 53, 76, 100], vector_task_percents)
        vector_task_completed = [
            event.completed
            for event in progress_events
            if event.task_name == "生成图片特征向量"
        ]
        self.assertEqual(sorted(vector_task_completed), vector_task_completed)


if __name__ == "__main__":
    unittest.main()
