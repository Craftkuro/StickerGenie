# coding=utf-8
import datetime
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import event

from commons.dto import StickerImage, Tag
from stickerdb.v1.sticker_db import StickerDBV1

EXPECTED_INDEXES = {
    "ix_sticker_images_imported_at_id",
    "ix_sticker_images_modification_date_id",
    "ix_sticker_images_original_file_name_id",
    "ix_sticker_images_file_size_id",
    "ix_tag_assoc_sticker_id",
    "ix_tag_assoc_tag_id",
}


def make_tag(name: str) -> Tag:
    tag = Tag()
    tag.name = name
    tag.enabled = True
    tag.color_rgb = "#2196F3"
    return tag


def make_sticker(
    hash_value: str,
    *,
    tags=None,
    file_name: str = "sticker.png",
) -> StickerImage:
    sticker = StickerImage()
    sticker.original_file_name = file_name
    sticker.file_size = 1
    sticker.hash = hash_value
    sticker.extension = ".png"
    sticker.imported_at = datetime.datetime(2026, 1, 1)
    sticker.modification_date = datetime.datetime(2026, 1, 1)
    sticker.size_width = 1
    sticker.size_height = 1
    sticker.vectordb_id = None
    sticker.text_in_image = None
    sticker.tags = list(tags or [])
    return sticker


def _index_names(db_path: str) -> set[str]:
    connection = sqlite3.connect(db_path)
    try:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    finally:
        connection.close()


class StickerDBIndexTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._temp_dir.name) / "library.db")

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_new_database_creates_query_indexes(self):
        db = StickerDBV1(self.db_path)
        self.assertTrue(EXPECTED_INDEXES.issubset(_index_names(self.db_path)))
        db.engine.dispose()

    def test_existing_database_gets_missing_indexes(self):
        # 模拟索引加入前创建的旧库：表已存在但没有索引。
        self._create_legacy_schema()
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            "INSERT INTO sticker_images "
            "(original_file_name, file_size, hash, extension, "
            "imported_at, modification_date, size_width, size_height) "
            "VALUES ('a.png', 1, 'legacy-hash', '.png', "
            "'2026-01-01 00:00:00', '2026-01-01 00:00:00', 1, 1)"
        )
        connection.commit()
        connection.close()

        db = StickerDBV1(self.db_path)
        self.assertTrue(EXPECTED_INDEXES.issubset(_index_names(self.db_path)))
        self.assertEqual(1, len(db.list_stickers()))
        db.engine.dispose()

    def test_list_stickers_loads_tags_without_n_plus_one(self):
        db = StickerDBV1(self.db_path)
        tags = [db.add_or_modify_tag(make_tag(f"Tag {i}")) for i in range(3)]
        stickers = [
            make_sticker(f"hash-{i}", tags=tags, file_name=f"f{i}.png")
            for i in range(10)
        ]
        db.add_stickers(stickers)

        query_count = 0

        def _count(*_args, **_kwargs):
            nonlocal query_count
            query_count += 1

        event.listen(db.engine, "before_cursor_execute", _count)
        try:
            page = db.list_stickers(count=10)
        finally:
            event.remove(db.engine, "before_cursor_execute", _count)
        db.engine.dispose()

        self.assertEqual(10, len(page))
        self.assertTrue(all(sticker.tags for sticker in page))
        # 分页 SELECT 1 次 + tags selectin 1 次，不再有逐条标签查询。
        self.assertEqual(2, query_count)

    def _create_legacy_schema(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executescript(
                """
                CREATE TABLE sticker_images (
                    id INTEGER NOT NULL,
                    original_file_name VARCHAR NOT NULL,
                    file_size INTEGER NOT NULL,
                    hash VARCHAR NOT NULL,
                    extension VARCHAR NOT NULL,
                    imported_at DATETIME NOT NULL,
                    modification_date DATETIME NOT NULL,
                    size_width INTEGER NOT NULL,
                    size_height INTEGER NOT NULL,
                    vectordb_id VARCHAR,
                    text_in_image TEXT,
                    PRIMARY KEY (id),
                    UNIQUE (hash)
                );
                CREATE TABLE tags (
                    id INTEGER NOT NULL,
                    name VARCHAR NOT NULL,
                    description VARCHAR,
                    enabled BOOLEAN NOT NULL,
                    color_rgb VARCHAR NOT NULL,
                    "order" INTEGER NOT NULL,
                    PRIMARY KEY (id),
                    UNIQUE (name)
                );
                CREATE TABLE tag_assoc (
                    sticker_id INTEGER,
                    tag_id INTEGER,
                    FOREIGN KEY(sticker_id) REFERENCES sticker_images (id),
                    FOREIGN KEY(tag_id) REFERENCES tags (id)
                );
                """
            )
            connection.commit()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
