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


def make_sticker(
    tags=None,
    *,
    file_name: str = "sticker.png",
    hash_value: str = "test-hash",
    text_in_image: str | None = None,
    modification_date: datetime.datetime | None = None,
) -> StickerImage:
    sticker = StickerImage()
    sticker.original_file_name = file_name
    sticker.relative_path = file_name
    sticker.file_size = 1
    sticker.hash = hash_value
    sticker.extension = ".png"
    sticker.imported_at = datetime.datetime(2026, 1, 1)
    sticker.modification_date = modification_date or datetime.datetime(2026, 1, 1)
    sticker.size_width = 1
    sticker.size_height = 1
    sticker.vectordb_id = None
    sticker.text_in_image = text_in_image
    sticker.tags = list(tags or [])
    return sticker


class StickerDBTagTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db = StickerDBV1(str(Path(self._temp_dir.name) / "library.db"))

    def tearDown(self):
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def test_list_tags_filters_and_sorts_by_insertion_order(self):
        zulu = self.db.add_or_modify_tag(make_tag("Zulu"))
        alpha = self.db.add_or_modify_tag(make_tag("Alpha", enabled=False))

        self.assertEqual([0, 1], [zulu.order, alpha.order])
        self.assertEqual(["Zulu", "Alpha"], [tag.name for tag in self.db.list_tags()])
        self.assertEqual(["Zulu"], [tag.name for tag in self.db.list_tags(enabled_only=True)])

    def test_name_based_tag_update_preserves_order(self):
        self.db.add_or_modify_tag(make_tag("First"))
        second = self.db.add_or_modify_tag(make_tag("Second"))

        updated = make_tag("Second", color="#112233")
        stored = self.db.add_or_modify_tag(updated)

        self.assertEqual(second.order, stored.order)
        self.assertEqual("#112233", stored.color_rgb)

    def test_implicit_tag_creation_uses_next_order(self):
        self.db.add_or_modify_tag(make_tag("First"))
        second = make_tag("Second")
        sticker = self.db.add_stickers(
            [make_sticker([second], hash_value="implicit-hash")]
        )[0]
        third = make_tag("Third")
        sticker.tags = [second, third]

        self.db.modify_stickers([sticker])

        stored_tags = self.db.list_tags()
        self.assertEqual(
            [("First", 0), ("Second", 1), ("Third", 2)],
            [(tag.name, tag.order) for tag in stored_tags],
        )

    def test_search_tags_uses_substring_order_limit_and_enabled_filter(self):
        self.db.add_or_modify_tag(make_tag("Zulu match"))
        self.db.add_or_modify_tag(make_tag("Alpha match"))
        self.db.add_or_modify_tag(make_tag("Disabled match", enabled=False))

        tags = self.db.search_tags("match", limit=1)

        self.assertEqual(["Zulu match"], [tag.name for tag in tags])

    def test_search_stickers_by_tag_deduplicates_and_sorts_newest_first(self):
        happy = self.db.add_or_modify_tag(make_tag("Happy"))
        happier = self.db.add_or_modify_tag(make_tag("Happier"))
        older = make_sticker(
            [happy, happier],
            file_name="older.png",
            hash_value="older-hash",
            modification_date=datetime.datetime(2026, 1, 1),
        )
        newer = make_sticker(
            [happy],
            file_name="newer.png",
            hash_value="newer-hash",
            modification_date=datetime.datetime(2026, 1, 2),
        )
        self.db.add_stickers([older, newer])

        results = self.db.search_stickers_by_tag("Happ")

        self.assertEqual(
            ["newer.png", "older.png"],
            [sticker.original_file_name for sticker in results],
        )

    def test_search_stickers_by_text_uses_literal_substring(self):
        matching = make_sticker(
            file_name="matching.png",
            hash_value="matching-hash",
            text_in_image="进度 100% 完成",
        )
        non_matching = make_sticker(
            file_name="other.png",
            hash_value="other-hash",
            text_in_image="进度 1000 完成",
        )
        self.db.add_stickers([matching, non_matching])

        results = self.db.search_stickers_by_text("100%")

        self.assertEqual(
            ["matching.png"],
            [sticker.original_file_name for sticker in results],
        )

    def test_add_stickers_silently_ignores_duplicate_hashes(self):
        first = make_sticker()
        duplicate = make_sticker()
        duplicate.original_file_name = "duplicate-name.png"

        inserted = self.db.add_stickers([first, duplicate])
        inserted_again = self.db.add_stickers([duplicate])

        self.assertEqual([first], inserted)
        self.assertEqual([], inserted_again)
        self.assertEqual(1, len(self.db.list_stickers()))

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
