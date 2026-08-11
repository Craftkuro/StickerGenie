import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

import apppath
from commons.dto import StickerImage, Tag
from stickerdb.v1.sticker_db import StickerDBV1
from ui.dialog_image_viewer import ImageViewerDialog, TAG_DATA_ROLE
from ui.widgets.custom_tag_widget import TAG_ACCENT_COLOR_ROLE


def make_tag(name: str, color: str, *, enabled: bool = True) -> Tag:
    tag = Tag()
    tag.name = name
    tag.color_rgb = color
    tag.enabled = enabled
    return tag


def make_sticker(tag: Tag) -> StickerImage:
    sticker = StickerImage()
    sticker.original_file_name = "viewer.png"
    sticker.relative_path = "viewer.png"
    sticker.file_size = 1
    sticker.hash = "viewer-test-hash"
    sticker.extension = ".png"
    sticker.imported_at = datetime.datetime(2026, 1, 1)
    sticker.modification_date = datetime.datetime(2026, 1, 1)
    sticker.size_width = 1
    sticker.size_height = 1
    sticker.vectordb_id = None
    sticker.text_in_image = None
    sticker.tags = [tag]
    return sticker


class ImageViewerTagEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        source_dir = Path(__file__).resolve().parents[1] / "src"
        apppath.setup_data_path(source_dir)

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db = StickerDBV1(str(Path(self._temp_dir.name) / "library.db"))
        self.first = self.db.add_or_modify_tag(make_tag("First", "#123456"))
        self.second = self.db.add_or_modify_tag(make_tag("Second", "#654321"))
        self.db.add_stickers([make_sticker(self.first)])
        self.sticker = self.db.list_stickers()[0]

        self.image_path = str(Path(self._temp_dir.name) / "viewer.png")
        image = QImage(2, 2, QImage.Format.Format_ARGB32)
        image.fill(0xFFFFFFFF)
        self.assertTrue(image.save(self.image_path))

        self.dialog = ImageViewerDialog(database=self.db)
        self.dialog.load_image(self.image_path, "viewer.png", self.sticker)

    def tearDown(self):
        self.dialog.close()
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def test_loads_tags_and_uses_tag_accent_color(self):
        self.assertFalse(self.dialog.widgetTagEditor.isHidden())
        self.assertEqual(1, self.dialog._tag_model.rowCount())
        item = self.dialog._tag_model.item(0)
        self.assertEqual(self.first.id, item.data(TAG_DATA_ROLE).id)
        self.assertEqual("#123456", item.data(TAG_ACCENT_COLOR_ROLE))

    def test_supports_maximization(self):
        self.assertTrue(
            self.dialog.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
        )

    def test_loads_file_information(self):
        table = self.dialog.tableWidgetFileInfo
        values = {
            table.item(row, 0).text(): table.item(row, 1).text()
            for row in range(table.rowCount())
        }

        self.assertEqual("viewer.png", values["文件名"])
        self.assertEqual(str(Path(self.image_path).resolve()), values["文件路径"])
        self.assertEqual("PNG", values["文件格式"])
        self.assertEqual("2 x 2 像素", values["图片尺寸"])
        self.assertRegex(
            values["文件大小"],
            r"\d+(?:\.\d+)?(?: [KMGT]B)?(?: \([\d,]+ 字节\)| 字节)",
        )
        self.assertRegex(
            values["修改时间"], r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
        )
        self.assertEqual("2026-01-01 00:00:00", values["导入时间"])
        self.assertEqual("viewer-test-hash", values["文件哈希"])

    def test_missing_file_information_remains_available(self):
        missing_path = str(Path(self._temp_dir.name) / "missing.webp")
        self.dialog.load_image(missing_path)

        table = self.dialog.tableWidgetFileInfo
        values = {
            table.item(row, 0).text(): table.item(row, 1).text()
            for row in range(table.rowCount())
        }

        self.assertEqual("missing.webp", values["文件名"])
        self.assertEqual("WEBP", values["文件格式"])
        self.assertEqual("不可用", values["图片尺寸"])
        self.assertEqual("不可用", values["文件大小"])
        self.assertEqual("不可用", values["修改时间"])

    def test_adds_existing_and_new_global_tags(self):
        with patch(
            "ui.dialog_image_viewer.QInputDialog.getItem",
            return_value=("Second", True),
        ):
            self.dialog._add_tag()

        with patch(
            "ui.dialog_image_viewer.QInputDialog.getItem",
            return_value=("New Tag", True),
        ):
            self.dialog._add_tag()

        self.assertEqual({"First", "Second", "New Tag"}, {tag.name for tag in self.sticker.tags})
        self.assertEqual(3, self.dialog._tag_model.rowCount())
        self.assertEqual(
            {"First", "Second", "New Tag"},
            {tag.name for tag in self.db.list_stickers()[0].tags},
        )

    def test_delete_only_removes_current_image_association(self):
        index = self.dialog._tag_model.index(0, 0)
        self.dialog._tag_widget._list_view.selectionModel().select(
            index,
            QItemSelectionModel.SelectionFlag.Select,
        )

        self.dialog._delete_selected_tags()

        self.assertEqual([], self.sticker.tags)
        self.assertEqual([], self.db.list_stickers()[0].tags)
        self.assertEqual(["First", "Second"], [tag.name for tag in self.db.list_tags()])

    def test_adding_disabled_tag_reenables_it_without_losing_metadata(self):
        disabled = make_tag("Disabled", "#ABCDEF", enabled=False)
        disabled.description = "Keep this description"
        self.db.add_or_modify_tag(disabled)

        with patch(
            "ui.dialog_image_viewer.QInputDialog.getItem",
            return_value=("Disabled", True),
        ):
            self.dialog._add_tag()

        stored = next(tag for tag in self.db.list_tags() if tag.name == "Disabled")
        self.assertTrue(stored.enabled)
        self.assertEqual("#ABCDEF", stored.color_rgb)
        self.assertEqual("Keep this description", stored.description)
        self.assertIn("Disabled", {tag.name for tag in self.sticker.tags})


if __name__ == "__main__":
    unittest.main()
