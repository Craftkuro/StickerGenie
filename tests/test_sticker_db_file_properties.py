import datetime
import tempfile
import unittest
from pathlib import Path

from commons.dto import StickerImage, Tag
from stickerdb.v1.sticker_db import StickerDBV1


def make_sticker(file_name: str = "a.png") -> StickerImage:
    sticker = StickerImage()
    sticker.original_file_name = file_name
    sticker.relative_path = file_name
    sticker.file_size = 123
    sticker.hash = "0" * 40
    sticker.extension = ".png"
    sticker.imported_at = datetime.datetime(2026, 1, 1, 8, 0, 0)
    sticker.modification_date = datetime.datetime(2025, 12, 31)
    sticker.size_width = 10
    sticker.size_height = 20
    sticker.vectordb_id = None
    sticker.text_in_image = None
    return sticker


class UpdateStickerFilePropertiesTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db = StickerDBV1(str(Path(self._temp_dir.name) / "library.db"))
        added = self.db.add_stickers([make_sticker()])
        self.sticker_id = added[0].id

    def tearDown(self):
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def test_updates_name_and_modification_date(self):
        updated = self.db.update_sticker_file_properties(
            self.sticker_id,
            original_file_name="  新名字.png  ",
            modification_date=datetime.datetime(2020, 5, 4, 12, 30),
        )

        self.assertEqual("新名字.png", updated.original_file_name)
        self.assertEqual(
            datetime.datetime(2020, 5, 4, 12, 30), updated.modification_date
        )

    def test_none_arguments_keep_existing_values(self):
        updated = self.db.update_sticker_file_properties(self.sticker_id)

        self.assertEqual("a.png", updated.original_file_name)
        self.assertEqual(datetime.datetime(2025, 12, 31), updated.modification_date)

    def test_other_fields_and_persistence_untouched(self):
        self.db.update_sticker_file_properties(
            self.sticker_id,
            original_file_name="renamed.png",
        )

        fetched = self.db.get_stickers_by_ids([self.sticker_id])[0]
        self.assertEqual("renamed.png", fetched.original_file_name)
        self.assertEqual("0" * 40, fetched.hash)
        self.assertEqual(".png", fetched.extension)
        self.assertEqual(123, fetched.file_size)
        self.assertEqual(10, fetched.size_width)

    def test_tags_survive_property_update(self):
        tag = Tag()
        tag.name = "测试标签"
        tag = self.db.add_or_modify_tag(tag)
        self.db.set_sticker_tags(self.sticker_id, [tag.id])

        self.db.update_sticker_file_properties(
            self.sticker_id,
            original_file_name="renamed.png",
        )

        fetched = self.db.get_stickers_by_ids([self.sticker_id])[0]
        self.assertEqual(["测试标签"], [t.name for t in fetched.tags])

    def test_blank_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "文件名不能为空"):
            self.db.update_sticker_file_properties(
                self.sticker_id, original_file_name="   "
            )

    def test_missing_sticker_raises(self):
        with self.assertRaisesRegex(ValueError, "不存在的表情包"):
            self.db.update_sticker_file_properties(
                9999, original_file_name="whatever.png"
            )

    def test_missing_extension_gets_actual_extension_appended(self):
        updated = self.db.update_sticker_file_properties(
            self.sticker_id, original_file_name="新名字"
        )
        self.assertEqual("新名字.png", updated.original_file_name)

    def test_typed_extension_is_normalized_to_actual_type(self):
        # 大小写差异归一。
        updated = self.db.update_sticker_file_properties(
            self.sticker_id, original_file_name="新名字.PNG"
        )
        self.assertEqual("新名字.png", updated.original_file_name)

        # 与实际类型不符的后缀不能冒充扩展名：保留并追加真实扩展名，
        # 避免导出产物名实不符。
        updated = self.db.update_sticker_file_properties(
            self.sticker_id, original_file_name="新名字.gif"
        )
        self.assertEqual("新名字.gif.png", updated.original_file_name)

    def test_dots_inside_base_name_are_preserved(self):
        updated = self.db.update_sticker_file_properties(
            self.sticker_id, original_file_name="2026.08.23 猫"
        )
        self.assertEqual("2026.08.23 猫.png", updated.original_file_name)

    def test_trailing_dots_and_spaces_are_stripped(self):
        updated = self.db.update_sticker_file_properties(
            self.sticker_id, original_file_name="cat.  . "
        )
        self.assertEqual("cat.png", updated.original_file_name)

    def test_name_consisting_only_of_extension_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "文件名不能为空"):
            self.db.update_sticker_file_properties(
                self.sticker_id, original_file_name=".png"
            )

    def test_unsafe_names_are_rejected(self):
        for bad_name in ("a/b.png", "a\\b.png", "..", "C:evil.png"):
            with self.assertRaisesRegex(ValueError, "文件名包含系统不支持的字符"):
                self.db.update_sticker_file_properties(
                    self.sticker_id, original_file_name=bad_name
                )


if __name__ == "__main__":
    unittest.main()
