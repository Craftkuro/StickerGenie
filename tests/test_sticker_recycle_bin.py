import datetime
import json
import tempfile
import unittest
from pathlib import Path

import services.global_instances
from blob_storage import BlobStorage
from commons.dto import StickerImage, Tag
from services.sticker_recycle_bin import RECYCLER_DIR_NAME, stash_sticker


def make_sticker(sticker_id: int = 1) -> StickerImage:
    sticker = StickerImage()
    sticker.id = sticker_id
    sticker.original_file_name = "原始名称.png"
    sticker.file_size = 1
    sticker.hash = f"{sticker_id:040d}"
    sticker.extension = ".png"
    sticker.imported_at = datetime.datetime(2026, 8, 9, 9, 0, 0)
    sticker.modification_date = datetime.datetime(2026, 8, 8, 12, 34, 56)
    sticker.size_width = 1
    sticker.size_height = 1
    sticker.vectordb_id = None
    sticker.text_in_image = None
    return sticker


class StickerRecycleBinTests(unittest.TestCase):
    def setUp(self):
        self._old_library_path = (
            services.global_instances.current_library_path
        )
        self._old_blob = services.global_instances.current_blob_storage
        self.addCleanup(self._restore_globals)

        # Blob 根为 <tmp>/library/blob，回收站落在 <tmp>/library/recycler。
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.library_root = Path(self._tmp.name) / "library"
        services.global_instances.current_library_path = self.library_root
        self.blob_storage = BlobStorage(str(self.library_root / "blob"))
        services.global_instances.current_blob_storage = self.blob_storage

    def _restore_globals(self):
        services.global_instances.current_library_path = (
            self._old_library_path
        )
        services.global_instances.current_blob_storage = self._old_blob

    def _store_blob(self, sticker: StickerImage, content: bytes = b"image"):
        source = Path(self._tmp.name) / f"src-{sticker.id}{sticker.extension}"
        source.write_bytes(content)
        return self.blob_storage.store_file(str(source), sticker.hash)

    def test_stash_moves_blob_and_writes_sidecar_metadata(self):
        sticker = make_sticker()
        sticker.original_file_name = "cat.png"
        sticker.text_in_image = "hello"
        tag = Tag()
        tag.name = "动物"
        sticker.tags.append(tag)
        entity = self._store_blob(sticker, b"png-bytes")

        stash_sticker(sticker)

        recycler = self.library_root / RECYCLER_DIR_NAME
        self.assertFalse(self.blob_storage.exists(entity))
        stashed = recycler / f"{sticker.hash}{sticker.extension}"
        self.assertEqual(b"png-bytes", stashed.read_bytes())

        payload = json.loads(
            (recycler / f"{sticker.hash}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(1, payload["schema_version"])
        self.assertEqual("cat.png", payload["original_file_name"])
        self.assertEqual(".png", payload["extension"])
        self.assertEqual("hello", payload["text_in_image"])
        self.assertEqual(["动物"], payload["tags"])
        self.assertEqual("2026-08-09T09:00:00", payload["imported_at"])
        self.assertEqual("2026-08-08T12:34:56", payload["modification_date"])
        # deleted_at 是可解析的 ISO 时间戳。
        datetime.datetime.fromisoformat(payload["deleted_at"])

    def test_stash_same_hash_twice_keeps_single_pair_latest_wins(self):
        first = make_sticker()
        self._store_blob(first)
        stash_sticker(first)

        second = make_sticker()
        second.original_file_name = "renamed.png"
        self._store_blob(second)
        stash_sticker(second)

        recycler = self.library_root / RECYCLER_DIR_NAME
        entries = sorted(path.name for path in recycler.iterdir())
        self.assertEqual(
            [f"{first.hash}.json", f"{first.hash}.png"], entries
        )
        payload = json.loads(
            (recycler / f"{first.hash}.json").read_text(encoding="utf-8")
        )
        self.assertEqual("renamed.png", payload["original_file_name"])

    def test_missing_blob_is_silent_noop(self):
        sticker = make_sticker()

        stash_sticker(sticker)

        self.assertFalse((self.library_root / RECYCLER_DIR_NAME).exists())

    def test_uninitialized_library_raises(self):
        services.global_instances.current_library_path = None

        with self.assertRaisesRegex(RuntimeError, "图库尚未初始化"):
            stash_sticker(make_sticker())


if __name__ == "__main__":
    unittest.main()
