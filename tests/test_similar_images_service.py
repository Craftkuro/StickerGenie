import datetime
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import services.global_instances
import services.sticker_library_viewer_service as viewer_service
from blob_storage import BlobStorage
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

    def get(self, vector_id):
        if vector_id == "source-vector":
            return SimpleNamespace(id=vector_id)
        return None

    def get_by_sqlite_id(self, sticker_id):
        return None

    def search_by_id(self, vector_id, top_k):
        return self.results[:top_k]

    def delete(self, vector_id):
        self.deleted.append(vector_id)
        return True


class SimilarImagesServiceTests(unittest.TestCase):
    def setUp(self):
        self.source = make_sticker(1, "source.png", "source-vector")
        self.second = make_sticker(2, "second.png", "second-vector")
        self.third = make_sticker(3, "third.png", "third-vector")
        self.db = FakeDB([self.source, self.second, self.third])
        self.vector_store = FakeVectorStore(
            [
                SimpleNamespace(sqlite_id=3, similarity=0.95),
                SimpleNamespace(sqlite_id=999, similarity=0.90),
                SimpleNamespace(sqlite_id=2, similarity=0.80),
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
        self.assertEqual([0.95, 0.80], [similarity for _, similarity in matches])

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


if __name__ == "__main__":
    unittest.main()
