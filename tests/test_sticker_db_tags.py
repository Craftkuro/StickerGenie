import datetime
import tempfile
import unittest
from pathlib import Path

from commons.dto import StickerImage, Tag
from stickerdb.v1.sticker_db import StickerDBV1


def make_tag(name: str, *, enabled: bool = True, color: str = "#2196F3") -> Tag:
    tag = Tag()
    tag.name = name
    tag.enabled = enabled
    tag.color_rgb = color
    return tag


def make_sticker(tags=None) -> StickerImage:
    sticker = StickerImage()
    sticker.original_file_name = "sticker.png"
    sticker.relative_path = "sticker.png"
    sticker.file_size = 1
    sticker.hash = "test-hash"
    sticker.extension = ".png"
    sticker.imported_at = datetime.datetime(2026, 1, 1)
    sticker.modification_date = datetime.datetime(2026, 1, 1)
    sticker.size_width = 1
    sticker.size_height = 1
    sticker.vectordb_id = None
    sticker.text_in_image = None
    sticker.tags = list(tags or [])
    return sticker


class StickerDBTagTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db = StickerDBV1(str(Path(self._temp_dir.name) / "library.db"))

    def tearDown(self):
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def test_list_tags_filters_and_sorts(self):
        self.db.add_or_modify_tag(make_tag("Zulu"))
        self.db.add_or_modify_tag(make_tag("Alpha", enabled=False))

        self.assertEqual(["Alpha", "Zulu"], [tag.name for tag in self.db.list_tags()])
        self.assertEqual(["Zulu"], [tag.name for tag in self.db.list_tags(enabled_only=True)])

    def test_set_sticker_tags_replaces_and_clears_associations(self):
        first = self.db.add_or_modify_tag(make_tag("First", color="#112233"))
        second = self.db.add_or_modify_tag(make_tag("Second", color="#445566"))
        self.db.add_stickers([make_sticker([first])])
        sticker = self.db.list_stickers()[0]

        updated = self.db.set_sticker_tags(sticker.id, [second.id, first.id, second.id])
        self.assertEqual({first.id, second.id}, {tag.id for tag in updated.tags})

        cleared = self.db.set_sticker_tags(sticker.id, [])
        self.assertEqual([], cleared.tags)
        self.assertEqual([], self.db.list_stickers()[0].tags)

    def test_set_sticker_tags_rejects_missing_tag_without_partial_update(self):
        first = self.db.add_or_modify_tag(make_tag("First"))
        self.db.add_stickers([make_sticker([first])])
        sticker = self.db.list_stickers()[0]

        with self.assertRaises(ValueError):
            self.db.set_sticker_tags(sticker.id, [first.id, 999999])

        self.assertEqual([first.id], [tag.id for tag in self.db.list_stickers()[0].tags])

    def test_modify_stickers_can_clear_all_tags(self):
        first = self.db.add_or_modify_tag(make_tag("First"))
        self.db.add_stickers([make_sticker([first])])
        sticker = self.db.list_stickers()[0]
        sticker.tags = []

        self.db.modify_stickers([sticker])

        self.assertEqual([], self.db.list_stickers()[0].tags)


if __name__ == "__main__":
    unittest.main()
