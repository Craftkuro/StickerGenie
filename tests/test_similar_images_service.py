import datetime
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import services.global_instances
import services.settings
import services.sticker_library_viewer_service as viewer_service
import services.similarity_result_filter as similarity_filter
from blob_storage import BlobFileEntity, BlobStorage
import commons.constants
from commons.dto import StickerImage


def make_sticker(sticker_id: int, name: str, vector_id: str | None = None):
    sticker = StickerImage()
    sticker.id = sticker_id
    sticker.original_file_name = name
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

    def find_ids_by_sqlite_ids(self, sticker_ids):
        wanted = set(sticker_ids)
        return {
            sticker_id: vector_id
            for sticker_id, vector_id in self.by_sqlite_id.items()
            if sticker_id in wanted
        }

    def delete_batch(self, vector_ids):
        unique = list(dict.fromkeys(vector_ids))
        existing = set(self.by_sqlite_id.values())
        removed = {vector_id for vector_id in unique if vector_id in existing}
        self.by_sqlite_id = {
            sticker_id: registered_id
            for sticker_id, registered_id in self.by_sqlite_id.items()
            if registered_id not in removed
        }
        self.deleted.extend(unique)
        return len(removed)

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
        self._old_library_path = (
            services.global_instances.current_library_path
        )
        self._old_settings = (
            services.global_instances.current_settings_manager
        )
        self._settings_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._settings_dir.cleanup)
        self.settings_manager = services.settings.create_settings_manager(
            Path(self._settings_dir.name) / "settings.toml"
        )
        services.global_instances.current_library_db = self.db
        services.global_instances.current_vector_store = self.vector_store
        services.global_instances.current_settings_manager = (
            self.settings_manager
        )

    def tearDown(self):
        services.global_instances.current_library_db = self._old_db
        services.global_instances.current_blob_storage = self._old_blob
        services.global_instances.current_vector_store = self._old_vectors
        services.global_instances.current_library_path = (
            self._old_library_path
        )
        services.global_instances.current_settings_manager = (
            self._old_settings
        )

    def test_fetch_similar_candidates_returns_unfiltered_results(self):
        results, sticker_map = viewer_service.fetch_similar_candidates(
            self.source
        )
        self.assertEqual(
            [3, 2, 999], [r.sqlite_id for r in results]
        )
        self.assertEqual([0.95, 0.90, 0.80], [r.similarity for r in results])
        self.assertEqual({2, 3}, set(sticker_map.keys()))

    def test_build_similar_matches_no_filter_returns_all(self):
        results, sticker_map = viewer_service.fetch_similar_candidates(
            self.source
        )
        matches = viewer_service.build_similar_matches(
            results, sticker_map, result_filter=None
        )
        self.assertEqual([3, 2], [s.id for s, _ in matches])
        self.assertEqual([0.95, 0.90], [sim for _, sim in matches])

    def test_build_similar_matches_with_filter_applies_filter(self):
        results, sticker_map = viewer_service.fetch_similar_candidates(
            self.source
        )
        custom_filter = similarity_filter.SimilarityResultFilter(
            similarity_filter.SimilarityFilterConfig(
                target_drop_ratio=0.5,
                min_keep=1,
                min_similarity=0.40,
                max_results=100,
            )
        )
        matches = viewer_service.build_similar_matches(
            results, sticker_map, result_filter=custom_filter
        )
        self.assertEqual([3, 2], [s.id for s, _ in matches])

    def test_similarity_results_keep_vector_ranking_and_skip_stale_rows(self):
        results, sticker_map = viewer_service.fetch_similar_candidates(
            self.source
        )
        matches = viewer_service.build_similar_matches(
            results, sticker_map
        )

        self.assertEqual([3, 2], [sticker.id for sticker, _ in matches])
        self.assertEqual([0.95, 0.90], [similarity for _, similarity in matches])
        self.assertEqual(
            [commons.constants.SIMILAR_IMAGE_CANDIDATE_COUNT],
            self.vector_store.calls,
        )

    def test_fetch_reads_candidate_count_from_settings_manager(self):
        self.settings_manager.set("similar_image_candidate_count", 42)

        viewer_service.fetch_similar_candidates(self.source)

        self.assertEqual([42], self.vector_store.calls)

    def test_explicit_top_k_overrides_settings_value(self):
        self.settings_manager.set("similar_image_candidate_count", 42)

        viewer_service.fetch_similar_candidates(self.source, top_k=7)

        self.assertEqual([7], self.vector_store.calls)

    def test_similarity_results_use_custom_filter(self):
        results, sticker_map = viewer_service.fetch_similar_candidates(
            self.source
        )
        custom_filter = similarity_filter.SimilarityResultFilter(
            similarity_filter.SimilarityFilterConfig(
                target_drop_ratio=0.5,
                min_keep=1,
                min_similarity=0.40,
                max_results=100,
            )
        )
        matches = viewer_service.build_similar_matches(
            results, sticker_map, result_filter=custom_filter
        )

        # With min_keep=1, the steep drop after the second candidate cuts
        # the result list to the first two candidates.
        self.assertEqual([3, 2], [sticker.id for sticker, _ in matches])

    def test_similarity_results_include_source_as_reference(self):
        self.vector_store.results = [
            SimpleNamespace(sqlite_id=1, similarity=1.0),
            SimpleNamespace(sqlite_id=3, similarity=0.95),
            SimpleNamespace(sqlite_id=2, similarity=0.90),
            SimpleNamespace(sqlite_id=999, similarity=0.80),
        ]

        results, sticker_map = viewer_service.fetch_similar_candidates(
            self.source
        )
        matches = viewer_service.build_similar_matches(
            results, sticker_map
        )

        self.assertEqual(
            [1, 3, 2],
            [sticker.id for sticker, _ in matches],
        )
        self.assertEqual(
            [1.0, 0.95, 0.90],
            [similarity for _, similarity in matches],
        )

    def test_missing_vector_reports_a_clear_error(self):
        sticker = make_sticker(4, "missing.png")

        with self.assertRaisesRegex(ValueError, "还没有特征向量"):
            viewer_service.fetch_similar_candidates(sticker)

    def test_delete_moves_blob_to_recycler_after_sqlite_row(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            blob_storage = BlobStorage(str(library_root / "blob"))
            source_file = library_root / "delete.png"
            source_file.write_bytes(b"image")
            entity = blob_storage.store_file(
                str(source_file),
                self.second.hash,
            )
            services.global_instances.current_blob_storage = blob_storage
            services.global_instances.current_library_path = library_root

            errors = viewer_service.delete_sticker(self.second)

            recycler = library_root / "recycler"
            self.assertEqual((), errors)
            self.assertEqual([self.second], self.db.deleted)
            self.assertEqual(["second-vector"], self.vector_store.deleted)
            self.assertFalse(blob_storage.exists(entity))
            stashed = recycler / f"{self.second.hash}{self.second.extension}"
            self.assertEqual(b"image", stashed.read_bytes())
            payload = json.loads(
                (recycler / f"{self.second.hash}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("second.png", payload["original_file_name"])

    def test_delete_falls_back_to_sqlite_id_when_stored_id_is_stale(self):
        stale = make_sticker(7, "stale.png", "stale-vector")
        self.vector_store.add_existing("actual-vector", 7)

        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            blob_storage = BlobStorage(str(library_root / "blob"))
            source_file = library_root / "stale.png"
            source_file.write_bytes(b"image")
            entity = blob_storage.store_file(str(source_file), stale.hash)
            services.global_instances.current_blob_storage = blob_storage
            services.global_instances.current_library_path = library_root

            errors = viewer_service.delete_sticker(stale)

            self.assertEqual((), errors)
            self.assertIn("stale-vector", self.vector_store.deleted)
            self.assertIn("actual-vector", self.vector_store.deleted)
            self.assertNotIn(7, self.vector_store.by_sqlite_id)
            self.assertFalse(blob_storage.exists(entity))
            self.assertTrue(
                (
                    library_root
                    / "recycler"
                    / f"{stale.hash}{stale.extension}"
                ).is_file()
            )

    def test_delete_stickers_cleans_all_rows_vectors_and_blobs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            blob_storage = BlobStorage(str(library_root / "blob"))
            entities = []
            for sticker in (self.second, self.third):
                source_file = library_root / sticker.original_file_name
                source_file.write_bytes(b"image")
                entity = blob_storage.store_file(
                    str(source_file),
                    sticker.hash,
                )
                entities.append(entity)
            self.vector_store.add_existing("second-vector", 2)
            self.vector_store.add_existing("third-vector", 3)
            services.global_instances.current_blob_storage = blob_storage
            services.global_instances.current_library_path = library_root

            errors = viewer_service.delete_stickers(
                [self.second, self.third]
            )

            recycler = library_root / "recycler"
            self.assertEqual((), errors)
            self.assertFalse(blob_storage.exists(entities[0]))
            self.assertFalse(blob_storage.exists(entities[1]))
            self.assertEqual(
                {
                    f"{self.second.hash}.png",
                    f"{self.second.hash}.json",
                    f"{self.third.hash}.png",
                    f"{self.third.hash}.json",
                },
                {path.name for path in recycler.iterdir()},
            )

        self.assertEqual([self.second, self.third], self.db.deleted)
        self.assertEqual(
            ["second-vector", "third-vector"],
            self.vector_store.deleted,
        )


if __name__ == "__main__":
    unittest.main()
