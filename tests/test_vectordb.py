import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from pathlib import Path

import numpy as np

from stickerdb.vectordb import (
    ChromaDBConfig,
    ChromaVectorStore,
    VectorDBConnectionError,
    VectorMetadata,
)


def make_vector(index: int, *, dimension: int = 768) -> np.ndarray:
    vector = np.zeros(dimension, dtype=np.float32)
    vector[index] = 1.0
    return vector


def make_metadata(sqlite_id: int, filename: str | None = None) -> VectorMetadata:
    return VectorMetadata(
        image_filename=filename or f"sticker-{sqlite_id}.png",
        model_hash="test-model",
        sqlite_id=sqlite_id,
        extraction_timestamp=time.time(),
        image_width=64,
        image_height=64,
    )


def get_collection_configuration(store: ChromaVectorStore) -> dict:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="legacy embedding function config",
            category=DeprecationWarning,
        )
        return store._collection.configuration


class ChromaVectorStoreTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.persist_directory = Path(self._temp_dir.name) / "vectors"
        self.store = ChromaVectorStore(str(self.persist_directory))
        self.store.initialize()

    def tearDown(self):
        self.store.close()
        self._temp_dir.cleanup()

    def test_initializes_persistent_collection_with_modern_configuration(self):
        self.assertTrue((self.persist_directory / "chroma.sqlite3").is_file())

        configuration = get_collection_configuration(self.store)
        self.assertIsNone(configuration["embedding_function"])
        self.assertEqual("cosine", configuration["hnsw"]["space"])
        self.assertEqual(100, configuration["hnsw"]["ef_construction"])
        self.assertEqual(100, configuration["hnsw"]["ef_search"])
        self.assertEqual(16, configuration["hnsw"]["max_neighbors"])

    def test_initialize_is_idempotent(self):
        client = self.store._client
        collection = self.store._collection

        self.store.initialize()

        self.assertIs(client, self.store._client)
        self.assertIs(collection, self.store._collection)

    def test_add_get_update_and_query_by_integer_sqlite_id(self):
        vector_id = self.store.add(make_vector(0), make_metadata(1))

        record = self.store.get(vector_id)
        self.assertIsNotNone(record)
        self.assertEqual(1, record.metadata.sqlite_id)
        np.testing.assert_array_equal(make_vector(0), record.vector)
        self.assertTrue(self.store.exists(vector_id))
        self.assertEqual(vector_id, self.store.get_by_sqlite_id(1).id)

        updated_metadata = make_metadata(1, "updated.png")
        self.assertTrue(
            self.store.update(vector_id, make_vector(1), updated_metadata)
        )

        updated = self.store.get(vector_id)
        self.assertEqual("updated.png", updated.metadata.image_filename)
        np.testing.assert_array_equal(make_vector(1), updated.vector)

        records = self.store.query_by_metadata({"sqlite_id": 1})
        self.assertEqual([vector_id], [record.id for record in records])

    def test_batch_delete_counts_only_existing_records(self):
        vector_ids = self.store.add_batch(
            [make_vector(0), make_vector(1), make_vector(2)],
            [make_metadata(1), make_metadata(2), make_metadata(3)],
        )

        deleted = self.store.delete_batch(
            [vector_ids[0], vector_ids[0], vector_ids[2], "missing"]
        )

        self.assertEqual(2, deleted)
        self.assertEqual(1, self.store.count())
        self.assertFalse(self.store.delete("missing"))
        self.assertTrue(self.store.delete(vector_ids[1]))
        self.assertEqual(0, self.store.count())

    def test_batch_delete_splits_requests_over_chroma_max_batch_size(self):
        from unittest.mock import MagicMock

        fake_collection = MagicMock()
        fake_collection.get.return_value = {
            "ids": [f"id-{index}" for index in range(6000)]
        }
        original_collection = self.store._collection
        self.store._collection = fake_collection
        try:
            deleted = self.store.delete_batch(
                [f"id-{index}" for index in range(6000)]
            )
        finally:
            self.store._collection = original_collection

        self.assertEqual(6000, deleted)
        batch_sizes = [
            len(call.kwargs["ids"])
            for call in fake_collection.delete.call_args_list
        ]
        self.assertEqual([5000, 1000], batch_sizes)

    def test_find_ids_by_sqlite_ids_maps_only_existing_records(self):
        vector_ids = self.store.add_batch(
            [make_vector(0), make_vector(1)],
            [make_metadata(1), make_metadata(2)],
        )

        mapping = self.store.find_ids_by_sqlite_ids([1, 2, 999])

        self.assertEqual(
            {1: vector_ids[0], 2: vector_ids[1]},
            mapping,
        )
        self.assertEqual({}, self.store.find_ids_by_sqlite_ids([]))

    def test_search_by_id_includes_the_requested_record_as_reference(self):
        base = make_vector(0)
        nearby = make_vector(0)
        nearby[1] = 0.1
        distant = make_vector(1)

        vector_ids = self.store.add_batch(
            [base, nearby, distant],
            [make_metadata(1), make_metadata(2), make_metadata(3)],
        )

        results = self.store.search_by_id(vector_ids[0], top_k=2)

        self.assertEqual(2, len(results))
        self.assertEqual(
            [vector_ids[0], vector_ids[1]],
            [result.id for result in results],
        )

    def test_search_by_id_only_returns_records_from_the_same_model(self):
        base = make_vector(0)
        nearby = make_vector(0)
        nearby[1] = 0.1
        other_model = make_vector(0)
        other_model[1] = 0.05

        other_metadata = make_metadata(3)
        other_metadata.model_hash = "other-model"
        vector_ids = self.store.add_batch(
            [base, nearby, other_model],
            [make_metadata(1), make_metadata(2), other_metadata],
        )

        results = self.store.search_by_id(vector_ids[0], top_k=2)

        self.assertEqual(
            [vector_ids[0], vector_ids[1]],
            [result.id for result in results],
        )
        self.assertNotIn(vector_ids[2], [result.id for result in results])

    def test_reset_recreates_collection_with_the_same_configuration(self):
        self.store.add(make_vector(0), make_metadata(1))

        self.store.reset()

        self.assertEqual(0, self.store.count())
        configuration = get_collection_configuration(self.store)
        self.assertIsNone(configuration["embedding_function"])
        self.assertEqual("cosine", configuration["hnsw"]["space"])

    def test_reset_respects_allow_reset(self):
        self.store.close()
        self._temp_dir.cleanup()

        self._temp_dir = tempfile.TemporaryDirectory()
        self.persist_directory = Path(self._temp_dir.name) / "vectors"
        self.store = ChromaVectorStore(
            str(self.persist_directory),
            ChromaDBConfig(allow_reset=False),
        )
        self.store.initialize()

        with self.assertRaises(VectorDBConnectionError):
            self.store.reset()

    def test_data_is_visible_from_an_independent_process(self):
        vector_id = self.store.add(make_vector(0), make_metadata(1))
        self.store.close()

        probe = """
import chromadb
import sys

client = chromadb.PersistentClient(path=sys.argv[1])
collection = client.get_collection(sys.argv[2])
result = collection.get(ids=[sys.argv[3]])
print(collection.count())
print(result["metadatas"][0]["sqlite_id"])
client.close()
"""
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                probe,
                str(self.persist_directory),
                self.store.config.collection_name,
                vector_id,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(["1", "1"], completed.stdout.strip().splitlines())

        self.store = ChromaVectorStore(str(self.persist_directory))
        self.store.initialize()
        self.assertEqual(1, self.store.count())
        self.assertEqual(vector_id, self.store.get_by_sqlite_id(1).id)


if __name__ == "__main__":
    unittest.main()
