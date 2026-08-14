import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtGui import QImage, QMovie
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

import apppath
from commons.dto import StickerImage, Tag
from stickerdb.v1.sticker_db import StickerDBV1
from ui.dialog_image_viewer import ImageViewerDialog, TAG_DATA_ROLE
from ui.widgets.custom_tag_widget import TAG_ACCENT_COLOR_ROLE
from ui.widgets.pan_zoom_image_view import PanZoomImageView


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


class StubTagSelectorDialog:
    """替换真实对话框：记录构造参数，并按需返回结果。"""

    def __init__(self, database=None, selected_tag_ids=(), parent=None, **kwargs):
        self.database = database
        self.selected_tag_ids = set(selected_tag_ids)
        self.parent = parent
        self.result = QDialog.DialogCode.Rejected
        self.tags_to_return: list[Tag] = []

    def exec(self):
        return self.result

    def selected_tags(self):
        return list(self.tags_to_return)


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

    def test_image_viewer_uses_pan_zoom_widget(self):
        self.assertIsInstance(self.dialog.widgetImageViewer, PanZoomImageView)
        self.assertIs(self.dialog._image_view, self.dialog.widgetImageViewer)

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
        self.assertEqual("viewer-test-hash", values["SHA1"])

    def test_file_path_row_uses_three_line_height(self):
        table = self.dialog.tableWidgetFileInfo
        path_row = next(
            row
            for row in range(table.rowCount())
            if table.item(row, 0).text() == "文件路径"
        )
        line_height = table.fontMetrics().lineSpacing()

        self.assertEqual(3 * line_height, table.rowHeight(path_row))
        for row in range(table.rowCount()):
            if row != path_row:
                self.assertEqual(
                    table.verticalHeader().defaultSectionSize(),
                    table.rowHeight(row),
                )

    def test_loads_animated_gif_with_movie(self):
        gif_path = Path(self._temp_dir.name) / "animated.gif"
        first_frame = Image.new("RGBA", (2, 2), "red")
        second_frame = Image.new("RGBA", (2, 2), "blue")
        first_frame.save(
            gif_path,
            save_all=True,
            append_images=[second_frame],
            duration=50,
            loop=0,
        )

        self.sticker.original_file_name = "动画.gif"
        self.dialog.load_image(str(gif_path), "动画.gif", self.sticker)
        for _ in range(50):
            if self.dialog._movie.currentFrameNumber() > 0:
                break
            QTest.qWait(20)

        self.assertIsNotNone(self.dialog._movie)
        self.assertEqual(
            QMovie.MovieState.Running,
            self.dialog._movie.state(),
        )
        self.assertFalse(
            self.dialog.widgetImageViewer._image_item.pixmap().isNull()
        )

        table = self.dialog.tableWidgetFileInfo
        values = {
            table.item(row, 0).text(): table.item(row, 1).text()
            for row in range(table.rowCount())
        }
        self.assertEqual("动画.gif", values["文件名"])
        self.assertEqual("GIF", values["文件格式"])
        self.assertEqual("2 x 2 像素", values["图片尺寸"])

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

    def patch_selector_dialog(self, *, accepted=False, tags=()):
        """用桩替换 TagSelectorDialog，返回工厂以获取构造出的桩实例。"""
        def factory(*args, **kwargs):
            stub = StubTagSelectorDialog(*args, **kwargs)
            stub.result = (
                QDialog.DialogCode.Accepted
                if accepted
                else QDialog.DialogCode.Rejected
            )
            stub.tags_to_return = list(tags)
            factory.stub = stub
            return stub

        patcher = patch("ui.dialog_image_viewer.TagSelectorDialog", new=factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        return factory

    def test_add_tag_opens_selector_without_preselection(self):
        factory = self.patch_selector_dialog()
        self.dialog._add_tag()

        stub = factory.stub
        self.assertIs(self.db, stub.database)
        self.assertEqual(set(), stub.selected_tag_ids)
        self.assertIs(self.dialog, stub.parent)

    def test_add_tag_saves_accepted_selection(self):
        factory = self.patch_selector_dialog(
            accepted=True,
            tags=[self.second],
        )
        self.dialog._add_tag()

        self.assertEqual(
            {"First", "Second"},
            {tag.name for tag in self.sticker.tags},
        )
        self.assertEqual(2, self.dialog._tag_model.rowCount())
        self.assertEqual(
            {"First", "Second"},
            {tag.name for tag in self.db.list_stickers()[0].tags},
        )

    def test_add_tag_cancel_keeps_tags_unchanged(self):
        factory = self.patch_selector_dialog()
        self.dialog._add_tag()

        self.assertEqual({"First"}, {tag.name for tag in self.sticker.tags})
        self.assertEqual(1, self.dialog._tag_model.rowCount())
        self.assertEqual(
            {"First"},
            {tag.name for tag in self.db.list_stickers()[0].tags},
        )

    def test_delete_only_removes_current_image_association(self):
        index = self.dialog._tag_model.index(0, 0)
        self.dialog._tag_widget._list_view.selectionModel().select(
            index,
            QItemSelectionModel.SelectionFlag.Select,
        )

        with patch(
            "ui.dialog_image_viewer.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.dialog._delete_selected_tags()

        self.assertEqual([], self.sticker.tags)
        self.assertEqual([], self.db.list_stickers()[0].tags)
        self.assertEqual(["First", "Second"], [tag.name for tag in self.db.list_tags()])

    def test_delete_confirmation_shows_tag_name(self):
        index = self.dialog._tag_model.index(0, 0)
        self.dialog._tag_widget._list_view.selectionModel().select(
            index,
            QItemSelectionModel.SelectionFlag.Select,
        )

        with patch("ui.dialog_image_viewer.QMessageBox.question") as question:
            self.dialog._delete_selected_tags()

        question.assert_called_once_with(
            self.dialog,
            "删除标签",
            '确实要取消关联标签"First"吗？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

    def test_delete_cancel_keeps_tags_unchanged(self):
        index = self.dialog._tag_model.index(0, 0)
        self.dialog._tag_widget._list_view.selectionModel().select(
            index,
            QItemSelectionModel.SelectionFlag.Select,
        )

        with patch(
            "ui.dialog_image_viewer.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            self.dialog._delete_selected_tags()

        self.assertEqual({"First"}, {tag.name for tag in self.sticker.tags})
        self.assertEqual(1, self.dialog._tag_model.rowCount())
        self.assertEqual(
            {"First"},
            {tag.name for tag in self.db.list_stickers()[0].tags},
        )

    def test_add_tag_deduplicates_tags_already_assigned(self):
        factory = self.patch_selector_dialog(
            accepted=True,
            tags=[self.first, self.second],
        )
        self.dialog._add_tag()

        self.assertEqual(
            ["First", "Second"],
            [tag.name for tag in self.sticker.tags],
        )
        self.assertEqual(2, self.dialog._tag_model.rowCount())

    def test_add_tag_appends_selected_tags_to_current_tags(self):
        factory = self.patch_selector_dialog(accepted=True, tags=[self.second])
        self.dialog._add_tag()

        self.assertEqual(
            {"First", "Second"},
            {tag.name for tag in self.sticker.tags},
        )
        self.assertEqual(2, self.dialog._tag_model.rowCount())
        self.assertEqual(
            {"First", "Second"},
            {tag.name for tag in self.db.list_stickers()[0].tags},
        )

    def test_add_tag_without_sticker_does_not_open_dialog(self):
        self.dialog.load_image(str(Path(self._temp_dir.name) / "missing.webp"))
        dialog_cls = MagicMock()
        with patch("ui.dialog_image_viewer.TagSelectorDialog", dialog_cls):
            self.dialog._add_tag()
        dialog_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
