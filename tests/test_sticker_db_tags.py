import datetime
import tempfile
import unittest
from pathlib import Path

from commons.dto import StickerImage, Tag
from stickerdb.v1.sticker_db import StickerDBV1, TagSearchExpressionError


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
    file_size: int = 1,
    imported_at: datetime.datetime | None = None,
) -> StickerImage:
    sticker = StickerImage()
    sticker.original_file_name = file_name
    sticker.relative_path = file_name
    sticker.file_size = file_size
    sticker.hash = hash_value
    sticker.extension = ".png"
    sticker.imported_at = imported_at or datetime.datetime(2026, 1, 1)
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

        self.assertEqual([0, 0], [zulu.order, alpha.order])
        self.assertEqual(["Zulu", "Alpha"], [tag.name for tag in self.db.list_tags()])
        self.assertEqual(["Zulu"], [tag.name for tag in self.db.list_tags(enabled_only=True)])

    def test_list_tags_sorts_by_order_then_id(self):
        later_order = self.db.add_or_modify_tag(make_tag("Later order"))
        first_tied = self.db.add_or_modify_tag(make_tag("First tied"))
        second_tied = self.db.add_or_modify_tag(make_tag("Second tied"))

        later_order.order = 5
        first_tied.order = 2
        second_tied.order = 2
        self.db.add_or_modify_tag(later_order)
        self.db.add_or_modify_tag(first_tied)
        self.db.add_or_modify_tag(second_tied)

        self.assertEqual(
            ["First tied", "Second tied", "Later order"],
            [tag.name for tag in self.db.list_tags()],
        )

    def test_name_based_tag_update_preserves_order(self):
        self.db.add_or_modify_tag(make_tag("First"))
        second = self.db.add_or_modify_tag(make_tag("Second"))

        updated = make_tag("Second", color="#112233")
        stored = self.db.add_or_modify_tag(updated)

        self.assertEqual(second.order, stored.order)
        self.assertEqual("#112233", stored.color_rgb)

    def test_implicit_tag_creation_uses_current_max_order(self):
        first = self.db.add_or_modify_tag(make_tag("First"))
        first.order = 4
        self.db.add_or_modify_tag(first)
        second = make_tag("Second")
        sticker = self.db.add_stickers(
            [make_sticker([second], hash_value="implicit-hash")]
        )[0]
        third = make_tag("Third")
        sticker.tags = [second, third]

        self.db.modify_stickers([sticker])

        stored_tags = self.db.list_tags()
        self.assertEqual(
            [("First", 4), ("Second", 4), ("Third", 4)],
            [(tag.name, tag.order) for tag in stored_tags],
        )

    def test_search_tags_uses_substring_order_id_limit_and_enabled_filter(self):
        zulu = self.db.add_or_modify_tag(make_tag("Zulu match"))
        alpha = self.db.add_or_modify_tag(make_tag("Alpha match"))
        beta = self.db.add_or_modify_tag(make_tag("Beta match"))
        disabled = self.db.add_or_modify_tag(
            make_tag("Disabled match", enabled=False)
        )

        zulu.order = 2
        alpha.order = 1
        beta.order = 1
        disabled.order = 0
        self.db.add_or_modify_tag(zulu)
        self.db.add_or_modify_tag(alpha)
        self.db.add_or_modify_tag(beta)
        self.db.add_or_modify_tag(disabled)

        tags = self.db.search_tags("match", limit=2)

        self.assertEqual(
            ["Alpha match", "Beta match"],
            [tag.name for tag in tags],
        )

    def test_sticker_export_sorts_tags_by_order_then_id(self):
        later_order = self.db.add_or_modify_tag(make_tag("Later order"))
        first_tied = self.db.add_or_modify_tag(make_tag("First tied"))
        second_tied = self.db.add_or_modify_tag(make_tag("Second tied"))

        later_order.order = 5
        first_tied.order = 2
        second_tied.order = 2
        self.db.add_or_modify_tag(later_order)
        self.db.add_or_modify_tag(first_tied)
        self.db.add_or_modify_tag(second_tied)
        self.db.add_stickers(
            [
                make_sticker(
                    [later_order, second_tied, first_tied],
                    hash_value="ordered-tags-hash",
                )
            ]
        )

        sticker = self.db.list_stickers()[0]

        self.assertEqual(
            ["First tied", "Second tied", "Later order"],
            [tag.name for tag in sticker.tags],
        )

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

    def test_search_stickers_by_tag_expression_uses_exact_enabled_tags(self):
        alpha = self.db.add_or_modify_tag(make_tag("Alpha"))
        alphabet = self.db.add_or_modify_tag(make_tag("Alphabet"))
        self.db.add_stickers(
            [
                make_sticker([alpha], hash_value="alpha-hash", file_name="alpha.png"),
                make_sticker(
                    [alphabet],
                    hash_value="alphabet-hash",
                    file_name="alphabet.png",
                ),
            ]
        )

        results = self.db.search_stickers_by_tag_expression("Alpha")

        self.assertEqual(["alpha.png"], [sticker.original_file_name for sticker in results])

    def test_search_stickers_by_tag_expression_compiles_boolean_logic(self):
        a = self.db.add_or_modify_tag(make_tag("A"))
        b = self.db.add_or_modify_tag(make_tag("B"))
        c = self.db.add_or_modify_tag(make_tag("C"))
        self.db.add_stickers(
            [
                make_sticker([a], hash_value="only-a", file_name="only-a.png"),
                make_sticker(
                    [a, b],
                    hash_value="a-b",
                    file_name="a-b.png",
                ),
                make_sticker([b], hash_value="only-b", file_name="only-b.png"),
                make_sticker(
                    [a, c],
                    hash_value="a-c",
                    file_name="a-c.png",
                ),
                make_sticker([], hash_value="no-tags", file_name="no-tags.png"),
            ]
        )

        names = lambda expression: {
            sticker.original_file_name
            for sticker in self.db.search_stickers_by_tag_expression(expression)
        }

        self.assertEqual({"a-b.png"}, names("A AND B"))
        self.assertEqual(
            {"only-a.png", "a-b.png", "only-b.png", "a-c.png"},
            names("A OR B"),
        )
        self.assertEqual(
            {"only-a.png", "a-b.png", "only-b.png", "no-tags.png"},
            names("NOT C"),
        )
        self.assertEqual(
            {"only-a.png", "a-b.png", "only-b.png"},
            names("(A OR B) AND NOT C"),
        )
        self.assertEqual({"no-tags.png"}, names("NOT (A OR B)"))

    def test_search_stickers_by_tag_expression_ignores_disabled_tags(self):
        disabled = self.db.add_or_modify_tag(make_tag("Hidden", enabled=False))
        self.db.add_stickers(
            [make_sticker([disabled], hash_value="hidden-hash", file_name="hidden.png")]
        )

        self.assertEqual([], self.db.search_stickers_by_tag_expression("Hidden"))
        self.assertEqual(
            ["hidden.png"],
            [
                sticker.original_file_name
                for sticker in self.db.search_stickers_by_tag_expression("NOT Hidden")
            ],
        )

    def test_search_stickers_by_tag_expression_handles_unknown_and_empty_queries(self):
        self.assertEqual([], self.db.search_stickers_by_tag_expression("Unknown"))
        self.assertEqual([], self.db.search_stickers_by_tag_expression(""))
        self.assertEqual(
            [],
            self.db.search_stickers_by_tag_expression("Unknown AND NOT Unknown"),
        )

        self.db.add_stickers(
            [make_sticker([], hash_value="empty-query-negative", file_name="empty.png")]
        )
        self.assertEqual(
            ["empty.png"],
            [
                sticker.original_file_name
                for sticker in self.db.search_stickers_by_tag_expression(
                    "NOT Unknown"
                )
            ],
        )

    def test_search_stickers_by_tag_expression_supports_quoted_tag_literals(self):
        parenthesized = self.db.add_or_modify_tag(make_tag("角色(作品)"))
        ampersand = self.db.add_or_modify_tag(make_tag("动作&喜剧"))
        quoted = self.db.add_or_modify_tag(make_tag('他说"好"'))
        self.db.add_stickers(
            [
                make_sticker(
                    [parenthesized],
                    hash_value="parenthesized-hash",
                    file_name="parenthesized.png",
                ),
                make_sticker(
                    [ampersand],
                    hash_value="ampersand-hash",
                    file_name="ampersand.png",
                ),
                make_sticker(
                    [quoted],
                    hash_value="quoted-hash",
                    file_name="quoted.png",
                ),
            ]
        )

        self.assertEqual(
            ["parenthesized.png"],
            [
                sticker.original_file_name
                for sticker in self.db.search_stickers_by_tag_expression(
                    '"角色(作品)"'
                )
            ],
        )
        self.assertEqual(
            ["ampersand.png"],
            [
                sticker.original_file_name
                for sticker in self.db.search_stickers_by_tag_expression(
                    '"动作&喜剧"'
                )
            ],
        )
        self.assertEqual(
            ["quoted.png"],
            [
                sticker.original_file_name
                for sticker in self.db.search_stickers_by_tag_expression(
                    '"他说""好"""'
                )
            ],
        )

    def test_search_stickers_by_tag_expression_rejects_syntax_errors_and_constants(self):
        for expression in ("A AND", "(A OR B", "A AND OR B", "TRUE", "None", "1"):
            with self.subTest(expression=expression):
                with self.assertRaises(TagSearchExpressionError):
                    self.db.search_stickers_by_tag_expression(expression)

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

    def test_search_stickers_by_file_name_uses_literal_substring(self):
        matching = make_sticker(
            file_name="vacation_100%.png",
            hash_value="file-name-matching-hash",
            modification_date=datetime.datetime(2026, 1, 2),
        )
        non_matching = make_sticker(
            file_name="vacation_1000.png",
            hash_value="file-name-other-hash",
            modification_date=datetime.datetime(2026, 1, 1),
        )
        self.db.add_stickers([non_matching, matching])

        results = self.db.search_stickers_by_file_name("100%")

        self.assertEqual(
            ["vacation_100%.png"],
            [sticker.original_file_name for sticker in results],
        )

    def test_search_stickers_by_file_name_sorts_newest_first(self):
        older = make_sticker(
            file_name="older.png",
            hash_value="file-name-older-hash",
            modification_date=datetime.datetime(2026, 1, 1),
        )
        newer = make_sticker(
            file_name="newer.png",
            hash_value="file-name-newer-hash",
            modification_date=datetime.datetime(2026, 1, 2),
        )
        self.db.add_stickers([older, newer])

        results = self.db.search_stickers_by_file_name(".png")

        self.assertEqual(
            ["newer.png", "older.png"],
            [sticker.original_file_name for sticker in results],
        )

    def test_list_stickers_sorts_by_imported_at_and_file_size(self):
        older = make_sticker(
            file_name="older.png",
            hash_value="older-hash",
            imported_at=datetime.datetime(2026, 1, 1),
            file_size=10,
        )
        newer = make_sticker(
            file_name="newer.png",
            hash_value="newer-hash",
            imported_at=datetime.datetime(2026, 1, 2),
            file_size=30,
        )
        tiny = make_sticker(
            file_name="tiny.png",
            hash_value="tiny-hash",
            imported_at=datetime.datetime(2026, 1, 1),
            file_size=5,
        )
        self.db.add_stickers([older, newer, tiny])

        by_import_date = self.db.list_stickers(
            order_by="imported_at",
            descending=True,
        )
        self.assertEqual(
            ["newer.png", "tiny.png", "older.png"],
            [sticker.original_file_name for sticker in by_import_date],
        )

        by_file_size = self.db.list_stickers(
            order_by="file_size",
            descending=False,
        )
        self.assertEqual(
            ["tiny.png", "older.png", "newer.png"],
            [sticker.original_file_name for sticker in by_file_size],
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

    def test_batch_edit_sticker_tags_adds_missing_tags_and_returns_fresh_dtos(self):
        first = self.db.add_or_modify_tag(make_tag("First"))
        second = self.db.add_or_modify_tag(make_tag("Second"))
        stickers = self.db.add_stickers(
            [
                make_sticker([first], hash_value="batch-one"),
                make_sticker([first, second], hash_value="batch-two"),
                make_sticker([], hash_value="batch-three"),
            ]
        )

        modified_count, updated = self.db.batch_edit_sticker_tags(
            [sticker.id for sticker in stickers],
            [first.id, second.id],
            add=True,
        )

        self.assertEqual(2, modified_count)
        self.assertEqual(3, len(updated))
        self.assertTrue(
            all(
                {tag.id for tag in sticker.tags} == {first.id, second.id}
                for sticker in updated
            )
        )

    def test_batch_edit_sticker_tags_removes_existing_tags_and_ignores_missing(self):
        first = self.db.add_or_modify_tag(make_tag("First"))
        second = self.db.add_or_modify_tag(make_tag("Second"))
        stickers = self.db.add_stickers(
            [
                make_sticker([first], hash_value="remove-one"),
                make_sticker([second], hash_value="remove-two"),
                make_sticker([], hash_value="remove-three"),
            ]
        )

        modified_count, updated = self.db.batch_edit_sticker_tags(
            [sticker.id for sticker in stickers],
            [first.id, second.id],
            add=False,
        )

        self.assertEqual(2, modified_count)
        self.assertEqual(3, len(updated))
        self.assertEqual(
            [[], [], []],
            [[tag.id for tag in sticker.tags] for sticker in updated],
        )

    def test_batch_edit_sticker_tags_rejects_missing_tag_without_partial_update(self):
        first = self.db.add_or_modify_tag(make_tag("First"))
        stickers = self.db.add_stickers(
            [make_sticker([first], hash_value="batch-missing-tag")]
        )

        with self.assertRaises(ValueError):
            self.db.batch_edit_sticker_tags(
                [stickers[0].id],
                [first.id, 999999],
                add=True,
            )

        self.assertEqual(
            [first.id],
            [tag.id for tag in self.db.list_stickers()[0].tags],
        )

    def test_modify_stickers_can_clear_all_tags(self):
        first = self.db.add_or_modify_tag(make_tag("First"))
        self.db.add_stickers([make_sticker([first])])
        sticker = self.db.list_stickers()[0]
        sticker.tags = []

        self.db.modify_stickers([sticker])

        self.assertEqual([], self.db.list_stickers()[0].tags)


if __name__ == "__main__":
    unittest.main()
