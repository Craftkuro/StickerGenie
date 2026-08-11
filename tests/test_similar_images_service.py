import datetime
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import services.global_instances
import services.sticker_library_viewer_service as viewer_service
from blob_storage import BlobStorage
import commons.constants
from commons.dto import StickerImage


def make_sticker(sticker_id: int, name: str, vector_id: str | None = None):
    sticker = StickerImage()
    sticker.id = sticker_id
    sticker.original_file_name = name
    sticker.relative_path = name
    sticker.file_size = 1
    sticker.hash = f"{sticker_id:040d}"
    sticker.extension = ".png"
    sticker.imported_at = datetime.datetime(2026, 1, 1)
    sticker.modification_date = datetime.datetime(2026, 1, 1)
    sticker.size_width = 1
    sticker.size_height = 1
    sticker.vectordb_id = vector_id
    sticker.text_in_image = None
    return sticker


class FakeDB:
    def __init__(self, stickers):
        self.stickers = {sticker.id: sticker for sticker in stickers}
        self.deleted = []

    def get_stickers_by_ids(self, sticker_ids):
        return [
            self.stickers[sticker_id]
            for sticker_id in sticker_ids
            if sticker_id in self.stickers
        ]

    def delete_stickers(self, stickers):
        self.deleted.extend(stickers)


class FakeVectorStore:
    def __init__(self, results=()):
        self.results = list(results)
        self.deleted = []
        self.by_sqlite_id = {}
        self.calls = []

    def add_existing(self, vector_id, sticker_id):
        self.by_sqlite_id[sticker_id] = vector_id

    def delete(self, vector_id):
        self.deleted.append(vector_id)
        return vector_id in self.by_sqlite_id.values()

    def delete_by_sqlite_id(self, sticker_id):
        vector_id = self.by_sqlite_id.get(sticker_id)
        if vector_id is None:
            return False
        del self.by_sqlite_id[sticker_id]
        self.deleted.append(vector_id)
        return True

    def get(self, vector_id):
        if vector_id == "source-vector":
            return SimpleNamespace(id=vector_id)
        return None

    def get_by_sqlite_id(self, sticker_id):
        return None

    def search_by_id(self, vector_id, top_k):
        self.calls.append(top_k)
        return self.results[:top_k]


class SimilarImagesServiceTests(unittest.TestCase):
    def setUp(self):
        self.source = make_sticker(1, "source.png", "source-vector")
        self.second = make_sticker(2, "second.png", "second-vector")
        self.third = make_sticker(3, "third.png", "third-vector")
        self.db = FakeDB([self.source, self.second, self.third])
        self.vector_store = FakeVectorStore(
            [
                SimpleNamespace(sqlite_id=3, similarity=0.95),
                SimpleNamespace(sqlite_id=2, similarity=0.90),
                SimpleNamespace(sqlite_id=999, similarity=0.80),
            ]
        )

        self._old_db = services.global_instances.current_library_db
        self._old_blob = services.global_instances.current_blob_storage
        self._old_vectors = services.global_instances.current_vector_store
        services.global_instances.current_library_db = self.db
        services.global_instances.current_vector_store = self.vector_store

    def tearDown(self):
        services.global_instances.current_library_db = self._old_db
        services.global_instances.current_blob_storage = self._old_blob
        services.global_instances.current_vector_store = self._old_vectors

    def test_similarity_results_keep_vector_ranking_and_skip_stale_rows(self):
        matches = viewer_service.find_similar_stickers(self.source)

        self.assertEqual([3, 2], [sticker.id for sticker, _ in matches])
        self.assertEqual([0.95, 0.90], [similarity for _, similarity in matches])
        self.assertEqual(
            [commons.constants.SIMILAR_IMAGE_CANDIDATE_COUNT],
            self.vector_store.calls,
        )

    def test_missing_vector_reports_a_clear_error(self):
        sticker = make_sticker(4, "missing.png")

        with self.assertRaisesRegex(ValueError, "还没有特征向量"):
            viewer_service.find_similar_stickers(sticker)

    def test_delete_cleans_vector_and_blob_after_sqlite_row(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            blob_storage = BlobStorage(temp_dir)
            source_file = Path(temp_dir) / "delete.png"
            source_file.write_bytes(b"image")
            entity = blob_storage.store_file(
                str(source_file),
                self.second.hash,
            )
            services.global_instances.current_blob_storage = blob_storage

            errors = viewer_service.delete_sticker(self.second)

            self.assertEqual((), errors)
            self.assertEqual([self.second], self.db.deleted)
            self.assertEqual(["second-vector"], self.vector_store.deleted)
            self.assertFalse(blob_storage.exists(entity))

    def test_delete_falls_back_to_sqlite_id_when_stored_id_is_stale(self):
        stale = make_sticker(7, "stale.png", "stale-vector")
        self.vector_store.add_existing("actual-vector", 7)

        with tempfile.TemporaryDirectory() as temp_dir:
            blob_storage = BlobStorage(temp_dir)
            source_file = Path(temp_dir) / "stale.png"
            source_file.write_bytes(b"image")
            entity = blob_storage.store_file(str(source_file), stale.hash)
            services.global_instances.current_blob_storage = blob_storage

            errors = viewer_service.delete_sticker(stale)

        self.assertEqual((), errors)
        self.assertIn("stale-vector", self.vector_store.deleted)
        self.assertIn("actual-vector", self.vector_store.deleted)
        self.assertNotIn(7, self.vector_store.by_sqlite_id)
        self.assertFalse(blob_storage.exists(entity))


class SimilarityCutoffTests(unittest.TestCase):
    def test_keeps_results_before_the_largest_gap(self):
        scores = [0.95, 0.90, 0.80, 0.79]

        self.assertEqual(2, viewer_service._select_similar_count(scores))

    def test_keeps_all_when_scores_have_no_gap_and_top_is_strong(self):
        scores = [0.54, 0.53, 0.52, 0.51]

        self.assertEqual(4, viewer_service._select_similar_count(scores))

    def test_returns_empty_when_no_gap_and_top_score_is_low(self):
        self.assertEqual(
            0, viewer_service._select_similar_count([0.33, 0.32, 0.31])
        )

    def test_drops_single_weak_result(self):
        scores = [0.46, 0.36, 0.35]

        self.assertEqual(0, viewer_service._select_similar_count(scores))

    def test_keeps_single_strong_result(self):
        self.assertEqual(
            1, viewer_service._select_similar_count([0.72, 0.50])
        )

    def test_limits_result_count(self):
        scores = [0.5 - index * 0.001 for index in range(120)]

        self.assertEqual(
            commons.constants.SIMILAR_IMAGE_MAX_RESULTS,
            viewer_service._select_similar_count(scores),
        )

    def test_returns_zero_for_empty_candidates(self):
        self.assertEqual(0, viewer_service._select_similar_count([]))


if __name__ == "__main__":
    unittest.main()
