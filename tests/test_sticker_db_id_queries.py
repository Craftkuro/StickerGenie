import datetime
import tempfile
import unittest
from pathlib import Path

from commons.dto import StickerImage
from stickerdb.v1.sticker_db import StickerDBV1


def make_sticker(file_name: str) -> StickerImage:
    sticker = StickerImage()
    sticker.original_file_name = file_name
    sticker.relative_path = file_name
    sticker.file_size = 1
    sticker.hash = file_name
    sticker.extension = ".png"
    sticker.imported_at = datetime.datetime(2026, 1, 1)
    sticker.modification_date = datetime.datetime(2026, 1, 1)
    sticker.size_width = 1
    sticker.size_height = 1
    sticker.vectordb_id = None
    sticker.text_in_image = None
    return sticker


class StickerDBIdQueryTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db = StickerDBV1(str(Path(self._temp_dir.name) / "library.db"))

    def tearDown(self):
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def _add_ids(self, count: int) -> list[int]:
        added = self.db.add_stickers(
            [make_sticker(f"s{i}.png") for i in range(count)]
        )
        return [sticker.id for sticker in added]

    def test_empty_database_returns_none(self):
        self.assertIsNone(self.db.random_sticker_id())
        self.assertIsNone(self.db.random_sticker_id(excluding=7))
        self.assertIsNone(self.db.next_sticker_id(1))

    def test_random_id_only_returns_existing_rows(self):
        ids = set(self._add_ids(5))
        for _ in range(20):
            self.assertIn(self.db.random_sticker_id(), ids)

    def test_random_id_excluding_skips_given_row(self):
        ids = set(self._add_ids(5))
        for _ in range(20):
            sampled = self.db.random_sticker_id(excluding=max(ids))
            self.assertIn(sampled, ids - {max(ids)})

    def test_random_id_excluding_last_remaining_row_returns_none(self):
        only_id = self._add_ids(1)[0]
        self.assertIsNone(self.db.random_sticker_id(excluding=only_id))
        self.assertEqual(only_id, self.db.random_sticker_id())

    def test_next_id_skips_deleted_holes_and_wraps(self):
        all_ids = self._add_ids(5)
        min_id = min(all_ids)
        max_id = max(all_ids)
        holes = [all_ids[1], all_ids[3]]
        victims = [
            sticker
            for sticker in self.db.get_stickers_by_ids(holes)
        ]
        self.db.delete_stickers(victims)

        self.assertEqual(min_id + 2, self.db.next_sticker_id(min_id))
        self.assertEqual(max_id, self.db.next_sticker_id(max_id - 2))
        self.assertEqual(max_id, self.db.next_sticker_id(max_id - 1))
        self.assertEqual(min_id, self.db.next_sticker_id(max_id))
        self.assertEqual(min_id, self.db.next_sticker_id(max_id + 100))

    def test_next_id_with_single_row_wraps_to_itself(self):
        only_id = self._add_ids(1)[0]
        self.assertEqual(only_id, self.db.next_sticker_id(only_id))


if __name__ == "__main__":
    unittest.main()
